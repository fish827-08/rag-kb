"""KBService：统一业务编排，组装 store / embedder / bm25 / retriever / llm。
English: Unified business orchestration that assembles store / embedder / bm25 / retriever / llm."""
import ipaddress
import json
import math
import os
import subprocess
import threading
import time
from collections import Counter, OrderedDict
from pathlib import Path
from urllib.parse import urlparse

from kb.config import Settings, get_settings
from kb.embedder import Embedder, EmbeddingUnavailableError
from kb.ingest import (UnsupportedFormatError, WebFetchError, chunk_text,
                       fetch_webpage, parse_file)
from kb.links import LinkError, validate_relation
from kb.llm import LLMClient, LLMStatus
from kb.models import Record, RecordType
from kb.scopes import ScopeManager
from kb.sparse import SparseEmbedder as _SparseEmbedderClass
from kb.sparse import SparseIndex
from kb.storage import ChromaStore
from kb.tables import (TABLE_EXTS, parse_table_file, query_table,
                       row_to_text, rows_from_content)

__all__ = ["KBService", "LLMDisabledError", "UnsupportedFormatError",
           "WebFetchError", "EmbeddingUnavailableError"]

# RAG 护栏系统提示：强约束仅依据参考文档作答，禁止编造
RAG_SYSTEM_PROMPT = "仅依据参考文档回答，无相关信息则明确说明，禁止编造。"

# 表格逐行入库上限：超过后按字符预算聚合成多行块，避免大表写爆向量库
TABLE_MAX_ROW_RECORDS = 500

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


_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_project(project: str | None) -> str | None:
    """校验 project（项目名）格式；空值视为未提供。
    同 agent_id 白名单（字母数字中文、下划线、连字符，1~64），并拒绝 Windows 保留目录名。
    English: Validate a project name; empty means not provided. Same whitelist as
    agent_id (1-64 chars), and reject Windows reserved directory names."""
    if not project:
        return None
    v = str(project).strip()
    if (v in _FORBIDDEN_IDENTS or len(v) > 64 or not _IDENT_RE.match(v)
            or v.upper() in _WINDOWS_RESERVED):
        return ("project 格式非法：仅允许字母/数字/中文/下划线/连字符，"
                f"1~64 字符且不能是系统保留名（当前：{v!r}）")
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


# ---- 嵌入模型解析（设计文档 §3.1，2026-10-19 轻量优先修订）----

# 本特性引入前（v1.0.2 及更早）的唯一默认模型；旧库无模型记录时按此沿用。
LEGACY_DEFAULT_EMBED_MODEL = "BAAI/bge-m3"


def resolve_embed_model(settings: Settings, has_data: bool) -> str:
    """决定本次生效的嵌入模型，并把选择写回 runtime.json（设计文档 §3.1）。

    背景：默认嵌入模型由 bge-m3（1024 维）改为 bge-small-zh（512 维）。
    ChromaDB 集合维度由首个写入向量决定，换模型会让既有数据维度不匹配而查不动，
    因此"库里有数据"时必须沿用库当初使用的模型，绝不能让新默认静默套用到旧库。

    规则：
    - 库为空：跟随配置（并记录），用户可自由改；
    - 库非空且有记录：沿用记录值；与当前配置不一致时记 WARNING 说明如何迁移；
    - 库非空但无记录（早于本特性的历史库）：沿用历史默认 bge-m3，
      除非用户显式配置了 KB_EMBED_MODEL（那说明用户清楚自己在换什么）。

    English: Decide the effective embedding model and persist the choice to runtime.json.
    Because a ChromaDB collection's dimension is fixed by the first vector written, an existing
    store must keep the model it was built with; the new lightweight default must never be
    applied silently to legacy data.
    """
    import json
    import logging

    log = logging.getLogger("kb.service")
    runtime = settings.runtime_file

    def _read_recorded() -> str:
        """读已记录的模型名；文件缺失/损坏时返回空串。"""
        if not runtime.exists():
            return ""
        try:
            data = json.loads(runtime.read_text(encoding="utf-8"))
            return str((data or {}).get("embed_model") or "")
        except (OSError, ValueError, TypeError, AttributeError):
            return ""

    def _remember(name: str) -> None:
        """把模型名写入 runtime.json（保留 device 等其他键）；失败静默不阻塞启动。"""
        try:
            data: dict = {}
            if runtime.exists():
                try:
                    data = json.loads(runtime.read_text(encoding="utf-8")) or {}
                except (OSError, ValueError, TypeError):
                    data = {}
            data["embed_model"] = name
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, ValueError, TypeError):
            pass

    if not has_data:
        # 空库：配置说了算（用户随时可换，不会破坏任何数据）
        _remember(settings.embed_model)
        return settings.embed_model

    recorded = _read_recorded()
    if recorded:
        if recorded != settings.embed_model:
            # 库里已记模型 ≠ 当前配置：必须沿用库里的（换了维度就不兼容）
            if "embed_model" in settings.model_fields_set:
                # 用户显式要求换模型却被沿用 → 必须告警，并给出可操作步骤
                log.warning(
                    "库中数据由 %s 构建，已沿用该模型以避免向量维度不匹配"
                    "（你配置的是 %s）。如确要换，请备份后清空 kb_data/chroma 重新入库。",
                    recorded, settings.embed_model)
            else:
                # 差异只因内置默认值变化（老库沿用建库模型）→ INFO 即可，不刷警告
                log.info("库中数据由 %s 构建，本次沿用该模型（当前内置默认：%s）。",
                         recorded, settings.embed_model)
        return recorded

    # 历史库（早于模型记录特性）：沿用历史默认，除非用户显式配置过
    if "embed_model" in settings.model_fields_set:
        _remember(settings.embed_model)
        return settings.embed_model
    _remember(LEGACY_DEFAULT_EMBED_MODEL)
    log.info(
        "检测到早于模型记录特性的历史数据，已沿用历史默认嵌入模型 %s 并记录"
        "（当前内置默认已改为 %s）。如需换用轻量模型，请备份后清空 kb_data/chroma 重新入库。",
        LEGACY_DEFAULT_EMBED_MODEL, settings.embed_model)
    return LEGACY_DEFAULT_EMBED_MODEL


# ---- 运行时 LLM 配置（2026-11-03，Web UI 保存到 kb_data/llm_config.json）----
LLM_MODES = ("off", "local", "auto", "cloud")
LLM_CONFIG_FIELDS = ("llm_mode", "ollama_base_url", "llm_model",
                     "llm_base_url", "llm_cloud_model", "llm_api_key")
_LLM_PATCH_MAP = {
    "mode": "llm_mode",
    "ollama_base_url": "ollama_base_url",
    "local_model": "llm_model",
    # 注意：cloud_* 是 Settings 的只读 property，实际落盘字段用 llm_* 原名
    "cloud_base_url": "llm_base_url",
    "cloud_model": "llm_cloud_model",
    "cloud_api_key": "llm_api_key",
}


