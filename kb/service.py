"""KBService：统一业务编排，组装 store / embedder / bm25 / retriever / llm。"""
import math
from collections import OrderedDict
from pathlib import Path

from kb.bm25 import BM25Index
from kb.config import Settings, get_settings
from kb.embedder import Embedder
from kb.ingest import (UnsupportedFormatError, WebFetchError, chunk_text,
                       fetch_webpage, parse_file)
from kb.llm import LLMClient, LLMStatus
from kb.models import Record, RecordType
from kb.retriever import HybridRetriever
from kb.storage import ChromaStore

__all__ = ["KBService", "LLMDisabledError", "UnsupportedFormatError",
           "WebFetchError"]

# RAG 护栏系统提示：强约束仅依据参考文档作答，禁止编造
RAG_SYSTEM_PROMPT = "仅依据参考文档回答，无相关信息则明确说明，禁止编造。"

# 复杂度分类提示（本地 LLM，max_tokens=5）：仅输出 SIMPLE / COMPLEX
CLASSIFY_PROMPT = "判断问题类型，仅输出 SIMPLE 或 COMPLEX。问题：{q}"

# 云端前置压缩提示（本地 LLM）：保留全部关键事实的要点化压缩
COMPRESS_PROMPT = "将以下检索内容压缩为要点，保留全部关键事实，500字内输出：\n{context}"


class LLMDisabledError(Exception):
    """LLM 不可用（本地与云端均未就绪）；API 层据此转 503 并附配置指引。"""


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；零向量返回 0（embedder 输出已归一化，此处仍稳妥求模）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _parse_label(resp: str) -> str:
    """解析分类输出：取先出现的 SIMPLE/COMPLEX 判定（大小写不敏感）；失败按 SIMPLE。"""
    up = (resp or "").upper()
    i_simple = up.find("SIMPLE")
    i_complex = up.find("COMPLEX")
    if i_complex >= 0 and (i_simple < 0 or i_complex < i_simple):
        return "COMPLEX"
    return "SIMPLE"


