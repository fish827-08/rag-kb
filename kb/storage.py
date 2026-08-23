"""存储层：VectorStore 抽象接口 + ChromaDB 实现（余弦空间、按 source 级联删除、过滤分页）。"""
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from kb.models import Record


class VectorStore(ABC):
    """预留 P2 可替换接口；本期只有 ChromaStore 一个实现。"""

    @abstractmethod
    def add(self, records: list[Record], embeddings: list[list[float]]) -> None: ...
    @abstractmethod
    def get(self, record_id: str) -> Record | None: ...
    @abstractmethod
    def delete(self, ids: list[str]) -> None: ...
    @abstractmethod
    def delete_by_source(self, source: str) -> int:
        """删除该 source 的全部记录，返回删除数量。"""
    @abstractmethod
    def iter_all(self) -> Iterator[Record]:
        """全量遍历（BM25 启动重建用）。"""
    @abstractmethod
    def list_records(self, type: str | None = None, source: str | None = None,
                     tag: str | None = None, q: str | None = None,
                     limit: int = 100, offset: int = 0) -> tuple[list[Record], int]:
        """过滤+分页，返回 (记录列表, 总数)。"""
    @abstractmethod
    def query(self, embedding: list[float], top_k: int = 5,
              where: dict | None = None) -> list[tuple[Record, float]]:
        """向量检索；score = 1 - 余弦距离 ∈ [-1, 1]，降序返回。"""


def _clean_metadata(record: Record) -> dict:
    """Chroma metadata 不支持 None 值，写入前丢弃为 None 的字段（如 source）。"""
    return {k: v for k, v in record.to_metadata().items() if v is not None}


class ChromaStore(VectorStore):
    """基于 ChromaDB PersistentClient 的实现，余弦距离空间。"""

    def __init__(self, persist_dir: Path, collection_name: str = "kb_records"):
        """PersistentClient + get_or_create_collection(metadata={"hnsw:space": "cosine"})。"""
        import chromadb
        self._persist_dir = persist_dir
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._col = self._client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )

    def add(self, records: list[Record], embeddings: list[list[float]]) -> None:
        """批量写入：document=content，metadata 过滤 None 值。"""
        self._col.add(
            ids=[r.id for r in records],
            documents=[r.content for r in records],
            embeddings=embeddings,
            metadatas=[_clean_metadata(r) for r in records],
        )

    def get(self, record_id: str) -> Record | None:
        """按 id 读取单条；不存在返回 None。"""
        res = self._col.get(ids=[record_id], include=["documents", "metadatas"])
        if not res["ids"]:
            return None
        return Record.from_chroma(
            res["ids"][0], res["documents"][0], res["metadatas"][0] or {}
        )

    def delete(self, ids: list[str]) -> None:
        """按 id 列表删除。"""
        self._col.delete(ids=ids)

    def delete_by_source(self, source: str) -> int:
        """删除该 source 的全部记录，返回删除数量。"""
        res = self._col.get(where={"source": source}, include=[])
        ids = res["ids"]
        if ids:
            self._col.delete(ids=ids)
        return len(ids)

    def iter_all(self) -> Iterator[Record]:
        """全量遍历。"""
        res = self._col.get(include=["documents", "metadatas"])
        for i, rid in enumerate(res["ids"]):
            yield Record.from_chroma(rid, res["documents"][i], res["metadatas"][i] or {})

    def list_records(self, type: str | None = None, source: str | None = None,
                     tag: str | None = None, q: str | None = None,
                     limit: int = 100, offset: int = 0) -> tuple[list[Record], int]:
        """过滤+分页；type/source 走 where，q 走 where_document，tag 在 Python 侧过滤。"""
        where: dict = {}
        if type:
            where["type"] = type
        if source:
            where["source"] = source
        where_document = {"$contains": q} if q else None
        res = self._col.get(
            where=where or None,
            where_document=where_document,
            include=["documents", "metadatas"],
        )
        records = [
            Record.from_chroma(rid, res["documents"][i], res["metadatas"][i] or {})
            for i, rid in enumerate(res["ids"])
        ]
        if tag:
            records = [r for r in records if tag in r.tags]
        total = len(records)
        return records[offset:offset + limit], total

    def query(self, embedding: list[float], top_k: int = 5,
              where: dict | None = None) -> list[tuple[Record, float]]:
        """向量检索；score = 1 - 余弦距离，降序返回。"""
        res = self._col.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[tuple[Record, float]] = []
        for i, rid in enumerate(res["ids"][0]):
            rec = Record.from_chroma(rid, res["documents"][0][i], res["metadatas"][0][i] or {})
            hits.append((rec, 1 - res["distances"][0][i]))
        return hits