"""存储层：VectorStore 抽象接口 + ChromaDB 实现（余弦空间、按 source 级联删除、过滤分页）。
English: Storage layer: VectorStore abstract interface plus a ChromaDB implementation
(cosine space, cascade delete by source, filtered pagination)."""
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from kb.models import Record

ITER_BATCH = 500   # iter_all 分页批大小：避免单次全量 get 把整库结果集压进内存
# Chroma 单次 add/delete 有批量上限（实测 5461，与向量维度相关）；取 2000 留余量。
WRITE_BATCH = 2000


def _batched(items: list, size: int = WRITE_BATCH):
    """把列表切成固定大小的批次。"""
    for start in range(0, len(items), size):
        yield items[start:start + size]


class VectorStore(ABC):
    """预留 P2 可替换接口；本期只有 ChromaStore 一个实现。
    English: Pluggable interface reserved for P2; only ChromaStore is implemented this milestone."""

    @abstractmethod
    def add(self, records: list[Record], embeddings: list[list[float]]) -> None: ...
    @abstractmethod
    def get(self, record_id: str) -> Record | None: ...
    @abstractmethod
    def get_many(self, record_ids: list[str]) -> dict[str, Record]:
        """批量读取（单次调用）；不存在的不在返回 dict 中（N27）。
        English: Batch read (single call); missing ids are absent from the returned dict (N27)."""

    @abstractmethod
    def delete(self, ids: list[str]) -> None: ...
    @abstractmethod
    def delete_by_source(self, source: str) -> int:
        """删除该 source 的全部记录，返回删除数量。
        English: Delete all records of this source, returning the count deleted."""
    @abstractmethod
    def iter_all(self) -> Iterator[Record]:
        """全量遍历（BM25 启动重建用）。
        English: Iterate all records (used for BM25 startup rebuild)."""
    @abstractmethod
    def list_records(self, type: str | None = None, source: str | None = None,
                     tag: str | None = None, q: str | None = None,
                     limit: int = 100, offset: int = 0) -> tuple[list[Record], int]:
        """过滤+分页，返回 (记录列表, 总数)。
        English: Filter + pagination, returning (records, total)."""
    @abstractmethod
    def query(self, embedding: list[float], top_k: int = 5,
              where: dict | None = None) -> list[tuple[Record, float]]:
        """向量检索；score = 1 - 余弦距离 ∈ [-1, 1]，降序返回。
        English: Vector retrieval; score = 1 - cosine distance ∈ [-1, 1], returned descending."""


def _clean_metadata(record: Record) -> dict:
    """Chroma metadata 不支持 None 与空串，写入前丢弃；access_count=0 是有效值须保留。

    N21a/TASK-0067：过滤规则从仅 None 扩展为 None + 空串 ""；
    0（int）、0.0（float）等假值但非空串/None 的值保留（access_count=0 须入库）。
    English: Chroma metadata rejects None and empty strings, so drop them before writing; access_count=0 is a
    valid value and must be kept. N21a/TASK-0067: the filter rule is widened from None-only to None + empty string "";
    falsy-but-non-""/None values like 0 (int) and 0.0 (float) are kept (access_count=0 must be stored)."""
    return {k: v for k, v in record.to_metadata().items()
            if v is not None and v != ""}