def _check_llm_url(value: str, name: str, allow_private: bool = True) -> None:
    """LLM 端点只接受 http(s)；长度上限防御异常输入。

    allow_private=False（云端基址）时拒绝回环/私有 IP 字面量，避免把服务
    当跳板去访问内网（SSRF）。确实要连本机 OpenAI 兼容服务时设置
    KB_LLM_ALLOW_PRIVATE_BASE_URL=1 放行。
    """
    if not value.startswith(("http://", "https://")):
        raise ValueError(f"{name} 必须是 http(s) 地址")
    if len(value) > 200:
        raise ValueError(f"{name} 过长（≤200 字符）")
    if allow_private:
        return
    if os.environ.get("KB_LLM_ALLOW_PRIVATE_BASE_URL", "").strip() in ("1", "true", "yes"):
        return
    host = (urlparse(value).hostname or "").lower()
    if not host:
        raise ValueError(f"{name} 缺少主机名")
    if host in ("localhost", "::1"):
        raise ValueError(f"{name} 不允许指向本机；如确需本地服务请设置 KB_LLM_ALLOW_PRIVATE_BASE_URL=1")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError(f"{name} 不允许指向私有/回环地址；如确需请设置 KB_LLM_ALLOW_PRIVATE_BASE_URL=1")


def normalize_llm_patch(patch: dict) -> dict:
    """把 Web UI 的补丁字段校验并映射为 Settings 字段；空串表示「不修改」。

    返回 {"llm_mode": ..., "llm_model": ...} 之类的 Settings 字段字典；
    非法值抛 ValueError（API 层转 422），避免把坏配置写进持久化文件。
    English: Validate UI patch fields and map them to Settings names; blank means
    "leave unchanged". Invalid values raise ValueError (mapped to 422).
    """
    clean: dict = {}
    for key, value in (patch or {}).items():
        field = _LLM_PATCH_MAP.get(key)
        if field is None or value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"{key} 必须是字符串")
        v = value.strip()
        if not v:
            continue  # 空串=不修改，防止误清已保存的 API Key
        if field == "llm_mode":
            if v not in LLM_MODES:
                raise ValueError("mode 只能是 off / local / auto / cloud")
        elif field == "ollama_base_url":
            _check_llm_url(v, key)
        elif field == "llm_base_url":
            # 云端基址禁私有/回环字面量（SSRF 缓解），Ollama 本地地址不受此限
            _check_llm_url(v, key, allow_private=False)
        elif len(v) > 200:
            raise ValueError(f"{key} 过长（≤200 字符）")
        clean[field] = v
    return clean


def load_llm_overrides(path: Path) -> dict:
    """读取运行时 LLM 配置；文件不存在/损坏返回 {}（不阻断服务启动）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if k in LLM_CONFIG_FIELDS and isinstance(v, str)}


def save_llm_overrides(path: Path, data: dict) -> None:
    """原子写入运行时 LLM 配置（先写 .tmp 再 replace，避免半截文件）。

    文件含 API Key，写入后收紧权限：POSIX chmod 600；Windows 用 icacls
    去掉继承、只授权当前用户（失败静默，不影响配置生效）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME") or ""
            if user:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r",
                     "/grant:r", f"{user}:F"],
                    check=False, capture_output=True, timeout=10)
        else:
            os.chmod(path, 0o600)
    except (OSError, subprocess.SubprocessError):
        pass


