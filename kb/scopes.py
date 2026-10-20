"""项目/全局作用域管理：每个项目一个独立 Chroma + BM25 子库。

English: Project/global scope manager: each project gets its own Chroma + BM25 store.
"""
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from kb.bm25 import BM25Index
from kb.retriever import HybridRetriever
from kb.storage import ChromaStore

_PROJECT_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}$")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _count_path(base_dir: Path) -> Path:
    return base_dir / "count.txt"


def _write_count(base_dir: Path, count: int) -> None:
    try:
        _count_path(base_dir).write_text(str(count), encoding="utf-8")
    except OSError:
        logging.getLogger("kb.scopes").warning(
            "记录计数落盘失败（%s）", base_dir)


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
        self._sparse_builder = sparse_builder or (lambda records, base: (None, None))
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

    def _open(self, project: str, store=None, records=None) -> Scope:
        base = self.base_dir(project)
        base.mkdir(parents=True, exist_ok=True)
        store = store or ChromaStore(base / "chroma")
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
        sparse_embedder, sparse_index = self._sparse_builder(records, base)
        retriever = HybridRetriever(
            store, bm25, self.embedder, settings=self.settings,
            reranker=self.reranker,
            sparse_embedder=sparse_embedder, sparse_index=sparse_index,
            write_lock=self.write_lock)
        _write_count(base, len(records))
        return Scope(project or "", base, store, bm25, retriever,
                     sparse_embedder, sparse_index)

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
