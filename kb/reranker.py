"""Cross-encoder 精排：bge-reranker-v2-m3 懒加载（实例化不加载模型，A3.5 N24）。
English: Cross-encoder reranking with lazy-loaded bge-reranker-v2-m3 (no model load at construction, A3.5 N24)."""
import logging

logger = logging.getLogger("kb.reranker")


class Reranker:
    """CrossEncoder 封装；构造只存参数，首次 rerank 才加载模型（同 Embedder 模式）。

    rerank 失败（模型加载/打分异常）降级为原顺序截断返回，不中断检索。
    English: CrossEncoder wrapper; construction only stores params and loads the model on first rerank
    (same pattern as Embedder). On rerank failure (model load/scoring error) degrade to returning the
    original-order truncation, without interrupting retrieval."""

    def __init__(self, model_name: str, device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _ensure_loaded(self):
        """首次使用时加载；优先离线缓存，失败在线下载；cuda 下 fp16。
        English: Load on first use; prefer the offline cache and fall back to an online download; fp16 under cuda."""
        if self._model is None:
            from sentence_transformers import CrossEncoder
            try:
                self._model = CrossEncoder(
                    self.model_name, device=self.device,
                    max_length=512, local_files_only=True)
            except Exception:
                self._model = CrossEncoder(
                    self.model_name, device=self.device, max_length=512)
            if self.device == "cuda":
                self._model.half()

    def rerank(self, query: str, candidates: list[dict],
               top_k: int) -> list[dict]:
        """对候选列表交叉打分并按新分数降序取 top_k。

        candidates: [{id, content, ...}]；返回副本含 rerank_score 字段。
        异常降级：原顺序截断返回（记 WARNING，检索不中断）。
        English: Cross-score the candidate list and take the top_k by new score descending.
        candidates: [{id, content, ...}]; returns copies with a rerank_score field.
        On error degrade to the original-order truncation (logs a WARNING; retrieval is not interrupted)."""
        if not candidates:
            return []
        try:
            self._ensure_loaded()
            pairs = [(query, c["content"]) for c in candidates]
            scores = self._model.predict(pairs)
            enriched = []
            for c, s in zip(candidates, scores):
                d = dict(c)
                d["rerank_score"] = float(s)
                enriched.append(d)
            ranked = sorted(enriched, key=lambda x: x["rerank_score"],
                            reverse=True)
            return ranked[:top_k]
        except Exception as e:
            logger.warning("rerank 失败，降级原顺序截断: %s", e)
            return [dict(c) for c in candidates[:top_k]]
