"""嵌入器：BGE-M3 延迟加载 + 设备支持 + 归一化向量输出（实例化不加载模型）。"""


class Embedder:
    """SentenceTransformer 封装；构造只存参数，首次 embed 才加载模型。"""

    def __init__(self, model_name: str, device: str = "cpu"):
        """只存参数不加载模型；_model 延迟到首次使用。"""
        self.model_name = model_name
        self.device = device
        self._model = None

    def _ensure_loaded(self):
        """首次使用时加载模型；cuda 设备下以 fp16 加载以省显存。"""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device=self.device)
            if self.device == "cuda":
                self._model.half()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量编码，normalize_embeddings=True（单位向量）。"""
        self._ensure_loaded()
        vecs = self._model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True
        )
        return [v.tolist() for v in vecs]

    def embed_query(self, query: str) -> list[float]:
        """单条查询编码，同上归一化。"""
        return self.embed_texts([query])[0]