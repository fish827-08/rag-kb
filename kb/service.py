"""KBService：统一业务编排，组装 store / embedder / bm25 / retriever / llm。"""
from kb.bm25 import BM25Index
from kb.config import Settings, get_settings
from kb.embedder import Embedder
from kb.llm import LLMClient, LLMStatus
from kb.models import Record
from kb.retriever import HybridRetriever
from kb.storage import ChromaStore

# RAG 护栏系统提示：强约束仅依据参考文档作答，禁止编造
RAG_SYSTEM_PROMPT = "仅依据参考文档回答，无相关信息则明确说明，禁止编造。"


class LLMDisabledError(Exception):
    """LLM 不可用（本地与云端均未就绪）；API 层据此转 503 并附配置指引。"""


class KBService:
    """记忆服务核心；REST / MCP / CLI 共用。"""

    def __init__(self, settings: Settings | None = None, llm=None):
        """组装各组件；启动时从 store.iter_all() 全量重建 BM25。
        llm 未注入时自建 LLMClient(settings)；注入时直接用（测试替身）。"""
        self.settings = settings or get_settings()
        self.device = self.settings.device or "cpu"
        self.embedder = Embedder(self.settings.embed_model, device=self.device)
        self.store = ChromaStore(self.settings.chroma_dir)
        self.bm25 = BM25Index()
        self.bm25.rebuild(self.store.iter_all())
        self.retriever = HybridRetriever(self.store, self.bm25, self.embedder)
        self.llm = llm or LLMClient(self.settings)

    # ---- 记忆 CRUD ----
    def add_memory(self, content: str, tags: list[str] | None = None,
                   source: str | None = None, namespace: str = "default") -> Record:
        """写入一条记忆短文本并嵌入。"""
        record = Record(content=content, tags=tags or [], source=source,
                        namespace=namespace)
        vec = self.embedder.embed_texts([content])[0]
        self.store.add([record], [vec])
        self.bm25.add(record)
        return record

    def get_memory(self, record_id: str) -> Record | None:
        """读取单条记忆。"""
        return self.store.get(record_id)

    def list_memories(self, **filters) -> tuple[list[Record], int]:
        """列表（过滤 + 分页），返回 (记录, 总数)。"""
        return self.store.list_records(**filters)

    def update_memory(self, record_id: str, content: str | None = None,
                      tags: list[str] | None = None) -> Record | None:
        """更新记忆；content 变更时重新嵌入并更新 updated_at。"""
        from datetime import datetime
        record = self.store.get(record_id)
        if record is None:
            return None
        if content is not None:
            record.content = content
        if tags is not None:
            record.tags = tags
        record.updated_at = datetime.now().isoformat()
        # 先删旧向量，再按同 id 重新嵌入写入（保持主键不变）
        self.store.delete([record_id])
        vec = self.embedder.embed_texts([record.content])[0]
        self.store.add([record], [vec])
        self.bm25.remove(record_id)
        self.bm25.add(record)
        return record

    def delete_memory(self, record_id: str) -> bool:
        """删除记忆；不存在返回 False。"""
        record = self.store.get(record_id)
        if record is None:
            return False
        self.store.delete([record_id])
        self.bm25.remove(record_id)
        return True

    # ---- 文档管理 ----
    def list_documents(self) -> list[dict]:
        """按 source 聚合文档列表（source 非空的所有记录，不限 type）。
        chunks=该 source 记录数；chars=content 总字符数；last_imported=最大 created_at。"""
        docs: dict[str, dict] = {}
        for r in self.store.iter_all():
            if not r.source:
                continue
            d = docs.setdefault(r.source, {
                "source": r.source, "chunks": 0, "chars": 0, "last_imported": ""})
            d["chunks"] += 1
            d["chars"] += len(r.content)
            if r.created_at > d["last_imported"]:
                d["last_imported"] = r.created_at
        return sorted(docs.values(), key=lambda d: d["source"])

    def delete_document(self, source: str) -> int:
        """按 source 删除文档全部记录，返回删除数量；同步清理 BM25 索引。"""
        ids = [r.id for r in self.store.iter_all() if r.source == source]
        n = self.store.delete_by_source(source)
        for rid in ids:
            self.bm25.remove(rid)
        return n

    # ---- 检索与统计 ----
    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               type: str | None = None, tag: str | None = None) -> list[dict]:
        """混合检索。"""
        return self.retriever.search(query, top_k=top_k, mode=mode,
                                     type=type, tag=tag)

    def stats(self) -> dict:
        """运行统计；llm 为当前 LLM 状态（local/cloud/disabled）。"""
        _, total = self.store.list_records()
        return {"records": total, "device": self.device,
                "llm": self.llm.status.value}

    # ---- 问答（基础 RAG）----
    def ask(self, question: str) -> dict:
        """基础 RAG 问答：检索 → 上下文拼装（字符预算截断）→ 护栏 prompt → 生成 → 附 sources。

        - 检索 top_k=5，结果按 score 降序拼入上下文；
        - 字符预算 = context_token_limit * 2（粗略 token→字符 2:1 估算），
          逐条累加，预算耗尽即停止（最后一条截断到剩余预算）；
        - LLM 禁用时抛 LLMDisabledError（API 层转 503 + 配置指引）。
        """
        if self.llm.status is LLMStatus.DISABLED:
            raise LLMDisabledError("LLM 不可用：本地 Ollama 未响应且未配置云端 Key")
        results = sorted(self.search(question, top_k=5),
                         key=lambda r: r["score"], reverse=True)
        # 逐条累加 content，超出字符预算即截断停止
        budget = self.settings.context_token_limit * 2
        parts: list[str] = []
        used = 0
        for r in results:
            content = r["content"]
            if used + len(content) > budget:
                remaining = budget - used
                if remaining > 0:
                    parts.append(content[:remaining])
                break
            parts.append(content)
            used += len(content)
        context = "\n".join(parts)
        messages = [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
            {"role": "user",
             "content": f"参考文档：\n{context}\n\n问题：{question}"},
        ]
        answer = self.llm.chat(messages)
        sources = [{"id": r["id"], "content": r["content"],
                    "score": r["score"], "source": r["source"]} for r in results]
        return {"answer": answer, "sources": sources,
                "llm": self.llm.status.value}