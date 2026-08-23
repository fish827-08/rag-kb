"""KBService：统一业务编排，组装 store / embedder / bm25 / retriever。"""
from kb.bm25 import BM25Index
from kb.config import Settings, get_settings
from kb.embedder import Embedder
from kb.models import Record
from kb.retriever import HybridRetriever
from kb.storage import ChromaStore


class KBService:
    """记忆服务核心；REST / MCP / CLI 共用。"""

    def __init__(self, settings: Settings | None = None):
        """组装各组件；启动时从 store.iter_all() 全量重建 BM25。"""
        self.settings = settings or get_settings()
        self.device = self.settings.device or "cpu"
        self.embedder = Embedder(self.settings.embed_model, device=self.device)
        self.store = ChromaStore(self.settings.chroma_dir)
        self.bm25 = BM25Index()
        self.bm25.rebuild(self.store.iter_all())
        self.retriever = HybridRetriever(self.store, self.bm25, self.embedder)

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

    # ---- 检索与统计 ----
    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               type: str | None = None, tag: str | None = None) -> list[dict]:
        """混合检索。"""
        return self.retriever.search(query, top_k=top_k, mode=mode,
                                     type=type, tag=tag)

    def stats(self) -> dict:
        """运行统计；llm 在 N9 前恒为 "disabled"。"""
        _, total = self.store.list_records()
        return {"records": total, "device": self.device, "llm": "disabled"}