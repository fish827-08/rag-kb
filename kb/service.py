"""KBService：统一业务编排，组装 store / embedder / bm25 / retriever / llm。
English: Unified business orchestration that assembles store / embedder / bm25 / retriever / llm."""
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
from kb.sparse import SparseEmbedder as _SparseEmbedderClass
from kb.sparse import SparseIndex
from kb.storage import ChromaStore

__all__ = ["KBService", "LLMDisabledError", "UnsupportedFormatError",
           "WebFetchError"]

# RAG 护栏系统提示：强约束仅依据参考文档作答，禁止编造
RAG_SYSTEM_PROMPT = "仅依据参考文档回答，无相关信息则明确说明，禁止编造。"

# 复杂度分类提示（本地 LLM，max_tokens=5）：仅输出 SIMPLE / COMPLEX
CLASSIFY_PROMPT = "判断问题类型，仅输出 SIMPLE 或 COMPLEX。问题：{q}"

# 云端前置压缩提示（本地 LLM）：保留全部关键事实的要点化压缩
COMPRESS_PROMPT = "将以下检索内容压缩为要点，保留全部关键事实，500字内输出：\n{context}"

# ---- 身份字段规约（A 节点：MCP/REST 统一校验 agent_id / client / project）----
import re as _re

# agent_id / project：任务名白名单——字母数字中文、下划线、连字符；1~64 字符。
# 禁止 default/空等无意义标识；client 额外允许空格与点（如 "Claude Code"）。
_IDENT_RE = _re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}$")
_CLIENT_RE = _re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff.· ]{1,64}$")
# 显式禁止的占位标识（调用方漏传时常见的默认/占位字串）
_FORBIDDEN_IDENTS = {"", "default", "unknown", "null", "none", "undefined",
                     "agent", "agent_id", "你的任务名"}


def validate_agent_id(agent_id: str | None, *, required: bool = False) -> str | None:
    """校验 agent_id（任务名）格式；非法返回错误消息（中文），合法返回 None。

    required=True 时空值/占位符也报错（MCP 通道用）；
    required=False 时空值视为未提供（REST 向后兼容 default 语义）。
    规则：1~64 字符，仅字母数字中文、下划线、连字符；禁止 default/unknown 等占位。
    English: Validate an agent_id (task name) against the whitelist format; returns an error
    message (Chinese) when invalid, None when OK. With required=True, empty/placeholder values
    also fail (used on the MCP channel); with required=False, empty means "not provided"."""
    if agent_id is None:
        v = ""
    else:
        v = str(agent_id).strip()
    if v in _FORBIDDEN_IDENTS:
        if required:
            return ("agent_id 必填且必须是有意义的任务名"
                    "（如 TASK-0076 / worker-1），不能用 default/unknown 等占位")
        return None
    if len(v) > 64 or not _IDENT_RE.match(v):
        return ("agent_id 格式非法：仅允许字母/数字/中文/下划线/连字符，1~64 字符"
                f"（当前：{v!r}）")
    return None


def validate_client(client: str | None) -> str | None:
    """校验 client（来源客户端）格式；空值/占位视为未提供（自动识别）。
    规则：1~64 字符，字母数字中文、下划线、连字符、空格、点。
    注意：default 视为"未提供"（客户端不传时服务端自动识别，不强制）。
    English: Validate a client (source client) name; empty/placeholder means auto-detect.
    "default" is treated as "not provided" so callers that omit client keep working."""
    if not client:
        return None
    v = str(client).strip()
    if v in ("", "default", "unknown", "null", "none", "undefined"):
        return None  # 未提供/占位 → 自动识别
    if len(v) > 64 or not _CLIENT_RE.match(v):
        return ("client 格式非法：仅允许字母/数字/中文/下划线/连字符/空格/点，"
                f"1~64 字符（当前：{v!r}；不传则自动识别）")
    return None


