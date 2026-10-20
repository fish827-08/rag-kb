"""目录监听：watchdog 后台线程，文件创建/修改去抖入库，删除同步清理（设计文档第 9 节）。

- 仅监听 ingest.parse_file 支持的扩展名（白名单与解析分派保持一致）；
- 忽略临时文件：文件名以 ``~`` 开头（vim/Office 锁文件）或后缀为
  ``.tmp`` / ``.crdownload`` / ``.part``（编辑器与浏览器下载中的文件）；
- 创建/修改事件进入去抖队列：距离最近一次事件静默 debounce_seconds 后
  才调用 service.add_document（等待写入完成，避免读到半截文件）；
- 删除事件按文件名（即 source）级联删除记录（service.delete_document
  内部走 store.delete_by_source 并同步清理 BM25 索引）；
- 重命名视为"删旧 + 加新"（下载工具常见的 .crdownload → 正式名收尾）。
"""
import logging
import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from kb.ingest import _DIRECT_EXTS, _MARKITDOWN_EXTS

# 模块日志器：目录缺失等容错警告（不配置 handler，交由服务入口统一接管）
logger = logging.getLogger("kb.watcher")

# 支持入库的扩展名白名单（与 ingest.parse_file 的解析分派一致）
SUPPORTED_EXTS = _DIRECT_EXTS | {".pdf", ".docx"} | _MARKITDOWN_EXTS

# 临时文件后缀（下载中 / 编辑器交换文件收尾前的中间态）
_TEMP_EXTS = {".tmp", ".crdownload", ".part"}

# worker 线程轮询周期（秒）；去抖精度与删除响应时延均不超过该值
_POLL_INTERVAL = 0.1


def _is_temp_file(path: Path) -> bool:
    """临时文件判定：~ 开头（含 ~$ Office 锁文件）或临时后缀。"""
    name = path.name.lower()
    return name.startswith("~") or path.suffix.lower() in _TEMP_EXTS


def _is_supported(path: Path) -> bool:
    """支持的扩展名且非临时文件才纳入入库/清理范围。"""
    return path.suffix.lower() in SUPPORTED_EXTS and not _is_temp_file(path)


class KBWatcher:
    """目录监听器：watchdog Observer 采集事件 + 后台 worker 去抖处理。"""

    def __init__(self, service, watch_dir: Path, debounce_seconds: float = 2.0,
                 ignore_dir: Path | None = None):
        """service 为 KBService；watch_dir 为监听目录（仅顶层，不递归）。

        ignore_dir 为服务数据目录：与 watch_dir 重合时 start() 跳过监听，
        避免把 bm25_corpus.json 等数据文件反复入库（自我入库死循环）。
        """
        self.service = service
        self.watch_dir = Path(watch_dir)
        self.ignore_dir = Path(ignore_dir) if ignore_dir is not None else None
        self.debounce_seconds = debounce_seconds
        self._pending: dict[Path, float] = {}        # 待入库路径 → 最近一次事件时间
        self._deletes: queue.Queue[str] = queue.Queue()  # 待删除 source（文件名）
        self._lock = threading.Lock()                # 保护 _pending（事件回调与 worker 并发访问）
        self._stop = threading.Event()
        self._observer = None
        self._worker: threading.Thread | None = None

    # ---- 生命周期 ----
    def _overlaps_data_dir(self) -> bool:
        """监听目录是否就是数据目录（相对路径/符号链接解析后比较）。"""
        if self.ignore_dir is None:
            return False
        try:
            return self.watch_dir.resolve() == self.ignore_dir.resolve()
        except OSError:
            return False

    def start(self) -> None:
        """启动 watchdog Observer 与后台处理线程。

        不在启动时预热嵌入模型：空闲常驻保持轻量，模型在首次写入/检索
        （含首个文件事件的入库）时才加载；一次加载会被后续事件复用。

        容错（V2-0）：监听目录不存在（或不是目录）时记 info 并直接跳过
        监听——服务其余功能不受影响，serve 不因此崩溃；默认目录 data/
        在全新安装时通常不存在，用 warning 会误导新用户以为启动失败。
        此刻不启动 Observer 与 worker 线程，stop() 对未启动状态安全。
        """
        if not self.watch_dir.is_dir():
            logger.info("监听目录不存在或不可用，跳过目录监听：%s",
                        self.watch_dir)
            return
        if self._overlaps_data_dir():
            logger.warning(
                "监听目录与数据目录相同（%s），已跳过目录监听以避免自我入库；"
                "请把 KB_WATCH_DIR 指向数据目录之外", self.watch_dir)
            return
        self._observer = Observer()
        self._observer.schedule(_EventHandler(self), str(self.watch_dir),
                                recursive=False)
        self._observer.start()
        self._worker = threading.Thread(target=self._run, name="kb-watcher",
                                        daemon=True)
        self._worker.start()

    def stop(self) -> None:
        """停止 Observer 与 worker 线程并等待退出（各至多 5s）。"""
        self._stop.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
        if self._worker is not None:
            self._worker.join(timeout=5)

    # ---- 事件入口（由 _EventHandler 调用，Observer 线程内执行，只做入队）----
    def _mark_created(self, path: Path) -> None:
        """创建/修改：记入去抖队列，刷新静默计时起点。"""
        if not _is_supported(path):
            return
        with self._lock:
            self._pending[path] = time.monotonic()

    def _mark_deleted(self, path: Path) -> None:
        """删除：按文件名（source）入删除队列。"""
        if not _is_supported(path):
            return
        self._deletes.put(path.name)

    def _mark_moved(self, src: Path, dest: Path) -> None:
        """重命名：旧名入删除队列，新名走去抖入库。"""
        self._mark_deleted(src)
        self._mark_created(dest)

    # ---- 后台处理 ----
    def _run(self) -> None:
        """worker 主循环：周期性处理删除队列与到期的去抖条目。"""
        while not self._stop.wait(_POLL_INTERVAL):
            self._flush_deletes()
            self._flush_pending()

    def _flush_deletes(self) -> None:
        """清空删除队列：按 source 级联删除（含 BM25 同步清理）。"""
        while True:
            try:
                name = self._deletes.get_nowait()
            except queue.Empty:
                return
            try:
                self.service.delete_document(name)
            except Exception:
                pass  # 单文件清理失败不拖垮监听线程

    def _flush_pending(self) -> None:
        """处理静默期满的去抖条目：文件仍在且是普通文件才入库。"""
        now = time.monotonic()
        with self._lock:
            due = [p for p, t in self._pending.items()
                   if now - t >= self.debounce_seconds]
            for p in due:
                del self._pending[p]
        for p in due:
            if not p.is_file():
                continue  # 去抖期间被删除 → 交给删除事件；目录 → 跳过
            try:
                self.service.add_document(p)
            except Exception:
                pass  # 解析/嵌入失败（如损坏文件）不拖垮监听线程


class _EventHandler(FileSystemEventHandler):
    """watchdog 事件适配器：分派到 KBWatcher 的三个入口（Observer 线程内执行）。"""

    def __init__(self, watcher: KBWatcher):
        self._watcher = watcher

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._watcher._mark_created(Path(event.src_path))

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._watcher._mark_created(Path(event.src_path))

    def on_deleted(self, event) -> None:
        if not event.is_directory:
            self._watcher._mark_deleted(Path(event.src_path))

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._watcher._mark_moved(Path(event.src_path),
                                      Path(event.dest_path))
