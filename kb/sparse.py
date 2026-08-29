"""BGE-M3 稀疏向量：sparse 头直载 + 倒排索引（N25，A3.5 spec §3.2）。

SparseEmbedder 复用 Embedder 的 SentenceTransformer 底层编码器
（`model[0].auto_model` + `.tokenizer`），只额外加载 sparse_linear.pt
（Linear(1024,1)，几 KB）——显存零增加（共享编码器）。

稀疏语义对齐 FlagEmbedding BGEM3：
  h = encoder(**tokens).last_hidden_state        # (b, seq, hidden)
  w = relu(sparse_linear(h)).squeeze(-1)          # (b, seq)
  w = w * attention_mask                          # 屏蔽 padding
  按 input_id 聚合：同 id 多位置取 max → {token_id: weight}
  每条向量 L2 归一化（查询与文档同规则；归一化后点积即余弦）

降级链：结构探测失败 / sparse_linear.pt 缺失 / 加载异常 →
SparseUnavailableError，service 捕获后记 WARNING 并关闭稀疏路
（检索退回双路，行为等同 sparse_enabled=false）。

English: BGE-M3 sparse vectors: sparse head directly loaded plus an inverted index (N25, A3.5 spec §3.2).
SparseEmbedder reuses the SentenceTransformer encoder of Embedder (`model[0].auto_model` + `.tokenizer`),
loading only sparse_linear.pt (Linear(1024,1), a few KB) — zero additional VRAM (shared encoder).
Degradation chain: structural probe failure / missing sparse_linear.pt / load failure →
SparseUnavailableError; service logs a WARNING and disables the sparse route
(retrieval falls back to dual-route, same as sparse_enabled=false).
"""
import json
import logging
import math
from pathlib import Path

logger = logging.getLogger("kb.sparse")


class SparseUnavailableError(Exception):
    """稀疏编码不可用（非 BGE-M3 族模型 / sparse 头缺失 / 加载异常）。
    English: Sparse encoding unavailable (non-BGE-M3 family model / missing sparse head / load error)."""


def aggregate_sparse(input_ids: list[int], weights: list[float]) -> dict[int, float]:
    """同 token_id 多位置取 max 聚合 + L2 归一化（纯函数，可单测）。

    参数：
        input_ids: 一条文本的全部 token id（含特殊 token，对齐 FlagEmbedding）
        weights: 与 input_ids 等长的稀疏权重（relu 后，padding 已置 0）

    返回：
        {token_id: weight}（L2 归一化）；全零/空输入返回空 dict。
    English: Aggregate by taking the max across multiple positions per token_id plus L2 normalization (pure, unit-testable).
    Args: input_ids = all token ids of one text (incl. special tokens, aligned with FlagEmbedding);
    weights = sparse weights (post-relu, padding zeroed) of the same length as input_ids.
    Returns: {token_id: weight} (L2-normalized); an all-zero/empty input returns an empty dict."""
    vec: dict[int, float] = {}
    for tid, w in zip(input_ids, weights):
        if w > (vec.get(tid) or 0.0):
            vec[tid] = float(w)
    # 过滤非正权重（relu 后 ≤0 的 token 无意义）并 L2 归一化
    vec = {tid: w for tid, w in vec.items() if w > 0.0}
    norm = math.sqrt(sum(w * w for w in vec.values()))
    if norm <= 0.0:
        return {}
    return {tid: w / norm for tid, w in vec.items()}


def _download_sparse_linear(model_name: str) -> Path:
    """下载 sparse_linear.pt（优先离线缓存，失败在线；HF_ENDPOINT 镜像由环境提供）。
    English: Download sparse_linear.pt (prefer offline cache, fall back to online; the HF_ENDPOINT mirror is provided by the environment)."""
    from huggingface_hub import hf_hub_download
    try:
        return Path(hf_hub_download(model_name, "sparse_linear.pt",
                                    local_files_only=True))
    except Exception:
        return Path(hf_hub_download(model_name, "sparse_linear.pt"))