def validate_project(project: str | None) -> str | None:
    """校验 project（项目名）格式；空值视为未提供。
    同 agent_id 白名单（字母数字中文、下划线、连字符，1~64）。
    English: Validate a project name; empty means not provided."""
    if not project:
        return None
    v = str(project).strip()
    if v in _FORBIDDEN_IDENTS or len(v) > 64 or not _IDENT_RE.match(v):
        return ("project 格式非法：仅允许字母/数字/中文/下划线/连字符，"
                f"1~64 字符（当前：{v!r}）")
    return None


class LLMDisabledError(Exception):
    """LLM 不可用（本地与云端均未就绪）；API 层据此转 503 并附配置指引。
    English: LLM unavailable (neither local nor cloud ready); the API layer maps it to 503 with setup guidance."""


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；零向量返回 0（embedder 输出已归一化，此处仍稳妥求模）。
    English: Cosine similarity; zero vectors return 0 (embedder outputs are normalized, but the modulus is still computed defensively)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _parse_label(resp: str) -> str:
    """解析分类输出：取先出现的 SIMPLE/COMPLEX 判定（大小写不敏感）；失败按 SIMPLE。
    English: Parse the classification output: pick the first SIMPLE/COMPLEX verdict (case-insensitive); default to SIMPLE on failure."""
    up = (resp or "").upper()
    i_simple = up.find("SIMPLE")
    i_complex = up.find("COMPLEX")
    if i_complex >= 0 and (i_simple < 0 or i_complex < i_simple):
        return "COMPLEX"
    return "SIMPLE"


