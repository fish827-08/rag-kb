"""嵌入器：嵌入模型延迟加载 + 设备支持 + 归一化向量输出（实例化不加载模型）。

English: Embedder: lazy-loaded embedding model, device support, normalized vectors
(construction never loads the model).
"""
import gc
import math
import os
import sys
import threading
import time
from pathlib import Path


def _cgroup_cpu_limit() -> int | None:
    """读取 cgroup v2/v1 的 CPU 配额并向上取整；无限制或不可读返回 None。

    容器内 os.cpu_count() 仍是宿主机核数；不看配额会在 2 核容器里按
    宿主机核数开线程，互相抢占。
    """
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(
            encoding="utf-8").split()[:2]
        if quota != "max" and int(period) > 0:
            return max(1, math.ceil(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text(
            encoding="utf-8"))
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text(
            encoding="utf-8"))
        if quota > 0 and period > 0:
            return max(1, math.ceil(quota / period))
    except (OSError, ValueError):
        pass
    return None


def available_cpus() -> int:
    """可用 CPU 核数：affinity 与 cgroup 配额取小；都读不到时回退 os.cpu_count。"""
    limits = []
    try:
        limits.append(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        pass
    cgroup = _cgroup_cpu_limit()
    if cgroup:
        limits.append(cgroup)
    if not limits:
        limits.append(os.cpu_count() or 1)
    return max(1, min(limits))


def resolve_torch_threads(configured: int = 0,
                          cpus: int | None = None) -> int:
    """解析 CPU 线程数：显式值优先；0=自动，按可用核数且最多 4。

    小模型（bge-small）超过 4 线程收益很小，线程过多反而增加调度抖动。
    """
    if configured and configured > 0:
        return configured
    n = cpus if cpus and cpus > 0 else available_cpus()
    return max(1, min(n, 4))


def _installed_version(package: str) -> str:
    """读取已安装分发包的版本号；读不到（含未安装）返回「未安装」。"""
    try:
        from importlib.metadata import version
        return version(package) or "未安装"
    except Exception:  # noqa: BLE001 - 版本读不出来不能反过来影响判定
        return "未安装"


def _is_torch_backend_disabled() -> bool | None:
    """transformers 是否禁用了 PyTorch 后端：True=已禁用，False=可用，None=无法判定。

    不同 transformers 版本暴露的判定函数位置不同，取不到时返回 None，
    让调用方退回原有报错路径，不引入新的误报。
    """
    try:
        from transformers.utils import is_torch_available
        return not bool(is_torch_available())
    except Exception:  # noqa: BLE001 - 判定失败不阻断加载
        return None


def torch_backend_issue() -> str:
    """加载模型前判定 torch 后端是否被 transformers 禁用；有问题返回可读根因，否则空串。

    transformers 检测到 torch 版本不满足自身要求时会静默禁用 PyTorch 后端，
    sentence-transformers 随后在裸 torch 符号上抛 NameError: name 'nn' is not
    defined，把真正的根因（版本不兼容）盖在「嵌入模型不可用」下面。
    """
    if not _is_torch_backend_disabled():
        return ""
    torch_version = _installed_version("torch")
    transformers_version = _installed_version("transformers")
    if torch_version == "未安装":
        return (f"torch 未安装：嵌入模型需要 PyTorch 后端（transformers "
                f"{transformers_version}）；先 pip install torch（CPU 版即可）再重试")
    return (f"torch 版本不满足 transformers 要求：torch {torch_version} + "
            f"transformers {transformers_version} → transformers 已自动禁用 "
            "PyTorch 后端（底层表现常为 NameError: name 'nn' is not defined）；"
            "升级 torch 或换用与当前 torch 匹配的 transformers 版本后重试")


class EmbeddingUnavailableError(RuntimeError):
    """嵌入模型不可用（下载/加载/推理失败）；API 层据此返回 503 结构化错误。
    English: Embedding model unavailable (download/load/encode failure); the API layer
    maps this to a structured 503."""

    def __init__(self, model_name: str, cause: str):
        self.model_name = model_name
        self.cause = cause
        super().__init__(f"嵌入模型不可用：{model_name}（{cause}）")


class Embedder:
    """SentenceTransformer 封装；构造只存参数，首次 embed 才加载模型。

    English: SentenceTransformer wrapper; the constructor only stores parameters,
    and the model is loaded on the first embed call.
    """

    def __init__(self, model_name: str, device: str = "cpu",
                 idle_unload_seconds: int = 0, torch_threads: int = 0,
                 embed_batch_size: int = 32):
        """只存参数不加载模型；_model 延迟到首次使用。
        English: Store parameters only; defer loading until first use."""
        self.model_name = model_name
        self.device = device
        self.idle_unload_seconds = max(0, int(idle_unload_seconds or 0))
        self.torch_threads = max(0, int(torch_threads or 0))
        self.embed_batch_size = max(1, int(embed_batch_size or 32))
        self._model = None
        self._state = "unloaded"   # unloaded | loading | ok | error
        self._error = ""
        self._last_used = time.monotonic()
        self._lock = threading.RLock()

    def status(self) -> dict:
        """返回可放进 healthz 的轻量状态（不触发加载）。
        English: Return a lightweight status suitable for healthz (never triggers loading)."""
        return {"state": self._state, "model": self.model_name,
                "error": self._error,
                "idle_unload_seconds": self.idle_unload_seconds,
                "torch_threads": self.torch_threads,
                "embed_batch_size": self.embed_batch_size}

    def _mark_error(self, exc: Exception) -> EmbeddingUnavailableError:
        """记录失败状态并包装成统一异常。
        English: Record the failure state and wrap it in the unified exception."""
        self._state = "error"
        self._error = str(exc)[:300]
        return EmbeddingUnavailableError(self.model_name, self._error)

    def _ensure_loaded(self):
        """首次使用时加载模型；先做 torch 后端预检，再优先离线（缓存命中，不联网），失败才在线下载；cuda 下 fp16。

        加载失败时状态置 error，并抛 EmbeddingUnavailableError（不把底层
        OSError/HTTPError 裸抛给调用方）。
        English: Load the model on first use — pre-flight the torch backend, then try
        offline (cache hit, no network) before an online download; fp16 under cuda. On
        failure the state becomes error and EmbeddingUnavailableError is raised instead
        of leaking OSError/HTTPError.
        """
        with self._lock:
            if self._model is not None:
                self._last_used = time.monotonic()
                return
            self._state = "loading"
            try:
                issue = torch_backend_issue()
                if issue:
                    raise RuntimeError(issue)
                from sentence_transformers import SentenceTransformer
                if self.device == "cpu":
                    self._configure_cpu_threads()
                try:
                    self._model = SentenceTransformer(
                        self.model_name, device=self.device, local_files_only=True)
                except Exception:
                    # 缓存未命中（首次部署新模型），走在线下载；HF_ENDPOINT 镜像由环境变量提供
                    self._model = SentenceTransformer(self.model_name, device=self.device)
                if self.device == "cuda":
                    self._model.half()
                self._state = "ok"
                self._error = ""
                self._last_used = time.monotonic()
            except Exception as exc:  # noqa: BLE001 - 统一转换为可读错误
                raise self._mark_error(exc) from exc

    def _configure_cpu_threads(self) -> None:
        """加载前固定 CPU 线程数，避免容器按宿主机核数开线程。

        set_num_interop_threads 只能在并行工作开始前调用一次；已初始化时
        忽略 RuntimeError（torch.set_num_threads 仍会生效）。
        """
        threads = resolve_torch_threads(self.torch_threads)
        import torch
        torch.set_num_threads(threads)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass

    def unload(self) -> bool:
        """卸载模型释放内存；未加载时返回 False。
        English: Unload the model to free memory; returns False when not loaded."""
        with self._lock:
            if self._model is None:
                return False
            self._model = None
            self._state = "unloaded"
            self._error = ""
            gc.collect()
            if sys.platform.startswith("linux"):
                # glibc 默认不把释放的大块内存还给系统；2G 机器上显式归还
                try:
                    import ctypes
                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                except (OSError, AttributeError):
                    pass
            if self.device == "cuda":
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001 - 释放显存失败不影响卸载
                    pass
            return True

    def unload_if_idle(self, now: float | None = None) -> bool:
        """空闲超过阈值时卸载模型；返回是否真正卸载。
        English: Unload when idle beyond the threshold; returns whether it actually unloaded."""
        if self.idle_unload_seconds <= 0 or self._model is None:
            return False
        now = time.monotonic() if now is None else now
        if now - self._last_used < self.idle_unload_seconds:
            return False
        return self.unload()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量编码，normalize_embeddings=True（单位向量）。
        English: Batch encode with normalize_embeddings=True (unit vectors)."""
        try:
            with self._lock:
                self._ensure_loaded()
                vecs = self._model.encode(
                    texts, normalize_embeddings=True, convert_to_numpy=True,
                    batch_size=self.embed_batch_size
                )
                self._last_used = time.monotonic()
        except EmbeddingUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - 推理失败同样视为模型不可用
            raise self._mark_error(exc) from exc
        return [v.tolist() for v in vecs]

    def embed_query(self, query: str) -> list[float]:
        """单条查询编码，同上归一化。
        English: Encode a single query with the same normalization."""
        return self.embed_texts([query])[0]
