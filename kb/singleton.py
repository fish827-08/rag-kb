"""数据目录单例锁：同一个 KB_DATA_DIR 只允许一个长驻进程持有。

为什么锁在数据目录而不是端口：
    共享资源是 Chroma / BM25 语料 / 关联边库这些文件，不是监听端口。
    两个进程用不同端口写同一份数据，仍然会并发写坏索引。

为什么用操作系统文件锁而不是 PID 文件：
    PID 文件在进程崩溃后会残留，PID 还可能被系统复用；OS 文件锁由内核
    在进程退出（含强杀）时自动释放，不存在陈旧锁的问题。
    PID 只写进锁文件内容，用于给人看的错误提示。

English: Data-directory singleton lock. The shared resource is the data directory
(Chroma / BM25 corpus / link database), not the listening port, so the lock is keyed
by data_dir. An OS-level advisory lock is used instead of a PID file because the
kernel releases it automatically when the process exits or is killed; the PID is
stored only for human-readable diagnostics.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

LOCK_FILE = "kb.lock"
INFO_FILE = "kb.lock.info"


def lock_path(data_dir: str | Path) -> Path:
    """锁文件路径（data_dir/kb.lock）。"""
    return Path(data_dir) / LOCK_FILE


def info_path(data_dir: str | Path) -> Path:
    """持有者信息文件（与锁文件分离）。

    Windows 的字节锁会阻止其他进程读取被锁区间，所以不能把 JSON 写进锁文件；
    信息单独放一个文件，探测方可以在对方持锁时正常读取。
    """
    return Path(data_dir) / INFO_FILE


def read_holder(data_dir: str | Path) -> dict | None:
    """读取持有者信息；文件缺失/损坏返回 None（不代表未被持有）。"""
    path = info_path(data_dir)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class DataDirLock:
    """跨进程独占锁；acquire 成功后须在进程生命周期内一直持有。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = lock_path(data_dir)
        self._fh = None

    def acquire(self, info: dict) -> dict | None:
        """尝试加锁。

        成功返回 None，并把 info 写入锁文件；
        失败返回当前持有者信息（可能为空 dict，表示有进程持锁但尚未写入信息）。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh = open(self.path, "r+b")
        except FileNotFoundError:
            fh = open(self.path, "w+b")
        try:
            self._lock(fh)
        except OSError:
            fh.close()
            return read_holder(self.data_dir) or {}
        try:
            payload = json.dumps(info, ensure_ascii=False)
            target = info_path(self.data_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
        except OSError:
            pass
        self._fh = fh
        return None

    @staticmethod
    def _lock(fh) -> None:
        """非阻塞独占锁；已被占用时抛 OSError。"""
        if os.name == "nt":
            import msvcrt
            # Windows 的 msvcrt.locking 按字节区间加锁，先保证文件至少有 1 字节
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"\n")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(fh) -> None:
        """释放锁；失败忽略（关闭文件同样会释放）。"""
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    def release(self) -> None:
        """释放并关闭锁文件；可重复调用。"""
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        self._unlock(fh)
        try:
            fh.close()
        except OSError:
            pass


def is_locked(data_dir: str | Path) -> dict | None:
    """探测数据目录是否被某个长驻进程持有（不会干扰持有者）。

    返回持有者信息；未被持有时返回 None。实现方式是尝试抢一次非阻塞锁：
    抢到说明没人持有，立刻释放；抢不到说明有进程正在运行。
    English: Probe whether a long-lived process holds the data directory, without
    disturbing it. Returns the holder info, or None when the directory is free.
    """
    path = lock_path(data_dir)
    if not path.exists():
        return None
    try:
        fh = open(path, "r+b")
    except OSError:
        return None
    try:
        DataDirLock._lock(fh)
    except OSError:
        info = read_holder(data_dir) or {}
        try:
            fh.close()
        except OSError:
            pass
        return info
    try:
        DataDirLock._unlock(fh)
    finally:
        try:
            fh.close()
        except OSError:
            pass
    return None
