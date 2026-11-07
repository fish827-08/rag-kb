"""项目/全局作用域管理：每个项目一个独立 Chroma + BM25 子库。

English: Project/global scope manager: each project gets its own Chroma + BM25 store.
"""
import hashlib
import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from kb.bm25 import BM25Index
from kb.links import LinkStore
from kb.retriever import HybridRetriever
from kb.storage import ChromaStore

_PROJECT_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}$")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# ---- 每模型独立 collection（v2.5 迁移）----
# 每个作用域的 Chroma 目录下按模型分 collection：第一个模型沿用历史名
# kb_records，后续模型用 kb_records__<模型名 sha1 前 12 位>，避免维度冲突。
MODELS_FILE = "models.json"
DEFAULT_COLLECTION = "kb_records"


def _count_path(base_dir: Path) -> Path:
    return base_dir / "count.txt"


def _model_key(model: str) -> str:
    """模型名 → 合法 collection 后缀（避免路径/名称非法字符）。"""
    return hashlib.sha1((model or "").encode("utf-8")).hexdigest()[:12]


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        logging.getLogger("kb.scopes").warning("模型映射落盘失败（%s）", path)


def _write_count(base_dir: Path, count: int) -> None:
    try:
        _count_path(base_dir).write_text(str(count), encoding="utf-8")
    except OSError:
        logging.getLogger("kb.scopes").warning(
            "记录计数落盘失败（%s）", base_dir)


def load_model_map(base: Path) -> dict[str, str]:
    """读取作用域内的「模型 → collection」映射；文件缺失/损坏返回空 dict。

    English: Load the model → collection map of a scope; returns an empty dict when
    the file is missing or corrupt.
    """
    data = _read_json(base / MODELS_FILE)
    cols = data.get("collections") if isinstance(data, dict) else None
    if not isinstance(cols, dict):
        return {}
    return {str(k): str(v) for k, v in cols.items()
            if isinstance(k, str) and k and isinstance(v, str) and v}


def save_model_map(base: Path, mapping: dict[str, str]) -> None:
    """落盘模型映射（原子语义由单文件覆盖写近似保证）。"""
    _write_json(base / MODELS_FILE, {"collections": dict(mapping)})


def collection_for(base: Path, model: str) -> str:
    """取模型在该作用域的 collection；未登记则分配并落盘。

    第一个模型沿用 kb_records（兼容本特性之前的历史库），后续模型使用
    kb_records__<hash>，保证不同维度的向量永远不写进同一个 collection。
    English: Return the collection for a model, allocating and persisting one when
    missing. The first model keeps the historical name kb_records (so pre-v2.5
    libraries keep working); later models get kb_records__<hash> so vectors of
    different dimensions never share a collection.
    """
    mapping = load_model_map(base)
    if model in mapping:
        return mapping[model]
    name = (DEFAULT_COLLECTION if not mapping
            else f"{DEFAULT_COLLECTION}__{_model_key(model)}")
    mapping[model] = name
    save_model_map(base, mapping)
    return name


def collection_count(base: Path, collection: str) -> int:
    """读取某 collection 的记录数；目录/collection 不存在时返回 0。"""
    if not (base / "chroma").exists():
        return 0
    try:
        return ChromaStore(base / "chroma", collection).count()
    except Exception:  # noqa: BLE001 - 统计失败按空库处理，不影响主流程
        return 0


def validate_scope_name(project: str | None) -> str:
    """项目名必须是安全目录名；非法时抛 ValueError（防路径穿越）。
    English: A project name must be a safe directory name; raise ValueError otherwise."""
    name = (project or "").strip()
    if not name:
        return ""
    if not _PROJECT_RE.match(name) or name.upper() in _WINDOWS_RESERVED:
        raise ValueError(f"project 不是合法的目录名：{project!r}")
    return name


@dataclass
class Scope:
    """一个作用域的存储与检索组件；name 为空表示全局库。"""

    name: str
    base_dir: Path
    store: ChromaStore
    bm25: BM25Index
    retriever: HybridRetriever
    sparse_embedder: object | None = None
    sparse_index: object | None = None
    links: LinkStore | None = None
    model: str = ""

    @property
    def bm25_cache(self) -> Path:
        return self.base_dir / "bm25_corpus.json"