class KBService:
    """记忆服务核心；REST / MCP / CLI 共用。
    English: Core memory service; shared by REST / MCP / CLI."""

    def __init__(self, settings: Settings | None = None, llm=None):
        """组装各组件；BM25 启动优先加载持久化语料（N27），漂移/缺失才全量分词重建。
        llm 未注入时自建 LLMClient(settings)；注入时直接用（测试替身）。
        English: Assemble components; BM25 loads the persisted corpus on startup (N27), rebuilding
        by full tokenization only on drift/missing. When llm is not injected, build an LLMClient(settings);
        when injected, use it directly (test double)."""
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
        # N25：稀疏第三路组装（sparse_enabled 且探测成功才启用，失败降级双路）
        self.sparse_embedder, self.sparse_index = self._build_sparse(all_records)
        self.retriever = HybridRetriever(self.store, self.bm25, self.embedder,
                                         settings=self.settings,
                                         reranker=self._build_reranker(),
                                         sparse_embedder=self.sparse_embedder,
                                         sparse_index=self.sparse_index)
        self.llm = llm or LLMClient(self.settings)
        # 云端客户端注入点：None=无独立云端客户端（真实云端由 self.llm 统一承担）
        self._cloud_client = None
        # /ask 答案缓存（LRU）：key=问题原文，条目含问题向量/答案/来源/后端
        self._cache: OrderedDict[str, dict] = OrderedDict()

    def _persist_bm25(self) -> None:
        """BM25 语料落盘；失败记 WARNING 不阻塞主流程（N27）。
        English: Persist the BM25 corpus; on failure log a WARNING without blocking the main flow (N27)."""
        import logging
        try:
            self.bm25.save_corpus(self._bm25_cache)
        except OSError as e:
            logging.getLogger("kb.service").warning("BM25 语料落盘失败: %s", e)

    def _build_reranker(self):
        """N24：rerank_enabled 时组装 Reranker（懒加载，实例化不加载模型）。
        English: N24: assemble a Reranker when rerank_enabled (lazy-loaded; construction loads no model)."""
        if getattr(self.settings, "rerank_enabled", False):
            from kb.reranker import Reranker
            return Reranker(self.settings.rerank_model, device=self.device)
        return None

    # ---- N25 稀疏第三路（A3.5 spec §3.2/§3.4）----

    def _build_sparse(self, all_records):
        """sparse_enabled 时组装 (SparseEmbedder, SparseIndex)；失败降级 (None, None)。

        探测失败（非 BGE-M3 族 / sparse_linear.pt 缺失 / 加载异常）记 WARNING，
        稀疏路自动关闭（检索退回双路，行为等同 sparse_enabled=false）。
        索引优先加载持久化（sparse_index.json，id 集合校验），漂移/缺失才全量 encode 重建。
        English: Assemble (SparseEmbedder, SparseIndex) when sparse_enabled; degrade to (None, None) on failure.
        On probe failure (non-BGE-M3 / missing sparse_linear.pt / load error) log a WARNING and disable
        the sparse route automatically (retrieval falls back to dual-route, same as sparse_enabled=false).
        The index prefers loading the persisted file (sparse_index.json, id-set validated); rebuild by
        full encode only on drift/missing."""
        import logging
        if not getattr(self.settings, "sparse_enabled", False):
            return None, None
        try:
            sparse_embedder = _SparseEmbedderClass(self.embedder,
                                                   self.settings.embed_model)
            sparse_embedder._ensure_loaded()  # 启动探测（含共享编码器加载）
        except Exception as e:
            logging.getLogger("kb.service").warning(
                "稀疏第三路不可用，降级双路检索: %s", e)
            return None, None
        sparse_index = SparseIndex()
        self._sparse_cache = self.settings.data_dir / "sparse_index.json"
        valid_ids = [r.id for r in all_records]
        if not sparse_index.load(self._sparse_cache, valid_ids):
            vecs = sparse_embedder.encode([r.content for r in all_records])
            sparse_index.rebuild(zip(valid_ids, vecs))
            # 注意：此时 self.sparse_index 尚未赋值，直接落盘局部实例
            try:
                sparse_index.save(self._sparse_cache)
            except OSError as e:
                logging.getLogger("kb.service").warning(
                    "稀疏索引落盘失败: %s", e)
        return sparse_embedder, sparse_index

    def _persist_sparse(self) -> None:
        """稀疏索引落盘；失败记 WARNING 不阻塞主流程（同 BM25 模式）。
        English: Persist the sparse index; on failure log a WARNING without blocking the main flow (same as BM25)."""
        import logging
        if self.sparse_index is None:
            return
        try:
            self.sparse_index.save(self._sparse_cache)
        except OSError as e:
            logging.getLogger("kb.service").warning("稀疏索引落盘失败: %s", e)

    def _sparse_add(self, records) -> None:
        """写入路径维护：批量 encode 并入稀疏索引 + 落盘。
        English: Write-path maintenance: batch-encode and merge into the sparse index and persist."""
        if self.sparse_embedder is None or not records:
            return
        vecs = self.sparse_embedder.encode([r.content for r in records])
        for r, v in zip(records, vecs):
            self.sparse_index.add(r.id, v)
        self._persist_sparse()

    def _sparse_remove(self, record_ids) -> None:
        """删除路径维护：增量移出稀疏索引 + 落盘。
        English: Delete-path maintenance: incrementally remove from the sparse index and persist."""
        if self.sparse_index is None:
            return
        for rid in record_ids:
            self.sparse_index.remove(rid)
        self._persist_sparse()

    # ---- 记忆 CRUD ----
    def add_memory(self, content: str, tags: list[str] | None = None,
                   source: str | None = None, namespace: str = "default",
                   agent_id: str = "default", client: str = "default",
                   project: str | None = None) -> Record:
        """写入一条记忆短文本并嵌入。

        v2（2026-08-30）：隔离键 = (client, project)；agent_id 仅作兼容冗余
        （Record.agent_id := project 或 "default"，供旧展示层，不参与隔离）。
        N22a/TASK-0069：dedup_enabled 时先做语义去重检查，命中则抛 DuplicateError
        （api 层捕获返回 409）；关闭时零行为变化。
        English: Write a memory short text and embed it. v2: isolation key = (client, project);
        agent_id is kept only as a compatibility redundancy (Record.agent_id := project or
        "default"), not used for isolation. N22a: when dedup_enabled, run a semantic dedup
        check first and raise DuplicateError on a hit (mapped to 409 by the api layer);
        zero behavior change when disabled."""
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
        # v2：agent_id 冗余 = project 或 default（不再由调用方指定身份）
        project = project or ""
        record = Record(content=content, tags=tags or [], source=source,
                        namespace=namespace, agent_id=project or "default",
                        client=client, project=project)
        vec = self.embedder.embed_texts([content])[0]
        self.store.add([record], [vec])
        self.bm25.add(record)
        self._persist_bm25()
        self._sparse_add([record])
        self._audit_access("write", agent_id, type="memory",
                           record_id=record.id, content=content,
                           namespace=namespace, client=client,
                           project=project)
        return record

    def get_memory(self, record_id: str,
                   agent_id: str = "default",
                   client: str = "default",
                   project: str | None = None) -> Record | None:
        """读取单条记忆；v2：memory 类型仅 (client, project) 归属者可读
        （数据不出库，非归属按 None 处理，调用方转 NOT_FOUND/FORBIDDEN）。
        English: Read a single memory; v2: memory records are readable only by the
        (client, project) owner (looked up in the store; non-owners get None)."""
        record = self.store.get(record_id)
        if record is None:
            return None
        # 共享知识（doc/web chunk）所有客户端可见；个人记忆按 (client, project) 校验
        if record.type == RecordType.MEMORY and (
                record.client != client or (record.project or "") != (project or "")):
            return None
        self._audit_access("read", agent_id, type=record.type.value,
                           record_id=record_id, content=record.content,
                           namespace=record.namespace, client=client,
                           project=project)
        return record

    def list_memories(self, **filters) -> tuple[list[Record], int]:
        """列表（过滤 + 分页），返回 (记录, 总数)。
        English: List (filter + pagination), returning (records, total)."""
        return self.store.list_records(**filters)

    def list_records(self, **filters) -> tuple[list[Record], int]:
        """记录列表（过滤 + 分页），直接委托 store；watcher 等内部与验收测试使用。
        English: List records (filter + pagination) delegating directly to store; used by watcher and acceptance tests."""
        return self.store.list_records(**filters)

    def update_memory(self, record_id: str, content: str | None = None,
                      tags: list[str] | None = None,
                      agent_id: str = "default",
                      client: str = "default",
                      project: str | None = None) -> Record | None:
        """更新记忆；content 变更时重新嵌入并更新 updated_at。
        v2：memory 类型仅 (client, project) 归属者可更新，非归属返回 None（FORBIDDEN）。
        English: Update a memory; re-embed and refresh updated_at when content changes.
        v2: memory records can only be updated by their (client, project) owner."""
        from datetime import datetime
        record = self.store.get(record_id)
        if record is None:
            return None
        if record.type == RecordType.MEMORY and (
                record.client != client or (record.project or "") != (project or "")):
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
        self._sparse_remove([record_id])
        self._sparse_add([record])
        self._audit_access("update", agent_id, type=record.type.value,
                           record_id=record_id, content=record.content,
                           namespace=record.namespace, client=client,
                           project=project)
        return record

    def delete_memory(self, record_id: str,
                      agent_id: str = "default",
                      client: str = "default",
                      project: str | None = None) -> bool:
        """删除记忆；不存在返回 False。
        v2：memory 类型仅 (client, project) 归属者可删，非归属返回 False（FORBIDDEN）。
        English: Delete a memory; return False if it does not exist.
        v2: memory records can only be deleted by their (client, project) owner."""
        record = self.store.get(record_id)
        if record is None:
            return False
        if record.type == RecordType.MEMORY and (
                record.client != client or (record.project or "") != (project or "")):
            return False
        self.store.delete([record_id])
        self.bm25.remove(record_id)
        self._persist_bm25()
        self._sparse_remove([record_id])
        self._audit_access("delete", agent_id, type=record.type.value,
                           record_id=record_id, namespace=record.namespace,
                           client=client, project=project)
        return True

    # ---- 文档摄取与管理 ----
    def add_document(self, path: str | Path,
                     source: str | None = None,
                     agent_id: str = "default",
                     client: str = "default",
                     project: str | None = None) -> dict:
        """解析本地文档 → 切分 → 每 chunk 一条 doc_chunk Record 入库。

        返回 {"source": 文件名, "chunks": 入库块数}；
        source 缺省取文件名，multipart 上传时以上传文件名入库（覆盖临时文件名）。
        空文档切不出 chunk，不入库直接返回 chunks=0。
        A 节点：doc/web 为共享知识库，记录归属 agent_id 仅用于审计，检索不隔离。
        English: Parse local document → split → store each chunk as a doc_chunk Record.
        Returns {"source": filename, "chunks": count ingested}; source defaults to the file name,
        and on multipart upload the uploaded file name is stored (overriding the temp file name).
        An empty document yields no chunks and is not ingested, returning chunks=0 directly.
        A-node: doc/web chunks form the shared knowledge base; agent_id is recorded for audit only,
        not used for retrieval isolation."""
        path = Path(path)
        text = parse_file(path)
        chunks = chunk_text(text, self.settings.chunk_size,
                            self.settings.chunk_overlap)
        doc_source = source or path.name
        if not chunks:
            return {"source": doc_source, "chunks": 0}
        records = [Record(content=c, type=RecordType.DOC_CHUNK,
                          source=doc_source, agent_id=agent_id, client=client)
                   for c in chunks]
        vecs = self.embedder.embed_texts([r.content for r in records])
        self.store.add(records, vecs)
        for r in records:
            self.bm25.add(r)
        self._persist_bm25()
        self._sparse_add(records)
        self._audit_access("ingest", agent_id, type="doc_chunk",
                           source=doc_source, content=records[0].content,
                           namespace=records[0].namespace, client=client,
                           project=project)
        return {"source": doc_source, "chunks": len(records)}

    def add_webpage(self, url: str,
                    agent_id: str = "default",
                    client: str = "default",
                    project: str | None = None) -> dict:
        """抓取网页正文 → 切分 → 每 chunk 一条 web_chunk Record 入库。

        返回 {"source": url, "chunks": 入库块数}；抓取/正文提取失败抛
        WebFetchError（API 层转 400 + 原因）。正文切不出 chunk 时不入库，
        直接返回 chunks=0。
        A 节点：web chunk 属共享知识库，agent_id 仅审计，不隔离。
        English: Fetch webpage body → split → store each chunk as a web_chunk Record.
        Returns {"source": url, "chunks": count}; a fetch/body-extraction failure raises WebFetchError
        (mapped to 400 with reason by the API layer). No chunks are ingested and chunks=0 is returned
        when the body yields none. A-node: web chunks are shared; agent_id is audit-only."""
        text = fetch_webpage(url)
        chunks = chunk_text(text, self.settings.chunk_size,
                            self.settings.chunk_overlap)
        if not chunks:
            return {"source": url, "chunks": 0}
        records = [Record(content=c, type=RecordType.WEB_CHUNK,
                          source=url, agent_id=agent_id, client=client) for c in chunks]
        vecs = self.embedder.embed_texts([r.content for r in records])
        self.store.add(records, vecs)
        for r in records:
            self.bm25.add(r)
        self._persist_bm25()
        self._sparse_add(records)
        self._audit_access("ingest", agent_id, type="web_chunk",
                           source=url, content=records[0].content,
                           namespace=records[0].namespace, client=client,
                           project=project)
        return {"source": url, "chunks": len(records)}

    def list_documents(self) -> list[dict]:
        """按 source 聚合文档列表（source 非空的所有记录，不限 type）。
        chunks=该 source 记录数；chars=content 总字符数；last_imported=最大 created_at。
        English: Aggregate a document list by source (all records with a non-empty source, any type).
        chunks=record count for the source; chars=total content chars; last_imported=max created_at."""
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
        """按 source 删除文档全部记录，返回删除数量；同步清理 BM25 索引。
        English: Delete all records of a document by source, returning the count; also clean the BM25 index."""
        ids = [r.id for r in self.store.iter_all() if r.source == source]
        n = self.store.delete_by_source(source)
        for rid in ids:
            self.bm25.remove(rid)
        self._persist_bm25()
        self._sparse_remove(ids)
        return n

    # ---- 检索与统计 ----
    def _audit_access(self, action: str, agent_id: str, *,
                      type: str | None = None, record_id: str | None = None,
                      content: str | None = None, query: str | None = None,
                      hits: int | None = None, source: str | None = None,
                      namespace: str = "default",
                      client: str = "default",
                      project: str | None = None) -> None:
        """Agent 存取审计统一入口（A 节点 spec 2.4）：开关关闭零成本跳过；
        content/query 截断摘要由 audit.py 负责，本方法不阻塞主流程。
        English: Unified Agent access-audit entry (A-node spec 2.4): skipped when the toggle is off;
        content/query are snipped by audit.py; never blocks the main flow."""
        if not getattr(self.settings, "access_audit_enabled", True):
            return
        try:
            from kb.audit import log_access_event
            log_access_event(
                agent_id=agent_id, action=action, record_id=record_id,
                type=type, content=content, query=query, hits=hits,
                namespace=namespace, source=source, client=client,
                project=project)
        except Exception:
            # 审计失败不阻塞主流程（audit.py 内部已兜底，这里再兜一层）
            pass

    def query_access_audit(self, client: str | None = None,
                           project: str | None = None,
                           agent: str | None = None,
                           action: str | None = None,
                           days: int | None = None,
                           limit: int = 100) -> list[dict]:
        """用户查询存取审计（v2 spec 2.5）：读 log_dir/agent-audit/ 下匹配
        (client, project) 的分文件 JSON 行——身份由文件名承载，解析补回（行内不重复记录）。
        client/project 可空（空=全部）；agent 保留为兼容参数（旧三段式文件名回溯）。
        可选 action/days/limit，倒序返回最新在前。
        English: Query the access audit (v2 spec 2.5): reads the per-(client, project) files under
        log_dir/agent-audit/, parsing identity (client/project) back from the file name. client/project
        may be empty (empty = all); agent is kept for backward compatibility with old three-segment names.
        Optional action/days/limit filtering, newest first."""
        import json as _json
        from datetime import datetime as _dt, timedelta, timezone
        from kb.audit import parse_agent_file_name
        agent_dir = self.settings.log_dir / "agent-audit"
        if not agent_dir.is_dir():
            return []
        items: list[dict] = []
        cutoff = None
        if days is not None and days > 0:
            cutoff = _dt.now(timezone.utc) - timedelta(days=days)
        try:
            for f in agent_dir.glob("*__*.log*"):
                if not f.is_file():
                    continue
                ident = parse_agent_file_name(f.name)
                # v2：按 (client, project) 匹配；旧三段式文件名 agent 段仅作兼容回溯
                if client and ident["client"] != client:
                    continue
                if project and ident["project"] != (project or "default"):
                    continue
                if agent and ident.get("agent") and ident["agent"] != agent:
                    continue
                client_c = ident["client"]
                project_c = ident.get("project") or ""
                with open(f, encoding="utf-8-sig") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = _json.loads(line)
                        except ValueError:
                            continue
                        if action and rec.get("action") != action:
                            continue
                        if cutoff is not None:
                            try:
                                ts = _dt.fromisoformat(rec.get("timestamp", ""))
                                if ts < cutoff:
                                    continue
                            except ValueError:
                                pass
                        # 身份由文件名补回（行内不重复记录）
                        rec["client"] = client_c
                        rec["project"] = project_c
                        if ident.get("agent"):
                            rec["agent_id"] = ident["agent"]
                        items.append(rec)
        except OSError:
            return []
        items.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return items[:limit]

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               type: str | None = None, tag: str | None = None,
               agent_id: str = "default",
               client: str = "default",
               project: str | None = None) -> list[dict]:
        """混合检索（v2：按 (client, project) 隔离 memory，doc/web 共享）。
        English: Hybrid retrieval (v2: memory isolated by (client, project); doc/web shared)."""
        results = self.retriever.search(
            query, top_k=top_k, mode=mode, type=type, tag=tag,
            agent_id=agent_id, client=client, project=project or "")
        self._audit_access("search", agent_id, query=query,
                           hits=len(results), namespace="default",
                           client=client, project=project or "")
        return results

    def stats(self) -> dict:
        """运行统计；llm 为当前 LLM 状态（local/cloud/disabled）。
        English: Runtime stats; llm is the current LLM status (local/cloud/disabled)."""
        _, total = self.store.list_records()
        return {"records": total, "device": self.device,
                "llm": self.llm.status.value}

    # ---- 问答（基础 RAG + 智能路由）----
    def ask(self, question: str, agent_id: str = "default",
            client: str = "default",
            project: str | None = None) -> dict:
        """RAG 问答：检索 → 上下文拼装（字符预算截断）→ 护栏 prompt → 生成 → 附 sources。

        - 检索 top_k=5，结果按 score 降序拼入上下文；
        - 字符预算 = context_token_limit * 2（粗略 token→字符 2:1 估算）；
        - llm_mode=local/cloud：走 N10 原路径（后端由 llm.chat 内部按 status/mode 决定）；
        - llm_mode=auto：智能路由（缓存 → 本地分类 → 敏感覆盖 → 分支路由），见 _ask_auto；
        - LLM 禁用时抛 LLMDisabledError（API 层转 503 + 配置指引）。
        - A 节点：检索按 agent_id 隔离 memory（doc/web 共享），并写 ask 存取审计。
        English: RAG Q&A: retrieve → build context (truncated by char budget) → guarded prompt → generate → attach sources.
        - top_k=5 retrieval, results joined into context in descending score order;
        - char budget = context_token_limit * 2 (~2:1 token-to-char estimate);
        - llm_mode=local/cloud: the N10 original path (backend decided inside llm.chat by status/mode);
        - llm_mode=auto: smart routing (cache → local classify → sensitive override → branch), see _ask_auto;
        - throws LLMDisabledError when LLM is disabled (mapped to 503 with guidance by the API layer).
        - A-node: retrieval isolates memory by agent_id (doc/web shared); an ask access-audit is emitted."""
        if self.llm.status is LLMStatus.DISABLED:
            raise LLMDisabledError("LLM 不可用：本地 Ollama 未响应且未配置云端 Key")
        results = sorted(self.search(question, top_k=5, agent_id=agent_id,
                                     client=client, project=project),
                         key=lambda r: r["score"], reverse=True)
        sources = [{"id": r["id"], "content": r["content"],
                    "score": r["score"], "source": r["source"]} for r in results]
        if self.settings.llm_mode == "auto":
            result = self._ask_auto(question, results, sources)
        else:
            context = self._build_context(results)
            answer = self.llm.chat(self._build_messages(context, question))
            result = {"answer": answer, "sources": sources,
                      "llm": self.llm.status.value}
        self._audit_access("ask", agent_id, query=question, hits=len(results),
                           namespace="default", client=client, project=project)
        return result

    # ---- 智能路由（auto 模式）----
    def _ask_auto(self, question: str, results: list[dict],
                  sources: list[dict]) -> dict:
        """auto 模式智能路由：缓存检查 → 本地分类 → 敏感覆盖 → 分支路由 → 写缓存。

        分支：SENSITIVE / SIMPLE / COMPLEX 无云 → 本地直答（N10 消息拼装）；
        COMPLEX 有云 → 本地压缩上下文 → 云端生成（失败降级本地直答）。
        缓存优先于分类：命中（问题向量与缓存条目余弦最大值 ≥ 阈值）直接返回，不调 LLM。
        English: auto-mode smart routing: cache check → local classify → sensitive override → branch → write cache.
        Branches: SENSITIVE / SIMPLE / COMPLEX-without-cloud → local answer (N10 message assembly);
        COMPLEX-with-cloud → compress context locally → cloud generation (fall back to local on failure).
        Cache takes priority over classification: on a hit (max cosine between question vec and a cached
        entry ≥ threshold) return directly without calling the LLM."""
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
        """本地 LLM 复杂度分类；输出解析失败按 SIMPLE 处理。
        English: Local-LLM complexity classification; on parse failure treat as SIMPLE."""
        messages = [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
            {"role": "user", "content": CLASSIFY_PROMPT.format(q=question)},
        ]
        resp = self.llm.chat(messages, prefer="local", max_tokens=5)
        return _parse_label(resp)

    def _is_sensitive(self, results: list[dict]) -> bool:
        """敏感判定：检索结果 tag 含 'sensitive'，或记录 namespace 命中敏感名单。

        检索结果 dict 不含 namespace 字段，敏感名单非空时按 id 回查 store 取 namespace。
        English: Sensitivity judgment: the result tag contains 'sensitive', or the record namespace hits a
        sensitive list. The result dict lacks a namespace field, so when the sensitive list is non-empty the
        store is looked up by id to fetch the namespace."""
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
        """auto 模式下云端是否可用：注入的云端客户端优先，其次通用云端 API Key 非空（KB_LLM_API_KEY，任意 OpenAI 兼容服务商）。
        English: Whether cloud is available in auto mode: the injected cloud client takes priority; otherwise a non-empty generic cloud API key (KB_LLM_API_KEY, any OpenAI-compatible provider)."""
        if self._cloud_client is not None:
            return True
        return bool(self.settings.cloud_api_key)

    def _answer_complex_cloud(self, question: str,
                              context: str) -> tuple[str, str]:
        """COMPLEX 有云路径：本地压缩上下文 → 云端生成；云端失败降级本地直答。

        云端生成优先用注入的 _cloud_client（若非 None），否则 self.llm.chat(prefer="cloud")。
        返回 (answer, llm_used)。
        English: COMPLEX-with-cloud path: compress context locally → generate in cloud; fall back to local
        answering on cloud failure. Cloud generation prefers the injected _cloud_client (if not None),
        otherwise self.llm.chat(prefer="cloud"). Returns (answer, llm_used)."""
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
        命中提升 LRU 新鲜度并返回该条目。
        English: Cache lookup: a hit occurs when the max cosine between the question vector and a cached
        entry ≥ cache_sim_threshold; on a hit refresh LRU recency and return that entry."""
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
        """写入缓存条目（问题向量/答案/来源/后端）；超容量淘汰最旧（LRU）。
        English: Write a cache entry (question vector / answer / sources / backend); evict the oldest on overflow (LRU)."""
        self._cache[question] = {"question_vec": qvec, "answer": answer,
                                 "sources": sources, "llm": llm_used}
        self._cache.move_to_end(question)
        while len(self._cache) > self.settings.cache_size:
            self._cache.popitem(last=False)

    # ---- 上下文与消息拼装（N10 逻辑，路由路径复用）----
    def _build_context(self, results: list[dict]) -> str:
        """逐条累加 content，超出字符预算即截断停止（最后一条截到剩余预算）。
        English: Accumulate content item by item; truncate and stop once the char budget is exceeded (the last
        item is cut to the remaining budget)."""
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
        """护栏 prompt：system 强约束 + user 参考文档与问题。
        English: Guarded prompt: a strongly constrained system + user reference docs and question."""
        return [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
            {"role": "user",
             "content": f"参考文档：\n{context}\n\n问题：{question}"},
        ]