class ChromaStore(VectorStore):
    """基于 ChromaDB PersistentClient 的实现，余弦距离空间。
    English: ChromaDB PersistentClient-based implementation with a cosine distance space."""

    def __init__(self, persist_dir: Path, collection_name: str = "kb_records"):
        """PersistentClient + get_or_create_collection(metadata={"hnsw:space": "cosine"})。
        English: PersistentClient plus get_or_create_collection(metadata={"hnsw:space": "cosine"})."""
        import chromadb
        self._persist_dir = persist_dir
        self._collection_name = collection_name
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._col = self._client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )

    def add(self, records: list[Record], embeddings: list[list[float]]) -> None:
        """分批批量写入：Chroma 单次 add 有上限（约 5461），2 万行必须切开。

        English: Batched write. Chroma caps a single add (around 5461 records), so
        large imports must be split into chunks.
        """
        for start in range(0, len(records), WRITE_BATCH):
            chunk = records[start:start + WRITE_BATCH]
            vecs = embeddings[start:start + WRITE_BATCH]
            self._col.add(
                ids=[r.id for r in chunk],
                documents=[r.content for r in chunk],
                embeddings=vecs,
                metadatas=[_clean_metadata(r) for r in chunk],
            )

    def get(self, record_id: str) -> Record | None:
        """按 id 读取单条；不存在返回 None。
        English: Read a single record by id; return None if absent."""
        res = self._col.get(ids=[record_id], include=["documents", "metadatas"])
        if not res["ids"]:
            return None
        return Record.from_chroma(
            res["ids"][0], res["documents"][0], res["metadatas"][0] or {}
        )

    def get_many(self, record_ids: list[str]) -> dict[str, Record]:
        """批量读取（单次 Chroma get，N27 消除检索路径 N+1）。

        不存在的 id 不在返回 dict 中；空列表直接返回空 dict。
        English: Batch read (single Chroma get, N27 removes the N+1 in retrieval paths).
        Missing ids are absent from the returned dict; an empty list directly returns an empty dict."""
        if not record_ids:
            return {}
        res = self._col.get(ids=record_ids, include=["documents", "metadatas"])
        return {
            rid: Record.from_chroma(rid, doc, meta or {})
            for rid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"])
        }

    def delete(self, ids: list[str]) -> None:
        """按 id 列表删除（分批，避免超过 Chroma 单次上限）。"""
        for chunk in _batched(ids):
            self._col.delete(ids=chunk)

    def upsert(self, records: list[Record], embeddings: list[list[float]]) -> None:
        """按 id 写入或覆盖（迁移到新模型时复用原 id）。"""
        for start in range(0, len(records), WRITE_BATCH):
            chunk = records[start:start + WRITE_BATCH]
            vecs = embeddings[start:start + WRITE_BATCH]
            self._col.upsert(
                ids=[r.id for r in chunk],
                documents=[r.content for r in chunk],
                embeddings=vecs,
                metadatas=[_clean_metadata(r) for r in chunk],
            )

    def list_ids(self) -> list[str]:
        """返回全部记录 id（迁移时做镜像差集，不读正文）。"""
        res = self._col.get(include=[])
        return list(res["ids"])

    def delete_collection(self) -> None:
        """删除整个 collection（仅用于删除某个非活跃模型的数据）。

        失败向上抛：调用方据此保留模型映射，避免出现"映射已删、数据还在"的孤儿。
        English: Delete the whole collection (only for removing a non-active model's
        data). Failures propagate so the caller keeps the model mapping instead of
        leaving orphaned data behind.
        """
        self._client.delete_collection(self._collection_name)

    def count(self) -> int:
        """记录总数（Chroma 原生计数，不加载文档）。
        English: Total record count (native Chroma count, no documents loaded)."""
        return self._col.count()

    def delete_by_source(self, source: str) -> int:
        """删除该 source 的全部记录，返回删除数量。
        English: Delete all records of this source, returning the count deleted."""
        res = self._col.get(where={"source": source}, include=[])
        ids = res["ids"]
        if ids:
            for chunk in _batched(ids):
                self._col.delete(ids=chunk)
        return len(ids)

    def iter_all(self) -> Iterator[Record]:
        """分页遍历全部记录（BM25 重建等），每批 ITER_BATCH 条。

        单次无 limit 的 get 会把整库结果集一次性物化，大库（数万条以上）容易拉高
        峰值内存；改成分页拉取，返回顺序与字段保持不变。
        English: Iterate all records in batches of ITER_BATCH (used for BM25 rebuild).
        A single unbounded get materialises the whole collection and spikes memory on large
        libraries; paged reads keep the same order and fields."""
        offset = 0
        while True:
            res = self._col.get(include=["documents", "metadatas"],
                                limit=ITER_BATCH, offset=offset)
            ids = res["ids"]
            for i, rid in enumerate(ids):
                yield Record.from_chroma(rid, res["documents"][i], res["metadatas"][i] or {})
            if len(ids) < ITER_BATCH:
                break
            offset += len(ids)

    def list_records(self, type: str | None = None, source: str | None = None,
                     tag: str | None = None, q: str | None = None,
                     limit: int = 100, offset: int = 0) -> tuple[list[Record], int]:
        """过滤+分页；type/source 走 where，q 走 where_document，tag 在 Python 侧过滤。
        English: Filter + pagination; type/source use where, q uses where_document, and tag is filtered on the Python side."""
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
        """向量检索；score = 1 - 余弦距离，降序返回。
        English: Vector retrieval; score = 1 - cosine distance, returned descending."""
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

    def increment_access(self, record_ids: list[str]) -> None:
        """命中记录 access_count+1 与 last_accessed=now（N21a/TASK-0067，异步调用）。

        批量实现（改进项 4）：get_many 单次读取 + update 单次批量写，N 条命中
        从 2N 次 Chroma 调用降为 2 次；仅改 access_count/last_accessed，其余字段保留；
        失败记 WARNING 不抛异常（检索路径异步调用，不阻塞返回）。
        English: Increment access_count and set last_accessed=now for hit records (N21a/TASK-0067, async call).
        Batch implementation (improvement #4): a single get_many read plus a single batch update
        reduce N hits from 2N Chroma calls to 2; only access_count/last_accessed are changed, other
        fields kept; on failure logs a WARNING without raising (async call on the retrieval path,
        does not block the return)."""
        import logging
        from datetime import datetime
        logger = logging.getLogger("kb.storage")
        try:
            now = datetime.now().isoformat()
            recs = self.get_many(record_ids)
            ids: list[str] = []
            metadatas: list[dict] = []
            for rid in record_ids:
                rec = recs.get(rid)
                if rec is None:
                    continue
                ids.append(rid)
                metadatas.append({"access_count": rec.access_count + 1,
                                  "last_accessed": now})
            if ids:
                self._col.update(ids=ids, metadatas=metadatas)
        except Exception as e:
            logger.warning("异步更新访问计数失败: %s", e)