class ScopeManager:
    """全局库常驻 + 项目子库懒加载/LRU；写入锁由服务层统一注入。

    English: Global store always open; project stores lazy-loaded with LRU.
    """

    def __init__(self, settings, embedder, write_lock, reranker=None,
                 sparse_builder=None, global_store=None, global_records=None):
        self.settings = settings
        self.embedder = embedder
        self.write_lock = write_lock
        self.reranker = reranker
        self._sparse_builder = sparse_builder or (
            lambda records, base, force=False: (None, None))
        self._lock = threading.RLock()
        self._scopes: OrderedDict[str, Scope] = OrderedDict()
        self.global_scope = self._open("", store=global_store,
                                       records=global_records)
        self._scopes[""] = self.global_scope

    def base_dir(self, project: str | None) -> Path:
        """全局库用 data_dir，项目库用 data_dir/projects/<project>。"""
        name = validate_scope_name(project)
        return (self.settings.data_dir if not name
                else self.settings.projects_dir / name)

    def current_model(self) -> str:
        """当前生效的嵌入模型名（由服务层注入的 embedder 决定）。"""
        return str(getattr(self.embedder, "model_name", "") or "")

    def _open(self, project: str, store=None, records=None,
              refresh_sparse: bool = False) -> Scope:
        base = self.base_dir(project)
        base.mkdir(parents=True, exist_ok=True)
        model = self.current_model()
        if store is None:
            store = ChromaStore(base / "chroma", collection_for(base, model))
        records = list(store.iter_all()) if records is None else records
        bm25 = BM25Index()
        cache = base / "bm25_corpus.json"
        if not bm25.load_corpus(cache, [r.id for r in records]):
            bm25.rebuild(records)
            try:
                bm25.save_corpus(cache)
            except OSError as e:
                logging.getLogger("kb.scopes").warning(
                    "BM25 语料落盘失败（%s）: %s", base, e)
        bm25.set_meta(records)
        sparse_embedder, sparse_index = self._sparse_builder(
            records, base, refresh_sparse)
        retriever = HybridRetriever(
            store, bm25, self.embedder, settings=self.settings,
            reranker=self.reranker,
            sparse_embedder=sparse_embedder, sparse_index=sparse_index,
            write_lock=self.write_lock)
        _write_count(base, len(records))
        links = LinkStore(base / "links.db")
        return Scope(project or "", base, store, bm25, retriever,
                     sparse_embedder, sparse_index, links, model)

    def set_embedder(self, embedder) -> None:
        """仅替换嵌入器，不换 collection（保留给库为空/兼容调用）。

        English: Swap only the embedder without switching collections."""
        with self._lock:
            self.embedder = embedder
            for scope in self._scopes.values():
                scope.retriever.embedder = embedder

    def switch_model(self, embedder, previous_model: str = "") -> None:
        """切换当前模型并按新模型的 collection 重开所有已打开作用域。

        - 库为空时换模型：旧模型登记的 collection 直接让给新模型（不留下
          一堆空 collection）；
        - 迁移完成时换模型：新模型已有独立 collection，重开后 store/retriever
          指向新数据；BM25 语料按 id 校验后复用，稀疏索引强制重建（它依赖模型）。
        English: Switch the current model and reopen every open scope against the new
        model's collection. Empty libraries hand the old collection over to the new
        model; after a migration the new model already owns its collection, BM25 is
        reused (validated by ids) and the sparse index is rebuilt (it depends on the model).
        """
        with self._lock:
            self.embedder = embedder
            new_model = self.current_model()
            if previous_model and previous_model != new_model:
                self._rebind_empty(previous_model, new_model)
            for name in list(self._scopes):
                old = self._scopes[name]
                new_scope = self._open(name, refresh_sparse=True)
                if old.links is not None:
                    old.links.close()
                self._scopes[name] = new_scope
                if name == "":
                    self.global_scope = new_scope

    def _rebind_empty(self, previous: str, new: str) -> None:
        """把「记录数为 0」的旧模型 collection 转登记给新模型（空库换模型场景）。"""
        for name in [""] + self.project_names():
            base = self.base_dir(name)
            mapping = load_model_map(base)
            collection = mapping.get(previous)
            if not collection or collection_count(base, collection) > 0:
                continue
            mapping.pop(previous, None)
            mapping[new] = collection
            save_model_map(base, mapping)

    def model_usage(self) -> list[dict]:
        """列出各模型在各作用域的数据量（设置面板展示 / 删除旧模型用）。"""
        active = self.current_model()
        usage: dict[str, dict] = {}
        for name in [""] + self.project_names():
            base = self.base_dir(name)
            for model, collection in load_model_map(base).items():
                item = usage.setdefault(model, {
                    "model": model, "records": 0, "scopes": 0,
                    "active": model == active})
                n = collection_count(base, collection)
                item["records"] += n
                if n:
                    item["scopes"] += 1
        return sorted(usage.values(),
                      key=lambda m: (not m["active"], -m["records"], m["model"]))

    def delete_model_data(self, model: str) -> int:
        """删除某个非活跃模型在所有作用域中的数据，返回删除记录数。

        禁止删除当前模型：正在使用的 collection 一旦删掉，服务会立刻失去检索数据。
        English: Delete a non-active model's data in every scope, returning the number
        of records removed. The active model is rejected — deleting the collection in
        use would immediately strip retrieval of all data.
        """
        if not model:
            raise ValueError("模型名不能为空")
        if model == self.current_model():
            raise ValueError("不能删除当前正在使用的模型数据，请先迁移到其他模型")
        deleted = 0
        for name in [""] + self.project_names():
            base = self.base_dir(name)
            mapping = load_model_map(base)
            collection = mapping.get(model)
            if not collection:
                continue
            try:
                if (base / "chroma").exists():
                    store = ChromaStore(base / "chroma", collection)
                    n = store.count()
                    store.delete_collection()
                    deleted += n
            except Exception as e:  # noqa: BLE001 - 单个作用域失败不阻断其余
                logging.getLogger("kb.scopes").warning(
                    "删除模型数据失败（%s %s）: %s", base, collection, e)
                continue
            mapping.pop(model, None)
            save_model_map(base, mapping)
        return deleted

    def get(self, project: str | None) -> Scope:
        """取作用域；项目库首次访问时打开，超出上限淘汰最久未用的项目库。"""
        key = project or ""
        with self._lock:
            if key in self._scopes:
                self._scopes.move_to_end(key)
                return self._scopes[key]
            scope = self._open(key)
            self._scopes[key] = scope
            limit = 1 + max(0, int(self.settings.project_cache_size or 0))
            while len(self._scopes) > limit:
                for name in list(self._scopes):
                    if name:
                        evicted = self._scopes.pop(name)
                        _write_count(evicted.base_dir, evicted.store.count())
                        if evicted.links is not None:
                            evicted.links.close()
                        break
                else:
                    break
            return scope

    def project_names(self) -> list[str]:
        """磁盘上已有的项目名（不打开子库）。"""
        root = self.settings.projects_dir
        if not root.exists():
            return []
        names = []
        for p in root.iterdir():
            if not p.is_dir() or p.name.startswith("."):
                continue
            try:
                names.append(validate_scope_name(p.name))
            except ValueError:
                continue
        return sorted(names)

    def has_project(self, project: str | None) -> bool:
        """项目子库是否已存在（读路径不因查询而创建空目录）。"""
        if not project:
            return False
        try:
            name = validate_scope_name(project)
        except ValueError:
            return False
        return (self.settings.projects_dir / name).is_dir()

    def locate(self, record_id: str, project: str | None = None):
        """按 ID 定位记录所在作用域，返回 (scope, record) 或 (None, None)。

        显式 project：先项目库后全局；未指定：先全局后各项目库。
        """
        names = (([project or "", ""] if self.has_project(project) else [""])
                 if project else [""] + self.project_names())
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            scope = self.get(name)
            record = scope.store.get(record_id)
            if record is not None:
                return scope, record
        return None, None

    def sync_count(self, scope: Scope) -> None:
        """记录数变更后刷新 count.txt，避免未打开子库读到陈旧计数。

        English: Refresh count.txt after record changes so unopened sub-stores do not
        report stale counts."""
        _write_count(scope.base_dir, scope.store.count())

    def total_records(self) -> int:
        """全局 + 所有项目库的记录总数（用于 healthz/stats）。

        已打开的项目库用 Chroma 原生计数；未打开的读 count.txt（打开/淘汰时写入），
        避免为统计把所有项目库连同 BM25 一起加载。
        English: Total records across the global and project stores (for healthz/stats).
        Open scopes use native Chroma counts; unopened ones read count.txt (written on
        open/evict) so stats never force-load every project's BM25.
        """
        total = self.global_scope.store.count()
        for name in self.project_names():
            scope = self._scopes.get(name)
            if scope is not None:
                total += scope.store.count()
                continue
            try:
                total += int(_count_path(self.base_dir(name)).read_text(
                    encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                continue
        return total