class KBService:
    """记忆服务核心；REST / MCP / CLI 共用。
    English: Core memory service; shared by REST / MCP / CLI."""

    def __init__(self, settings: Settings | None = None, llm=None):
        """组装各组件；全局库启动时打开，项目子库按需懒加载。
        llm 未注入时自建 LLMClient(settings)；注入时直接用（测试替身）。
        English: Assemble components; the global store opens at startup and project scopes
        are lazy-loaded. When llm is not injected, build an LLMClient(settings); when
        injected, use it directly (test double)."""
        self.settings = settings or get_settings()
        # 运行时 LLM 配置（Web UI 保存）：覆盖环境变量，重启后依然生效
        self._llm_overrides = load_llm_overrides(self.settings.llm_config_file)
        for _field, _value in self._llm_overrides.items():
            setattr(self.settings, _field, _value)
        self.device = self.settings.device or "cpu"
        # 设备兜底（2026-11-03）：runtime.json 可能残留 cuda 选择，而当前 torch 是 CPU 版；
        # 若不做兜底，sentence-transformers 会在 cuda 上失败后退回在线加载，
        # 触发 HuggingFace 网络重试，一次查询卡住数分钟。
        if str(self.device).lower().startswith("cuda"):
            try:
                import torch
                if not torch.cuda.is_available():
                    import logging
                    logging.getLogger("kb.service").warning(
                        "配置要求 CUDA，但当前 torch 不支持 CUDA，已自动回退 cpu")
                    self.device = "cpu"
            except ImportError:
                self.device = "cpu"
        # 并发写入锁（改进项 2）：ChromaDB / BM25 / 稀疏索引均非线程安全，
        # 所有作用域的写路径统一串行化；读路径不加锁。
        self._write_lock = threading.RLock()
        store = ChromaStore(self.settings.chroma_dir)
        all_records = list(store.iter_all())
        # 嵌入模型解析（设计文档 §3.1）：全局库已有数据则沿用其建库模型；
        # 项目子库首次创建时沿用同一模型，避免同一安装出现维度不一致。
        self.settings.embed_model = resolve_embed_model(self.settings, bool(all_records))
        self.embedder = Embedder(
            self.settings.embed_model, device=self.device,
            idle_unload_seconds=self.settings.idle_unload_seconds,
            torch_threads=self.settings.torch_threads,
            embed_batch_size=self.settings.embed_batch_size)
        self.scopes = ScopeManager(
            self.settings, self.embedder, self._write_lock,
            reranker=self._build_reranker(),
            sparse_builder=self._build_sparse,
            global_store=store, global_records=all_records)
        # 兼容别名：既有测试/调用方直接访问全局库组件
        g = self.scopes.global_scope
        self.store = g.store
        self.bm25 = g.bm25
        self._bm25_cache = g.bm25_cache
        self.sparse_embedder = g.sparse_embedder
        self.sparse_index = g.sparse_index
        self.retriever = g.retriever
        self.llm = llm or LLMClient(self.settings)
        # 云端客户端注入点：None=无独立云端客户端（真实云端由 self.llm 统一承担）
        self._cloud_client = None
        # /ask 答案缓存（LRU）：key=问题原文，条目含问题向量/答案/来源/后端
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._stats_cache: tuple[float, int] = (0.0, 0)

    # ---- 运行时 LLM 配置（Web UI：云端 / Ollama 本地）----
    def llm_info(self) -> dict:
        """当前 LLM 配置与可用性（API Key 只回是否已设置，不回明文）。"""
        s = self.settings
        avail = self.llm.availability() if hasattr(self.llm, "availability") else {}
        return {
            "mode": s.llm_mode,
            "status": self.llm.status.value,
            "local": {"base_url": s.ollama_base_url, "model": s.llm_model,
                      "available": bool(avail.get("local"))},
            "cloud": {"base_url": s.cloud_base_url, "model": s.cloud_model,
                      "api_key_set": bool(s.cloud_api_key),
                      "available": bool(avail.get("cloud"))},
            "config_file": str(s.llm_config_file),
        }

    def reconfigure_llm(self, patch: dict) -> dict:
        """保存运行时 LLM 配置并热重建客户端，返回最新状态。

        只改 LLM 相关字段；持久化到 kb_data/llm_config.json，重启后仍生效。
        重建会清空 /ask 答案缓存（模型/模式变了，旧答案不再可信）。
        English: Persist runtime LLM config and rebuild the client in place; clears
        the ask cache because cached answers are no longer trustworthy.
        """
        clean = normalize_llm_patch(patch)
        with self._write_lock:
            merged = dict(self._llm_overrides)
            merged.update(clean)
            # v2.4：云端不再预填服务商；配置了 Key 却缺 Base URL/模型时直接报错，
            # 避免写入一份运行时必然失败的配置（UI 会显示失败提示）。
            eff_mode = merged.get("llm_mode", self.settings.llm_mode)
            eff_key = (merged.get("llm_api_key") or self.settings.llm_api_key
                       or self.settings.deepseek_api_key)
            eff_base = (merged.get("llm_base_url") or self.settings.llm_base_url
                        or self.settings.deepseek_base_url)
            eff_model = (merged.get("llm_cloud_model")
                         or self.settings.llm_cloud_model
                         or self.settings.deepseek_model)
            if eff_mode in ("cloud", "auto") and eff_key and not (eff_base and eff_model):
                raise ValueError(
                    "云端模式需同时填写 Base URL 与模型名（v2.4 起不再预填服务商）")
            save_llm_overrides(self.settings.llm_config_file, merged)
            self._llm_overrides = merged
            for field, value in merged.items():
                setattr(self.settings, field, value)
            self.llm = LLMClient(self.settings)
            self._cache.clear()
        return self.llm_info()

    def _persist_bm25(self, scope=None) -> None:
        """BM25 语料落盘；失败记 WARNING 不阻塞主流程（N27）。
        English: Persist the BM25 corpus; on failure log a WARNING without blocking the main flow (N27)."""
        import logging
        scope = scope or self.scopes.global_scope
        try:
            scope.bm25.save_corpus(scope.bm25_cache)
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

    def _build_sparse(self, all_records, base_dir):
        """sparse_enabled 时组装 (SparseEmbedder, SparseIndex)；失败降级 (None, None)。

        每个作用域独立索引，落盘到各自 base_dir/sparse_index.json。
        English: Build per-scope (SparseEmbedder, SparseIndex) when sparse_enabled;
        degrade to (None, None) on failure. Each scope persists its own index file."""
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
        cache = Path(base_dir) / "sparse_index.json"
        sparse_index = SparseIndex()
        valid_ids = [r.id for r in all_records]
        if not sparse_index.load(cache, valid_ids):
            vecs = sparse_embedder.encode([r.content for r in all_records])
            sparse_index.rebuild(zip(valid_ids, vecs))
            # 注意：调用方尚未持有索引引用，直接落盘局部实例
            try:
                sparse_index.save(cache)
            except OSError as e:
                logging.getLogger("kb.service").warning(
                    "稀疏索引落盘失败: %s", e)
        return sparse_embedder, sparse_index

    def _persist_sparse(self, scope) -> None:
        """稀疏索引落盘；失败记 WARNING 不阻塞主流程（同 BM25 模式）。
        English: Persist the sparse index; on failure log a WARNING without blocking the main flow (same as BM25)."""
        import logging
        if scope.sparse_index is None:
            return
        try:
            scope.sparse_index.save(scope.base_dir / "sparse_index.json")
        except OSError as e:
            logging.getLogger("kb.service").warning("稀疏索引落盘失败: %s", e)

    def _sparse_add(self, scope, records) -> None:
        """写入路径维护：批量 encode 并入稀疏索引 + 落盘。
        English: Write-path maintenance: batch-encode and merge into the sparse index and persist."""
        if scope.sparse_embedder is None or not records:
            return
        vecs = scope.sparse_embedder.encode([r.content for r in records])
        for r, v in zip(records, vecs):
            scope.sparse_index.add(r.id, v)
        self._persist_sparse(scope)

    def _sparse_remove(self, scope, record_ids) -> None:
        """删除路径维护：增量移出稀疏索引 + 落盘。
        English: Delete-path maintenance: incrementally remove from the sparse index and persist."""
        if scope.sparse_index is None:
            return
        for rid in record_ids:
            scope.sparse_index.remove(rid)
        self._persist_sparse(scope)

    # ---- 记忆 CRUD ----
    def add_memory(self, content: str, tags: list[str] | None = None,
                   source: str | None = None, namespace: str = "default",
                   agent_id: str = "default", client: str = "default",
                   project: str | None = None) -> Record:
        """写入一条记忆短文本并嵌入。

        v2.1（2026-10-20）：project 选择项目子库（空=全局库），项目间物理隔离；
        client 仅用于审计归类；agent_id 为兼容冗余（Record.agent_id := project 或 "default"）。
        N22a/TASK-0069：dedup_enabled 时先做语义去重检查，命中则抛 DuplicateError
        （api 层捕获返回 409）；关闭时零行为变化。
        English: Write a memory short text and embed it. v2.1: project selects the
        project sub-store (empty = global) with physical isolation between projects;
        client is audit-only; agent_id is a compatibility redundancy
        (Record.agent_id := project or "default"). N22a: when dedup_enabled, run a
        semantic dedup check first and raise DuplicateError on a hit (mapped to 409 by
        the api layer); zero behavior change when disabled."""
        with self._write_lock:
            project = project or ""
            scope = self.scopes.get(project)
            from kb.governance import DuplicateError, check_duplicate
            if self.settings.dedup_enabled:
                existing_id, similarity = check_duplicate(
                    content, scope.store, self.embedder,
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
            record = Record(content=content, tags=tags or [], source=source,
                            namespace=namespace, agent_id=project or "default",
                            client=client, project=project)
            vec = self.embedder.embed_texts([content])[0]
            scope.store.add([record], [vec])
            scope.bm25.add(record)
            self._persist_bm25(scope)
            self._sparse_add(scope, [record])
            self.scopes.sync_count(scope)
            self._audit_access("write", agent_id, type="memory",
                               record_id=record.id, content=content,
                               namespace=namespace, client=client,
                               project=project)
            return record

    def get_memory(self, record_id: str,
                   agent_id: str = "default",
                   client: str = "default",
                   project: str | None = None) -> Record | None:
        """读取单条记忆（memory 与 doc/web 同规则）。

        v2.1：带 project 时先查项目子库再查全局库；未传时先全局后各项目子库。
        English: Read a single memory (same rule for doc/web records). v2.1: with
        project, look in that project's sub-store then the global store; without it,
        global first and then each project store."""
        _, record = self.scopes.locate(record_id, project)
        if record is None:
            return None
        self._audit_access("read", agent_id, type=record.type.value,
                           record_id=record_id, content=record.content,
                           namespace=record.namespace, client=client,
                           project=project)
        return record

    def list_memories(self, project: str | None = None,
                      **filters) -> tuple[list[Record], int]:
        """列表（过滤 + 分页），返回 (记录, 总数)；project 指定子库，空=全局库。
        English: List (filter + pagination), returning (records, total);
        project selects the sub-store, empty means the global store."""
        return self.scopes.get(project).store.list_records(**filters)

    def list_records(self, project: str | None = None,
                     **filters) -> tuple[list[Record], int]:
        """记录列表（过滤 + 分页）；watcher 等内部与验收测试使用。
        English: List records (filter + pagination); used by watcher and acceptance tests."""
        return self.scopes.get(project).store.list_records(**filters)

    def update_memory(self, record_id: str, content: str | None = None,
                      tags: list[str] | None = None,
                      agent_id: str = "default",
                      client: str = "default",
                      project: str | None = None) -> Record | None:
        """更新记忆；content 变更时重新嵌入并更新 updated_at。
        按 project 定位子库；未指定时先全局后项目库。
        English: Update a memory; re-embed and refresh updated_at when content changes.
        The project selects the sub-store; when omitted, global is searched first."""
        with self._write_lock:
            from datetime import datetime
            scope, record = self.scopes.locate(record_id, project)
            if record is None:
                return None
            if content is not None:
                record.content = content
            if tags is not None:
                record.tags = tags
            record.updated_at = datetime.now().isoformat()
            # v2.4.1：先嵌入再删旧；嵌入失败时旧向量仍在，不会留下空记录
            vec = self.embedder.embed_texts([record.content])[0]
            scope.store.delete([record_id])
            scope.store.add([record], [vec])
            scope.bm25.remove(record_id)
            scope.bm25.add(record)
            self._persist_bm25(scope)
            self._sparse_remove(scope, [record_id])
            self._sparse_add(scope, [record])
            self._audit_access("update", agent_id, type=record.type.value,
                               record_id=record_id, content=record.content,
                               namespace=record.namespace, client=client,
                               project=project)
            return record

    def delete_memory(self, record_id: str,
                      agent_id: str = "default",
                      client: str = "default",
                      project: str | None = None) -> bool:
        """删除记忆；不存在返回 False。按 project 定位子库。
        English: Delete a memory; return False if it does not exist.
        The project selects the sub-store."""
        with self._write_lock:
            scope, record = self.scopes.locate(record_id, project)
            if record is None:
                return False
            scope.store.delete([record_id])
            scope.bm25.remove(record_id)
            self._persist_bm25(scope)
            self._sparse_remove(scope, [record_id])
            if scope.links is not None:
                scope.links.remove_record(record_id)
            self.scopes.sync_count(scope)
            self._audit_access("delete", agent_id, type=record.type.value,
                               record_id=record_id, namespace=record.namespace,
                               client=client, project=project)
            return True

    def batch_delete_records(self, record_ids: list[str],
                             project: str | None = None,
                             agent_id: str = "default",
                             client: str = "default") -> int:
        """批量删除记录并级联清理 BM25/稀疏索引/关联边/计数缓存。

        删除不可逆前由调用方负责确认；不存在的 ID 直接跳过。按作用域分组后
        每个作用域只落盘一次 BM25，并写一条 forget 审计事件。
        English: Batch-delete records with cascade cleanup of BM25, sparse index,
        link edges and count cache. Callers own the irreversible confirmation;
        missing IDs are skipped. Groups by scope so each BM25 corpus is persisted once,
        and writes one forget audit event per scope."""
        ids = list(dict.fromkeys(record_ids or []))
        if not ids:
            return 0
        with self._write_lock:
            groups: dict[str, tuple[object, list[str]]] = {}
            for rid in ids:
                scope, record = self.scopes.locate(rid, project)
                if record is None:
                    continue
                groups.setdefault(scope.name, (scope, []))[1].append(rid)
            total = 0
            for name, (scope, group_ids) in groups.items():
                scope.store.delete(group_ids)
                scope.bm25.remove_many(group_ids)
                self._persist_bm25(scope)
                self._sparse_remove(scope, group_ids)
                if scope.links is not None:
                    scope.links.remove_records(group_ids)
                self.scopes.sync_count(scope)
                self._audit_access(
                    "forget", agent_id, type="memory",
                    content=f"批量遗忘 {len(group_ids)} 条记录"
                            f"（{name or '全局'}）",
                    hits=len(group_ids), client=client,
                    project=name or None)
                total += len(group_ids)
            return total

    # ---- 记忆关联（轻量知识图谱，2026-10-22 links spec）----
    def _locate_link_pair(self, from_id: str, to_id: str,
                          project: str | None, *, require_memory: bool = True):
        """定位关联两端并校验：同作用域、存在、均为 memory（require_memory 时）。

        返回 (scope, from_record, to_record)；不满足约束抛 LinkError。
        English: Locate both link endpoints and validate existence, same scope, and
        memory type (when require_memory); raises LinkError otherwise."""
        scope_a, rec_a = self.scopes.locate(from_id, project)
        scope_b, rec_b = self.scopes.locate(to_id, project)
        if rec_a is None or rec_b is None:
            missing = from_id if rec_a is None else to_id
            raise LinkError("NOT_FOUND", f"记录不存在：{missing}")
        if scope_a.name != scope_b.name:
            raise LinkError("CROSS_SCOPE",
                            "两条记忆不在同一作用域（全局库/项目子库）内，无法关联")
        if require_memory and (rec_a.type is not RecordType.MEMORY
                               or rec_b.type is not RecordType.MEMORY):
            raise LinkError("MEMORY_ONLY", "仅 memory 类型记录可以建立关联")
        return scope_a, rec_a, rec_b

    def link_memories(self, from_id: str, to_id: str,
                      relation: str = "related",
                      agent_id: str = "default",
                      client: str = "default",
                      project: str | None = None) -> dict:
        """建立两条记忆的关联；重复建边幂等（created=false）。

        约束：禁止自关联、关系白名单、同作用域、两端均为 memory、
        单条记录边数不超过 KB_LINK_MAX_PER_RECORD。
        English: Link two memories; duplicate edges are idempotent (created=false).
        Constraints: no self-link, whitelisted relation, same scope, memory-only,
        and per-record edge count under KB_LINK_MAX_PER_RECORD."""
        if from_id == to_id:
            raise LinkError("SELF_LINK", "不能把记忆关联到自身")
        relation = validate_relation(relation)
        with self._write_lock:
            scope, _, _ = self._locate_link_pair(from_id, to_id, project)
            cap = max(0, int(self.settings.link_max_per_record or 0))
            if cap and not scope.links.has(from_id, to_id, relation):
                for rid in (from_id, to_id):
                    if scope.links.count_for(rid) >= cap:
                        raise LinkError(
                            "LIMIT_REACHED",
                            f"记录 {rid} 的关联数已达上限 {cap}")
            created = scope.links.add(from_id, to_id, relation)
            self._audit_access("link", agent_id, type="memory",
                               record_id=from_id,
                               content=f"{from_id} -[{relation}]-> {to_id}",
                               client=client, project=project)
            return {"ok": True, "from_id": from_id, "to_id": to_id,
                    "relation": relation, "created": created}

    def unlink_memories(self, from_id: str, to_id: str,
                        relation: str | None = None,
                        agent_id: str = "default",
                        client: str = "default",
                        project: str | None = None) -> int:
        """取消关联（任一方向）；relation 为空时删除该对全部关系，返回删除数。
        English: Remove links between two records in either direction; when relation
        is empty, remove all relations of the pair; returns the removed count."""
        relation = validate_relation(relation) if relation else None
        with self._write_lock:
            scope, _, _ = self._locate_link_pair(from_id, to_id, project,
                                                 require_memory=False)
            removed = scope.links.remove(from_id, to_id, relation)
            self._audit_access("unlink", agent_id, type="memory",
                               record_id=from_id,
                               content=f"{from_id} -/- {to_id}",
                               client=client, project=project)
            return removed

    def list_links(self, record_id: str, relation: str | None = None,
                   project: str | None = None) -> dict:
        """列某条记忆的一跳邻居（含内容/类型/标签/方向）；记录不存在抛 LinkError。
        English: List one-hop neighbors of a memory (content/type/tags/direction);
        raises LinkError when the record is missing."""
        relation = validate_relation(relation) if relation else None
        scope, record = self.scopes.locate(record_id, project)
        if record is None:
            raise LinkError("NOT_FOUND", f"记录不存在：{record_id}")
        links = scope.links.neighbors(record_id) if scope.links else []
        if relation:
            links = [item for item in links if item["relation"] == relation]
        # 同一邻居可能有多条关系，Chroma get 要求 ID 唯一
        neighbor_ids = list(dict.fromkeys(item["id"] for item in links))
        records = scope.store.get_many(neighbor_ids)
        items = []
        for item in links:
            neighbor = records.get(item["id"])
            if neighbor is None or neighbor.type is not RecordType.MEMORY:
                continue
            items.append({**item, "content": neighbor.content,
                          "type": neighbor.type.value, "tags": neighbor.tags})
        return {"record_id": record_id, "links": items}

    def _expand_links(self, results: list[dict], project: str | None,
                      type: str | None, tag: str | None) -> list[dict]:
        """基础命中后的 1 跳扩展：前 3 条 memory 命中作种子，邻居按比例计分。

        返回追加项（带 via）；总追加数受 KB_LINK_EXPAND_LIMIT 约束。
        English: One-hop expansion after base hits: the first 3 memory hits act as
        seeds and neighbors are scored by ratio; additional items (with via) are
        bounded by KB_LINK_EXPAND_LIMIT."""
        limit = max(0, int(self.settings.link_expand_limit or 0))
        if not results or limit <= 0 or type not in (None, "memory"):
            return []
        ratio = float(self.settings.link_expand_score_ratio)
        seen = {item["id"] for item in results}
        extras: list[dict] = []
        seeds = [item for item in results
                 if item.get("type") == "memory"][:3]
        for seed in seeds:
            if len(extras) >= limit:
                break
            scope, _ = self.scopes.locate(seed["id"], project)
            if scope is None or scope.links is None:
                continue
            for link in scope.links.neighbors(seed["id"]):
                if len(extras) >= limit:
                    break
                neighbor_id = link["id"]
                if neighbor_id in seen:
                    continue
                neighbor = scope.store.get(neighbor_id)
                if neighbor is None or neighbor.type is not RecordType.MEMORY:
                    continue
                if tag and tag not in neighbor.tags:
                    continue
                seen.add(neighbor_id)
                extras.append({
                    "id": neighbor.id,
                    "content": neighbor.content,
                    "score": round(float(seed.get("score", 0.0)) * ratio, 6),
                    "type": neighbor.type.value,
                    "source": neighbor.source,
                    "tags": neighbor.tags,
                    "created_at": neighbor.created_at,
                    "agent_id": neighbor.agent_id,
                    "via": {"id": seed["id"], "relation": link["relation"]},
                })
        return extras

    # ---- 文档摄取与管理 ----
    def _records_for_path(self, path: Path, *, source: str | None = None,
                          agent_id: str = "default", client: str = "default",
                          project: str | None = None) -> list[Record]:
        """按扩展名把文件转成待入库记录（表格 vs 普通文档）。

        表格（CSV/XLSX）逐行转 `列名=值 | ...` 文本并打 `table` 标签，
        RAG 文档仍走 parse_file + chunk_text（行为不变）。
        English: Convert a file into records: tables (CSV/XLSX) become one
        `column=value | ...` line per row tagged `table`; regular documents keep
        the parse_file + chunk_text behavior."""
        suffix = path.suffix.lower()
        doc_source = source or path.name
        if suffix == ".xls":
            raise UnsupportedFormatError(
                "旧版 .xls 暂不支持表格问答，请用 Excel 另存为 .xlsx 后重试")
        if suffix in TABLE_EXTS:
            try:
                return self._table_records(
                    path, doc_source, agent_id=agent_id,
                    client=client, project=project)
            except UnsupportedFormatError as exc:
                # multipart 上传时 path 是临时文件名，错误信息应回显上传文件名
                message = str(exc)
                if path.name != doc_source:
                    message = message.replace(path.name, doc_source)
                raise UnsupportedFormatError(message) from exc
        text = parse_file(path)
        chunks = chunk_text(text, self.settings.chunk_size,
                            self.settings.chunk_overlap)
        return [Record(content=c, type=RecordType.DOC_CHUNK,
                       source=doc_source, agent_id=agent_id, client=client,
                       project=project or "") for c in chunks]

    def _table_records(self, path: Path, source: str, *, agent_id: str,
                       client: str, project: str | None) -> list[Record]:
        """表格逐行转记录；行数超限时按 chunk_size 聚合成多行块。

        多 sheet 时在行首加 `[表名] ` 前缀（解析时会被剥离），source 仍为
        文件名，保证同一文件重复导入能整体替换。
        English: Turn table rows into records; when the row count exceeds the limit,
        group lines into multi-line blocks by chunk_size. With multiple sheets a
        `[sheet] ` prefix is added (stripped on parsing); source stays the file name
        so a re-import replaces the whole file."""
        tables = parse_table_file(path)
        multi_sheet = len(tables) > 1
        records: list[Record] = []
        for table in tables:
            prefix = f"[{table['sheet']}] " if multi_sheet else ""
            lines = []
            for row in table["rows"]:
                text = row_to_text(table["headers"], row)
                if text:
                    lines.append(prefix + text)
            if not lines:
                continue
            if len(lines) > TABLE_MAX_ROW_RECORDS:
                blocks: list[str] = []
                buf: list[str] = []
                used = 0
                size = max(1, int(self.settings.chunk_size))
                for line in lines:
                    if used + len(line) > size and buf:
                        blocks.append("\n".join(buf))
                        buf, used = [], 0
                    buf.append(line)
                    used += len(line) + 1
                if buf:
                    blocks.append("\n".join(buf))
                contents = blocks
            else:
                contents = lines
            records.extend(
                Record(content=c, type=RecordType.DOC_CHUNK, source=source,
                       agent_id=agent_id, client=client,
                       project=project or "", tags=["table"])
                for c in contents)
        return records

    def add_document(self, path: str | Path,
                     source: str | None = None,
                     agent_id: str = "default",
                     client: str = "default",
                     project: str | None = None) -> dict:
        """解析本地文档 → 切分 → 每 chunk 一条 doc_chunk Record 入库。

        返回 {"source": 文件名, "chunks": 入库块数}；
        source 缺省取文件名，multipart 上传时以上传文件名入库（覆盖临时文件名）。
        空文档切不出 chunk，不入库直接返回 chunks=0。
        v2.1：project 选择项目子库（空=全局库），文档与记忆同样隔离。
        English: Parse local document → split → store each chunk as a doc_chunk Record.
        Returns {"source": filename, "chunks": count ingested}; source defaults to the file name,
        and on multipart upload the uploaded file name is stored (overriding the temp file name).
        An empty document yields no chunks and is not ingested, returning chunks=0 directly.
        v2.1: project selects the project sub-store (empty = global); documents are
        isolated exactly like memories."""
        with self._write_lock:
            path = Path(path)
            doc_source = source or path.name
            records = self._records_for_path(
                path, source=doc_source, agent_id=agent_id, client=client,
                project=project)
            if not records:
                return {"source": doc_source, "chunks": 0}
            scope = self.scopes.get(project)
            vecs = self.embedder.embed_texts([r.content for r in records])
            replaced = self._replace_source_locked(scope, doc_source, records, vecs)
            # 改进项 1B：批量并入语料后单次重建（此前每个 chunk 触发一次全量重建）
            scope.bm25.add_many(records)
            self._persist_bm25(scope)
            self._sparse_add(scope, records)
            self.scopes.sync_count(scope)
            self._audit_access("ingest", agent_id, type="doc_chunk",
                               source=doc_source, content=records[0].content,
                               namespace=records[0].namespace, client=client,
                               project=project)
            return {"source": doc_source, "chunks": len(records),
                    "replaced": replaced}

    def add_documents_bulk(self, paths: list[str | Path],
                           project: str | None = None,
                           agent_id: str = "default",
                           client: str = "default") -> dict:
        """批量导入多个文件：一次编码、一次写库、一次 BM25 重建。

        与逐个调用 add_document 相比，导入整本书/几百个章节时省掉
        「每个文件一次 BM25 全量重建 + 一次 Chroma 事务」的重复开销
        （实测 226 个文件、2337 块从 367 秒降到 60 秒级）。
        语义与 add_document 一致：同一 source 重复导入先删旧再写新；
        解析失败或空文档的文件跳过并记入 failed，不影响其余文件。
        English: Bulk-import many files with one embedding pass, one store write and
        one BM25 rebuild. Same replace-on-same-source semantics as add_document;
        unsupported or empty files are skipped and reported in `failed`."""
        with self._write_lock:
            scope = self.scopes.get(project)
            records: list[Record] = []
            failed: list[dict] = []
            files = 0
            path_list = [Path(p) for p in paths]
            # 同名文件（a/doc.md 与 b/doc.md）用「父目录/文件名」区分，避免互相覆盖
            name_counts = Counter(p.name for p in path_list)
            used_sources: dict[str, int] = {}
            for path in path_list:
                src = path.name
                if name_counts[path.name] > 1:
                    src = f"{path.parent.name}/{path.name}"
                if src in used_sources:
                    used_sources[src] += 1
                    src = f"{src}#{used_sources[src]}"
                else:
                    used_sources[src] = 1
                try:
                    file_records = self._records_for_path(
                        path, source=src, agent_id=agent_id,
                        client=client, project=project)
                except (UnsupportedFormatError, OSError) as exc:
                    failed.append({"source": path.name, "error": str(exc)})
                    continue
                if not file_records:
                    failed.append({"source": path.name, "error": "空文档"})
                    continue
                files += 1
                records.extend(file_records)
            if not records:
                return {"files": 0, "chunks": 0, "replaced": 0, "failed": failed}
            # 先算全部向量再删旧，嵌入失败不会清空已有数据（同 add_document）
            vectors = self.embedder.embed_texts([r.content for r in records])
            sources = {r.source for r in records}
            old_ids = [r.id for r in scope.store.iter_all()
                       if r.source in sources]
            if old_ids:
                scope.store.delete(old_ids)
                scope.bm25.remove_many(old_ids)
                self._sparse_remove(scope, old_ids)
                if scope.links is not None:
                    scope.links.remove_records(old_ids)
            scope.store.add(records, vectors)
            # 改进项：批量并入语料后单次重建（逐文件导入会重建 226 次）
            scope.bm25.add_many(records)
            self._persist_bm25(scope)
            self._sparse_add(scope, records)
            self.scopes.sync_count(scope)
            for source in sorted(sources):
                self._audit_access("ingest", agent_id, type="doc_chunk",
                                   source=source, content=f"bulk:{source}",
                                   namespace="default", client=client,
                                   project=project)
            return {"files": files, "chunks": len(records),
                    "replaced": len(old_ids), "failed": failed}

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
        with self._write_lock:
            text = fetch_webpage(url)
            chunks = chunk_text(text, self.settings.chunk_size,
                                self.settings.chunk_overlap)
            if not chunks:
                return {"source": url, "chunks": 0}
            scope = self.scopes.get(project)
            records = [Record(content=c, type=RecordType.WEB_CHUNK,
                              source=url, agent_id=agent_id, client=client,
                              project=project or "") for c in chunks]
            vecs = self.embedder.embed_texts([r.content for r in records])
            replaced = self._replace_source_locked(scope, url, records, vecs)
            # 改进项 1B：批量并入语料后单次重建
            scope.bm25.add_many(records)
            self._persist_bm25(scope)
            self._sparse_add(scope, records)
            self.scopes.sync_count(scope)
            self._audit_access("ingest", agent_id, type="web_chunk",
                               source=url, content=records[0].content,
                               namespace=records[0].namespace, client=client,
                               project=project)
            return {"source": url, "chunks": len(records), "replaced": replaced}

    def list_documents(self, project: str | None = None) -> list[dict]:
        """按 source 聚合文档列表（source 非空的所有记录，不限 type），并标注 kind。

        kind=web（网页）/ table（CSV/Excel）/ doc（普通文档），供前端区分 RAG 内容来源；
        chunks=该 source 记录数；chars=content 总字符数；last_imported=最大 created_at。
        English: Aggregate a document list by source (any type) and label its kind:
        web / table (CSV/Excel) / doc. chunks=record count; chars=total content chars;
        last_imported=max created_at."""
        docs: dict[str, dict] = {}
        internal_sources = {"kb-monitor", "kb-dispatch"}
        for r in self.scopes.get(project).store.iter_all():
            if not r.source or r.source in internal_sources:
                continue
            d = docs.setdefault(r.source, {
                "source": r.source, "chunks": 0, "chars": 0,
                "last_imported": "", "kind": "doc"})
            if r.type == RecordType.WEB_CHUNK:
                d["kind"] = "web"
            elif "table" in (r.tags or []):
                d["kind"] = "table"
            d["chunks"] += 1
            d["chars"] += len(r.content)
            if r.created_at > d["last_imported"]:
                d["last_imported"] = r.created_at
        return sorted(docs.values(), key=lambda d: d["source"])

    def _replace_source_locked(self, scope, source: str, records: list[Record],
                               vectors: list[list[float]]) -> int:
        """同一 source 重新入库时先删旧再写新，返回替换掉的旧记录数。

        调用方必须已持有 _write_lock；先算好新向量再删除，避免嵌入失败
        导致旧数据被清空而新数据又写不进去。
        English: Replace an existing source: delete old records before writing new
        ones, returning the number replaced. Caller must hold _write_lock; vectors
        are computed before deletion so an embedding failure cannot destroy old data.
        """
        ids = [r.id for r in scope.store.iter_all() if r.source == source]
        if ids:
            scope.store.delete_by_source(source)
            scope.bm25.remove_many(ids)
            self._persist_bm25(scope)
            self._sparse_remove(scope, ids)
            if scope.links is not None:
                scope.links.remove_records(ids)
        scope.store.add(records, vectors)
        return len(ids)

    def delete_document(self, source: str, project: str | None = None) -> int:
        """按 source 删除文档全部记录，返回删除数量；同步清理 BM25 索引。
        English: Delete all records of a document by source, returning the count; also clean the BM25 index."""
        with self._write_lock:
            scope = self.scopes.get(project)
            ids = [r.id for r in scope.store.iter_all() if r.source == source]
            n = scope.store.delete_by_source(source)
            # 改进项 1B：批量移出语料后单次重建
            scope.bm25.remove_many(ids)
            self._persist_bm25(scope)
            self._sparse_remove(scope, ids)
            if scope.links is not None:
                scope.links.remove_records(ids)
            self.scopes.sync_count(scope)
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
        """混合检索；带 project 时合并项目子库与全局库，项目命中加分。

        轻量关联扩展（2026-10-22 links spec）：link_expand_enabled 时对前 3 条
        memory 命中做 1 跳扩展，追加最多 link_expand_limit 条邻居（带 via），
        无关联数据时结果与基础检索完全一致。
        English: Hybrid retrieval; with a project, merge that project's sub-store and
        the global store, boosting project hits. With link_expand_enabled, the first
        3 memory hits are expanded one hop, appending at most link_expand_limit
        neighbors (tagged via); with no links the results are identical to base search."""
        if project and self.scopes.has_project(project):
            scope = self.scopes.get(project)
            project_hits = scope.retriever.search(
                query, top_k=top_k, mode=mode, type=type, tag=tag,
                agent_id=agent_id, client=client, project=project)
            global_hits = self.retriever.search(
                query, top_k=top_k, mode=mode, type=type, tag=tag,
                agent_id=agent_id, client=client, project="")
            boost = float(self.settings.project_boost or 0.0)
            merged: dict[str, dict] = {}
            for hit in project_hits:
                item = dict(hit)
                # 项目加分后仍限制在 [0,1]，避免出现 1.05 这类超出相似度语义的分数
                item["score"] = min(1.0, float(item.get("score", 0.0)) + boost)
                merged[item["id"]] = item
            for hit in global_hits:
                merged.setdefault(hit["id"], hit)
            results = sorted(merged.values(),
                             key=lambda r: r.get("score", 0.0), reverse=True)[:top_k]
        else:
            results = self.retriever.search(
                query, top_k=top_k, mode=mode, type=type, tag=tag,
                agent_id=agent_id, client=client, project=project or "")
        if getattr(self.settings, "link_expand_enabled", True):
            extras = self._expand_links(results, project, type, tag)
            if extras:
                # v2.4.1：关联扩展只填补 top_k 剩余名额，不再突破 top_k
                remaining = max(0, top_k - len(results))
                extras = extras[:remaining]
                if extras:
                    results = sorted(results + extras,
                                     key=lambda item: item.get("score", 0.0),
                                     reverse=True)[:top_k]
        self._audit_access("search", agent_id, query=query,
                           hits=len(results), namespace="default",
                           client=client, project=project or "")
        return results

    def stats(self) -> dict:
        """运行统计；records 为全局 + 项目库总数（5 秒缓存，避免探针频繁开库）。
        English: Runtime stats; records is the global + project total (5s cache to avoid
        reopening stores on every probe)."""
        now = time.monotonic()
        if now - self._stats_cache[0] >= 5:
            self._stats_cache = (now, self.scopes.total_records())
        return {"records": self._stats_cache[1], "device": self.device,
                "llm": self.llm.status.value,
                "embedder": self.embedder.status()}

    # ---- 问答（基础 RAG + 智能路由）----
    def ask(self, question: str, agent_id: str = "default",
            client: str = "default",
            project: str | None = None,
            top_k: int = 5) -> dict:
        """RAG 问答：检索 → 上下文拼装（字符预算截断）→ 护栏 prompt → 生成 → 附 sources。

        - 检索 top_k 默认 5（长文档可调高），结果按 score 降序拼入上下文；
        - 字符预算 = context_token_limit * 2（粗略 token→字符 2:1 估算）；
        - llm_mode=local/cloud：走 N10 原路径（后端由 llm.chat 内部按 status/mode 决定）；
        - llm_mode=auto：智能路由（缓存 → 本地分类 → 敏感覆盖 → 分支路由），见 _ask_auto；
        - LLM 禁用时抛 LLMDisabledError（API 层转 503 + 配置指引）。
        - v2.1（2026-10-20）：带 project 时检索该项目子库 + 全局库，并写 ask 存取审计。
        - v2.4（2026-11-03）：先尝试确定性表格查询（命中直接返回，无 LLM 也能答），
          未命中再走原 RAG 流程。
        English: RAG Q&A: retrieve → build context (truncated by char budget) → guarded prompt → generate → attach sources.
        - top_k=5 retrieval, results joined into context in descending score order;
        - char budget = context_token_limit * 2 (~2:1 token-to-char estimate);
        - llm_mode=local/cloud: the N10 original path (backend decided inside llm.chat by status/mode);
        - llm_mode=auto: smart routing (cache → local classify → sensitive override → branch), see _ask_auto;
        - throws LLMDisabledError when LLM is disabled (mapped to 503 with guidance by the API layer).
        - v2.1: with project, retrieval covers that project's sub-store plus the global store;
          an ask access-audit is emitted.
        - v2.4: try the deterministic table query first (return directly on a hit, even without
          an LLM); otherwise continue with the regular RAG path."""
        table_result = self._table_answer(question, project)
        if table_result is not None:
            self._audit_access("ask", agent_id, query=question,
                               hits=len(table_result["sources"]),
                               namespace="default", client=client,
                               project=project or "")
            return table_result
        if self.llm.status is LLMStatus.DISABLED:
            if self.settings.llm_mode == "off":
                raise LLMDisabledError(
                    "LLM 已关闭（KB_LLM_MODE=off，默认不调用 LLM）；"
                    "如需 /ask 问答请配置 local/auto/cloud")
            raise LLMDisabledError("LLM 不可用：本地 Ollama 未响应且未配置云端 Key")
        results = sorted(self.search(question, top_k=top_k, agent_id=agent_id,
                                     client=client, project=project),
                         key=lambda r: r["score"], reverse=True)
        sources = [{"id": r["id"], "content": r["content"],
                    "score": r["score"], "source": r["source"]} for r in results]
        if self.settings.llm_mode == "auto":
            result = self._ask_auto(question, results, sources,
                                    project=project, top_k=top_k)
        else:
            context = self._build_context(results)
            answer = self.llm.chat(self._build_messages(context, question))
            result = {"answer": answer, "sources": sources,
                      "llm": self.llm.status.value}
        self._audit_access("ask", agent_id, query=question, hits=len(results),
                           namespace="default", client=client, project=project)
        return result

    def _table_answer(self, question: str, project: str | None) -> dict | None:
        """确定性表格问答：扫描带 table 标签的记录并逐行匹配，命中即返回。

        与 RAG 不同，这里不调用 LLM、不做向量检索，答案直接来自表格单元格，
        因此 `小明 语文 95` 这类问题可以精确命中；未命中返回 None 交给原流程。
        English: Deterministic table Q&A: scan records tagged `table` and match rows
        directly. No LLM or vector retrieval is involved, so exact cell questions hit
        precisely; returns None when nothing matches, letting the normal path continue."""
        scopes = []
        if project and self.scopes.has_project(project):
            scopes.append(self.scopes.get(project))
        scopes.append(self.scopes.global_scope)
        rows: list[dict] = []
        records_by_id: dict[str, object] = {}
        for scope in scopes:
            for record in scope.store.iter_all():
                if "table" not in (record.tags or []):
                    continue
                parsed = rows_from_content(record.content)
                if not parsed:
                    continue
                records_by_id[record.id] = record
                for row in parsed:
                    row["_record_id"] = record.id
                rows.extend(parsed)
        result = query_table(rows, question)
        if result is None:
            return None
        answer, matched = result
        # 来源只取真正命中的记录（不再把所有表格记录塞进 sources）
        matched_ids = {row.get("_record_id") for row in matched}
        sources = []
        for rid in matched_ids:
            record = records_by_id.get(rid)
            if record is None:
                continue
            sources.append({"id": record.id, "content": record.content,
                            "score": 1.0, "source": record.source})
        return {"answer": answer, "sources": sources[:5], "llm": "table",
                "table": True}

    def graph(self, project: str | None = None, limit: int = 200) -> dict:
        """记忆知识图谱：memory 节点 + links.db 边（RAG 文档块不入图）。

        项目存在时合并项目子库与全局库；节点按关联度降序截断到 limit。
        English: Memory knowledge graph: memory nodes plus links.db edges (RAG
        document chunks are excluded). With a project, that sub-store is merged
        with the global store; nodes are truncated to `limit` by degree."""
        limit = max(1, min(int(limit or 200), 500))
        scopes = []
        if project and self.scopes.has_project(project):
            scopes.append(self.scopes.get(project))
        scopes.append(self.scopes.global_scope)
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for scope in scopes:
            for record in scope.store.iter_all():
                if record.type != RecordType.MEMORY:
                    continue
                nodes[record.id] = {
                    "id": record.id,
                    "label": (record.content or "").strip()[:80],
                    "type": record.type.value,
                    "project": record.project,
                    "tags": list(record.tags or []),
                    "created_at": record.created_at,
                    "degree": 0,
                }
            if scope.links is not None:
                for edge in scope.links.all_edges():
                    if edge["from"] in nodes and edge["to"] in nodes:
                        edges.append(edge)
        degree: dict[str, int] = {}
        for edge in edges:
            degree[edge["from"]] = degree.get(edge["from"], 0) + 1
            degree[edge["to"]] = degree.get(edge["to"], 0) + 1
        for node_id, node in nodes.items():
            node["degree"] = degree.get(node_id, 0)
        ranked = sorted(nodes.values(),
                        key=lambda n: (n["degree"], n["created_at"]),
                        reverse=True)
        truncated = len(ranked) > limit
        kept = {n["id"] for n in ranked[:limit]}
        return {
            "nodes": ranked[:limit],
            "edges": [e for e in edges
                      if e["from"] in kept and e["to"] in kept],
            "truncated": truncated,
        }

    # ---- 智能路由（auto 模式）----
    def _ask_auto(self, question: str, results: list[dict],
                  sources: list[dict], project: str | None = None,
                  top_k: int = 5) -> dict:
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
        hit = self._cache_lookup(qvec, project=project, top_k=top_k)
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
            self._cache_put(question, qvec, answer, sources, llm_used,
                            project=project, top_k=top_k)
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
        self._cache_put(question, qvec, answer, sources, llm_used,
                        project=project, top_k=top_k)
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
                _, rec = self.scopes.locate(r["id"])
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

    def _cache_lookup(self, qvec: list[float], project: str | None = None,
                      top_k: int = 5) -> dict | None:
        """缓存查找：仅在同一作用域 + 同一 top_k 的条目里比较问题向量余弦。

        缺少这两个维度会导致「A 项目的问题命中 B 项目的答案」，是曾经的隐患。
        English: Cache lookup restricted to the same scope and top_k; without these
        dimensions an answer from project A could be served for project B."""
        best_key: str | None = None
        best_sim = -1.0
        for key, entry in self._cache.items():
            if entry.get("project") != project or entry.get("top_k") != top_k:
                continue
            sim = _cosine(qvec, entry["question_vec"])
            if sim > best_sim:
                best_key, best_sim = key, sim
        if best_key is not None and best_sim >= self.settings.cache_sim_threshold:
            self._cache.move_to_end(best_key)
            return self._cache[best_key]
        return None

    def _cache_put(self, question: str, qvec: list[float], answer: str,
                   sources: list[dict], llm_used: str,
                   project: str | None = None, top_k: int = 5) -> None:
        """写入缓存条目（问题向量/答案/来源/后端/作用域/top_k）；超容量淘汰最旧（LRU）。
        English: Write a cache entry (question vector / answer / sources / backend / scope / top_k);
        evict the oldest on overflow (LRU)."""
        self._cache[question] = {"question_vec": qvec, "answer": answer,
                                 "sources": sources, "llm": llm_used,
                                 "project": project, "top_k": top_k}
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