class SparseEmbedder:
    """BGE-M3 稀疏编码：复用 Embedder 底层编码器 + 独立加载 sparse 头。

    构造只存参数；首次 encode 触发 _ensure_loaded（结构探测 + sparse 头加载），
    任一步失败抛 SparseUnavailableError（调用方降级双路）。
    English: BGE-M3 sparse encoding: reuse the Embedder bottom encoder plus an independently loaded sparse head.
    Construction only stores params; the first encode triggers _ensure_loaded (structural probe + sparse head
    load); failure at any step raises SparseUnavailableError (caller degrades to dual-route)."""

    def __init__(self, embedder, model_name: str):
        self.embedder = embedder
        self.model_name = model_name
        self._encoder = None      # Transformer 底座（XLMRobertaModel）
        self._tokenizer = None
        self._sparse_linear = None
        self._device = None

    def _ensure_loaded(self):
        """结构探测 + sparse 头加载；幂等（成功后早退）。
        English: Structural probe plus sparse head load; idempotent (early-returns once loaded)."""
        if self._sparse_linear is not None:
            return
        # 1) 触发 Embedder 加载（共享同一模型实例，不二次加载 2GB 编码器）
        self.embedder._ensure_loaded()
        st_model = self.embedder._model
        # 2) 结构探测：SentenceTransformer 首模块 Transformer 的 auto_model + tokenizer
        try:
            first = st_model[0]
            encoder = getattr(first, "auto_model", None)
            tokenizer = getattr(st_model, "tokenizer", None)
        except (IndexError, TypeError):
            encoder = None
        if encoder is None or tokenizer is None:
            raise SparseUnavailableError(
                "底层模型结构不支持稀疏头探测（非 BGE-M3 族 SentenceTransformer）")
        # 3) 加载 sparse 头（Linear(hidden, 1)）
        import torch
        from torch import nn
        try:
            linear_path = _download_sparse_linear(self.model_name)
            state = torch.load(str(linear_path), map_location="cpu")
        except Exception as e:
            raise SparseUnavailableError(f"sparse_linear.pt 加载失败: {e}")
        if not isinstance(state, dict) or "weight" not in state:
            raise SparseUnavailableError("sparse_linear.pt 格式不符（非 Linear state_dict）")
        hidden = encoder.config.hidden_size
        linear = nn.Linear(hidden, state["weight"].shape[0])
        # 兼容带 "linear." 前缀的 key（FlagEmbedding 两种存档格式）
        state = {k.replace("linear.", ""): v for k, v in state.items()}
        linear.load_state_dict(state)
        linear.eval()
        # 编码器在哪个设备稀疏头就跟到哪（fp16 编码器下权重同步 half）
        device = next(encoder.parameters()).device
        dtype = next(encoder.parameters()).dtype
        linear = linear.to(device=device, dtype=dtype)
        self._encoder = encoder
        self._tokenizer = tokenizer
        self._sparse_linear = linear
        self._device = device

    def encode(self, texts: list[str]) -> list[dict[int, float]]:
        """批量稀疏编码；返回 [{token_id: weight}]（每条已 L2 归一化）。
        English: Batch sparse encoding; returns [{token_id: weight}] (each L2-normalized)."""
        if not texts:
            return []
        self._ensure_loaded()
        import torch
        encoded = self._tokenizer(
            list(texts), padding=True, truncation=True, max_length=512,
            return_tensors="pt").to(self._device)
        with torch.no_grad():
            h = self._encoder(**encoded).last_hidden_state      # (b, seq, hidden)
            w = torch.relu(self._sparse_linear(h)).squeeze(-1)  # (b, seq)
            w = w * encoded["attention_mask"]                   # 屏蔽 padding
        w = w.float().cpu()
        input_ids = encoded["input_ids"].cpu()
        return [aggregate_sparse(input_ids[b].tolist(), w[b].tolist())
                for b in range(len(texts))]


class SparseIndex:
    """稀疏倒排索引：{token_id: {record_id: weight}}，增量维护（无重建成本）。

    打分（归一化后点积即余弦）：
        score(q, d) = Σ_{tid ∈ q∩d} q_w[tid] × d_w[tid]
    持久化：{record_id: {token_id: weight}} JSON（同 BM25 模式，N27）。
    English: Sparse inverted index: {token_id: {record_id: weight}}, incrementally maintained (no rebuild cost).
    Scoring (dot product after normalization equals cosine): score(q, d) = Σ_{tid ∈ q∩d} q_w[tid] × d_w[tid].
    Persistence: {record_id: {token_id: weight}} JSON (same as the BM25 corpus mode, N27)."""

    def __init__(self) -> None:
        self._vecs: dict[str, dict[int, float]] = {}     # 正排（持久化用）
        self._inverted: dict[int, dict[str, float]] = {}  # 倒排（检索用）

    def add(self, record_id: str, sparse_vec: dict[int, float]) -> None:
        """新增/覆盖一条稀疏向量（增量维护倒排）。
        English: Add/overwrite a sparse vector (incremental inverted-index maintenance)."""
        self.remove(record_id)
        self._vecs[record_id] = sparse_vec
        for tid, w in sparse_vec.items():
            self._inverted.setdefault(tid, {})[record_id] = w

    def remove(self, record_id: str) -> None:
        """删除一条记录（增量清理倒排，幂等）。
        English: Remove a record (incremental inverted-index cleanup, idempotent)."""
        old = self._vecs.pop(record_id, None)
        if not old:
            return
        for tid in old:
            posting = self._inverted.get(tid)
            if posting is not None:
                posting.pop(record_id, None)
                if not posting:
                    del self._inverted[tid]

    def rebuild(self, items) -> None:
        """全量重建：items 为 [(record_id, sparse_vec)] 迭代器。
        English: Full rebuild: items is an iterable of [(record_id, sparse_vec)]."""
        self._vecs = {}
        self._inverted = {}
        for rid, vec in items:
            self.add(rid, vec)

    def search(self, query_vec: dict[int, float],
               top_n: int = 10) -> list[tuple[str, float]]:
        """倒排点积打分，返回 (record_id, score) 降序 top_n。
        English: Score via inverted dot product, returning (record_id, score) top_n descending."""
        scores: dict[str, float] = {}
        for tid, qw in query_vec.items():
            for rid, dw in self._inverted.get(tid, {}).items():
                scores[rid] = scores.get(rid, 0.0) + qw * dw
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_n]

    # ---- 持久化（同 BM25 语料模式，N27）----

    def save(self, path) -> None:
        """ {record_id: {token_id: weight}} JSON 落盘。
        English: Persist {record_id: {token_id: weight}} as JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {rid: {str(tid): w for tid, w in vec.items()}
                for rid, vec in self._vecs.items()}
        path.write_text(json.dumps(data, ensure_ascii=False),
                        encoding="utf-8")

    def load(self, path, valid_ids) -> bool:
        """加载持久化索引；id 集合与库一致才生效，否则 False（调用方全量重建）。

        文件缺失 / JSON 损坏 / id 集合漂移均返回 False（安全降级为重建）。
        English: Load the persisted index; apply only when the id set matches the library exactly, else False
        (caller does a full rebuild). A missing file / corrupt JSON / drifted id set all return False."""
        path = Path(path)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return False
        if not isinstance(data, dict) or set(data.keys()) != set(valid_ids):
            return False
        items = [(rid, {int(tid): float(w) for tid, w in vec.items()})
                 for rid, vec in data.items()]
        self.rebuild(items)
        return True