class KBService:
    """记忆服务核心；REST / MCP / CLI 共用。"""

    def __init__(self, settings: Settings | None = None, llm=None):
        """组装各组件；BM25 启动优先加载持久化语料（N27），漂移/缺失才全量分词重建。
        llm 未注入时自建 LLMClient(settings)；注入时直接用（测试替身）。"""
        self.settings = settings or get_settings()
        self.device = self.settings.device or "cpu"
        self.embedder = Embedder(self.settings.embed_model, device=self.device)
        self.store = ChromaStore(self.settings.chroma_dir)
        self.bm25 = BM25Index()
        self._bm25_cache = self.settings.data_dir / "bm25_corpus.json"
        all_records = list(self.store.iter_all())
        if not self.bm25.load_corpus(self._bm25_cache, [r.id for r in all_records]):
            self.bm25.rebuild(all_records)
            self._persist_bm25()
        self.retriever = HybridRetriever(self.store, self.bm25, self.embedder, settings=self.settings)
        self.llm = llm or LLMClient(self.settings)
        # 云端客户端注入点：None=无独立云端客户端（真实云端由 self.llm 统一承担）
        self._cloud_client = None
        # /ask 答案缓存（LRU）：key=问题原文，条目含问题向量/答案/来源/后端
        self._cache: OrderedDict[str, dict] = OrderedDict()

    def _persist_bm25(self) -> None:
        """BM25 语料落盘；失败记 WARNING 不阻塞主流程（N27）。"""
        import logging
        try:
            self.bm25.save_corpus(self._bm25_cache)
        except OSError as e:
            logging.getLogger("kb.service").warning("BM25 语料落盘失败: %s", e)

    # ---- 记忆 CRUD ----
    def add_memory(self, content: str, tags: list[str] | None = None,
                   source: str | None = None, namespace: str = "default") -> Record:
        """写入一条记忆短文本并嵌入。

        N22a/TASK-0069：dedup_enabled 时先做语义去重检查，命中则抛 DuplicateError
        （api 层捕获返回 409）；关闭时零行为变化。
        """
        from kb.governance import DuplicateError, check_duplicate
        if self.settings.dedup_enabled:
            existing_id, similarity = check_duplicate(
                content, self.store, self.embedder,
                threshold=self.settings.dedup_threshold)
            if existing_id is not None:
                # N23b/TASK-0073：去重拦截审计日志（默认开，不阻塞主流程）
                if getattr(self.settings, "audit_dedup_enabled", True):
                    from kb.audit import log_governance_event
                    log_governance_event(
                        "dedup_blocked", existing_id,
                        {"similarity": similarity, "duplicate_of": existing_id},
                        namespace=namespace)
                raise DuplicateError(existing_id, similarity)
        record = Record(content=content, tags=tags or [], source=source,
                        namespace=namespace)
        vec = self.embedder.embed_texts([content])[0]
        self.store.add([record], [vec])
        self.bm25.add(record)
        self._persist_bm25()
        return record

    def get_memory(self, record_id: str) -> Record | None:
        """读取单条记忆。"""
        return self.store.get(record_id)

    def list_memories(self, **filters) -> tuple[list[Record], int]:
        """列表（过滤 + 分页），返回 (记录, 总数)。"""
        return self.store.list_records(**filters)

    def list_records(self, **filters) -> tuple[list[Record], int]:
        """记录列表（过滤 + 分页），直接委托 store；watcher 等内部与验收测试使用。"""
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
        self._persist_bm25()
        return record

    def delete_memory(self, record_id: str) -> bool:
        """删除记忆；不存在返回 False。"""
        record = self.store.get(record_id)
        if record is None:
            return False
        self.store.delete([record_id])
        self.bm25.remove(record_id)
        self._persist_bm25()
        return True

    # ---- 文档摄取与管理 ----
    def add_document(self, path: str | Path,
                     source: str | None = None) -> dict:
        """解析本地文档 → 切分 → 每 chunk 一条 doc_chunk Record 入库。

        返回 {"source": 文件名, "chunks": 入库块数}；
        source 缺省取文件名，multipart 上传时以上传文件名入库（覆盖临时文件名）。
        空文档切不出 chunk，不入库直接返回 chunks=0。
        """
        path = Path(path)
        text = parse_file(path)
        chunks = chunk_text(text, self.settings.chunk_size,
                            self.settings.chunk_overlap)
        doc_source = source or path.name
        if not chunks:
            return {"source": doc_source, "chunks": 0}
        records = [Record(content=c, type=RecordType.DOC_CHUNK,
                          source=doc_source) for c in chunks]
        vecs = self.embedder.embed_texts([r.content for r in records])
        self.store.add(records, vecs)
        for r in records:
            self.bm25.add(r)
        self._persist_bm25()
        return {"source": doc_source, "chunks": len(records)}

    def add_webpage(self, url: str) -> dict:
        """抓取网页正文 → 切分 → 每 chunk 一条 web_chunk Record 入库。

        返回 {"source": url, "chunks": 入库块数}；抓取/正文提取失败抛
        WebFetchError（API 层转 400 + 原因）。正文切不出 chunk 时不入库，
        直接返回 chunks=0。
        """
        text = fetch_webpage(url)
        chunks = chunk_text(text, self.settings.chunk_size,
                            self.settings.chunk_overlap)
        if not chunks:
            return {"source": url, "chunks": 0}
        records = [Record(content=c, type=RecordType.WEB_CHUNK,
                          source=url) for c in chunks]
        vecs = self.embedder.embed_texts([r.content for r in records])
        self.store.add(records, vecs)
        for r in records:
            self.bm25.add(r)
        self._persist_bm25()
        return {"source": url, "chunks": len(records)}

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
        self._persist_bm25()
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

    # ---- 问答（基础 RAG + 智能路由）----
    def ask(self, question: str) -> dict:
        """RAG 问答：检索 → 上下文拼装（字符预算截断）→ 护栏 prompt → 生成 → 附 sources。

        - 检索 top_k=5，结果按 score 降序拼入上下文；
        - 字符预算 = context_token_limit * 2（粗略 token→字符 2:1 估算）；
        - llm_mode=local/cloud：走 N10 原路径（后端由 llm.chat 内部按 status/mode 决定）；
        - llm_mode=auto：智能路由（缓存 → 本地分类 → 敏感覆盖 → 分支路由），见 _ask_auto；
        - LLM 禁用时抛 LLMDisabledError（API 层转 503 + 配置指引）。
        """
        if self.llm.status is LLMStatus.DISABLED:
            raise LLMDisabledError("LLM 不可用：本地 Ollama 未响应且未配置云端 Key")
        results = sorted(self.search(question, top_k=5),
                         key=lambda r: r["score"], reverse=True)
        sources = [{"id": r["id"], "content": r["content"],
                    "score": r["score"], "source": r["source"]} for r in results]
        if self.settings.llm_mode == "auto":
            return self._ask_auto(question, results, sources)
        context = self._build_context(results)
        answer = self.llm.chat(self._build_messages(context, question))
        return {"answer": answer, "sources": sources,
                "llm": self.llm.status.value}

    # ---- 智能路由（auto 模式）----
    def _ask_auto(self, question: str, results: list[dict],
                  sources: list[dict]) -> dict:
        """auto 模式智能路由：缓存检查 → 本地分类 → 敏感覆盖 → 分支路由 → 写缓存。

        分支：SENSITIVE / SIMPLE / COMPLEX 无云 → 本地直答（N10 消息拼装）；
        COMPLEX 有云 → 本地压缩上下文 → 云端生成（失败降级本地直答）。
        缓存优先于分类：命中（问题向量与缓存条目余弦最大值 ≥ 阈值）直接返回，不调 LLM。
        """
        # 1) 缓存检查（问题向量与缓存条目 question_vec 余弦最大值 ≥ 阈值 → 直接返回）
        qvec = self.embedder.embed_texts([question])[0]
        hit = self._cache_lookup(qvec)
        if hit is not None:
            return {"answer": hit["answer"], "sources": hit["sources"],
                    "llm": hit["llm"]}
        # 2) 本地 LLM 复杂度分类（prefer="local"，max_tokens=5）
        try:
            label = self._classify(question)
        except Exception:
            # 本地不可用（如 auto 无本地仅剩云端）时无法分类，退回 N10 原路径
            # （prefer 默认 auto，由 llm 内部本地优先、云端兜底）
            context = self._build_context(results)
            answer = self.llm.chat(self._build_messages(context, question))
            llm_used = self.llm.status.value
            self._cache_put(question, qvec, answer, sources, llm_used)
            return {"answer": answer, "sources": sources, "llm": llm_used}
        # 3) 敏感覆盖：检索结果 tag 含 sensitive 或 namespace 命中敏感名单 → SENSITIVE
        if self._is_sensitive(results):
            label = "SENSITIVE"
        # 4) 路由分支
        context = self._build_context(results)
        if label == "COMPLEX" and self._cloud_available():
            answer, llm_used = self._answer_complex_cloud(question, context)
        else:
            answer = self.llm.chat(self._build_messages(context, question),
                                   prefer="local")
            llm_used = "local"
        # 5) 未命中缓存走完整流程后写入缓存
        self._cache_put(question, qvec, answer, sources, llm_used)
        return {"answer": answer, "sources": sources, "llm": llm_used}

    def _classify(self, question: str) -> str:
        """本地 LLM 复杂度分类；输出解析失败按 SIMPLE 处理。"""
        messages = [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
            {"role": "user", "content": CLASSIFY_PROMPT.format(q=question)},
        ]
        resp = self.llm.chat(messages, prefer="local", max_tokens=5)
        return _parse_label(resp)

    def _is_sensitive(self, results: list[dict]) -> bool:
        """敏感判定：检索结果 tag 含 'sensitive'，或记录 namespace 命中敏感名单。

        检索结果 dict 不含 namespace 字段，敏感名单非空时按 id 回查 store 取 namespace。
        """
        sensitive_ns = self.settings.sensitive_ns_list
        for r in results:
            if "sensitive" in (r.get("tags") or []):
                return True
            if sensitive_ns:
                rec = self.store.get(r["id"])
                if rec is not None and rec.namespace in sensitive_ns:
                    return True
        return False

    def _cloud_available(self) -> bool:
        """auto 模式下云端是否可用：注入的云端客户端优先，其次 DeepSeek Key 非空。"""
        if self._cloud_client is not None:
            return True
        return bool(self.settings.deepseek_api_key)

    def _answer_complex_cloud(self, question: str,
                              context: str) -> tuple[str, str]:
        """COMPLEX 有云路径：本地压缩上下文 → 云端生成；云端失败降级本地直答。

        云端生成优先用注入的 _cloud_client（若非 None），否则 self.llm.chat(prefer="cloud")。
        返回 (answer, llm_used)。
        """
        compress_messages = [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
            {"role": "user",
             "content": COMPRESS_PROMPT.format(context=context)},
        ]
        compressed = self.llm.chat(compress_messages, prefer="local")
        cloud_messages = self._build_messages(compressed, question)
        try:
            if self._cloud_client is not None:
                answer = self._cloud_client.chat(cloud_messages)
            else:
                answer = self.llm.chat(cloud_messages, prefer="cloud")
            return answer, "cloud"
        except Exception:
            # 云端失败（抛异常）→ 降级本地直答（原始上下文，N10 消息拼装）
            answer = self.llm.chat(self._build_messages(context, question),
                                   prefer="local")
            return answer, "local"

    def _cache_lookup(self, qvec: list[float]) -> dict | None:
        """缓存查找：与各条目问题向量余弦最大值 ≥ cache_sim_threshold 即命中；
        命中提升 LRU 新鲜度并返回该条目。"""
        best_key: str | None = None
        best_sim = -1.0
        for key, entry in self._cache.items():
            sim = _cosine(qvec, entry["question_vec"])
            if sim > best_sim:
                best_key, best_sim = key, sim
        if best_key is not None and best_sim >= self.settings.cache_sim_threshold:
            self._cache.move_to_end(best_key)
            return self._cache[best_key]
        return None

    def _cache_put(self, question: str, qvec: list[float], answer: str,
                   sources: list[dict], llm_used: str) -> None:
        """写入缓存条目（问题向量/答案/来源/后端）；超容量淘汰最旧（LRU）。"""
        self._cache[question] = {"question_vec": qvec, "answer": answer,
                                 "sources": sources, "llm": llm_used}
        self._cache.move_to_end(question)
        while len(self._cache) > self.settings.cache_size:
            self._cache.popitem(last=False)

    # ---- 上下文与消息拼装（N10 逻辑，路由路径复用）----
    def _build_context(self, results: list[dict]) -> str:
        """逐条累加 content，超出字符预算即截断停止（最后一条截到剩余预算）。"""
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
        return "\n".join(parts)

    @staticmethod
    def _build_messages(context: str, question: str) -> list[dict]:
        """护栏 prompt：system 强约束 + user 参考文档与问题。"""
        return [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
            {"role": "user",
             "content": f"参考文档：\n{context}\n\n问题：{question}"},
        ]