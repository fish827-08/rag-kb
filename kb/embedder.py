"""嵌入器：嵌入模型延迟加载 + 设备支持 + 归一化向量输出（实例化不加载模型）。

English: Embedder: lazy-loaded embedding model, device support, normalized vectors
(construction never loads the model).
"""
import gc
import threading
import time


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
                 idle_unload_seconds: int = 0):
        """只存参数不加载模型；_model 延迟到首次使用。
        English: Store parameters only; defer loading until first use."""
        self.model_name = model_name
        self.device = device
        self.idle_unload_seconds = max(0, int(idle_unload_seconds or 0))
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
                "idle_unload_seconds": self.idle_unload_seconds}

    def _mark_error(self, exc: Exception) -> EmbeddingUnavailableError:
        """记录失败状态并包装成统一异常。
        English: Record the failure state and wrap it in the unified exception."""
        self._state = "error"
        self._error = str(exc)[:300]
        return EmbeddingUnavailableError(self.model_name, self._error)

    def _ensure_loaded(self):
        """首次使用时加载模型；优先离线（缓存命中，不联网），失败再在线下载；cuda 下 fp16。

        加载失败时状态置 error，并抛 EmbeddingUnavailableError（不把底层
        OSError/HTTPError 裸抛给调用方）。
        English: Load the model on first use — offline first (cache hit, no network),
        then online download; fp16 under cuda. On failure the state becomes error and
        EmbeddingUnavailableError is raised instead of leaking OSError/HTTPError.
        """
        with self._lock:
            if self._model is not None:
                self._last_used = time.monotonic()
                return
            self._state = "loading"
            try:
                from sentence_transformers import SentenceTransformer
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
                    texts, normalize_embeddings=True, convert_to_numpy=True
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
