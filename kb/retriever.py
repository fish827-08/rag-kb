"""混合检索：向量 + BM25 双路，RRF 融合。"""
from kb.bm25 import BM25Index
from kb.embedder import Embedder

RRF_K = 60


def rrf_fuse(vector_hits: list[tuple[str, float]],
             keyword_hits: list[tuple[str, float]],
             top_k: int) -> list[tuple[str, float]]:
    """score(d) = Σ 1/(RRF_K + rank_i(d))，rank 从 1 起；按融合分降序取 top_k。"""
    scores: dict[str, float] = {}
    for ranked in (vector_hits, keyword_hits):
        for rank, (rid, _score) in enumerate(ranked, start=1):
            scores[rid] = scores.get(rid, 0.0) + 1 / (RRF_K + rank)
    ranked_fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked_fused[:top_k]


class HybridRetriever:
    """混合检索器；mode 支持 hybrid / vector / keyword。"""

    def __init__(self, store, bm25: BM25Index, embedder: Embedder):
        self.store = store
        self.bm25 = bm25
        self.embedder = embedder

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               type: str | None = None, tag: str | None = None) -> list[dict]:
        """mode: hybrid/vector/keyword；每路取 3*top_k 候选；
        type/tag 过滤在融合后进行（过滤后不足 top_k 属正常）；
        输出 [{id, content, score, type, source, tags, created_at}]，score 为融合分。"""
        candidate = 3 * top_k
        if mode in ("hybrid", "vector"):
            vec = self.embedder.embed_query(query)
            vector_hits = [(r.id, s) for r, s in self.store.query(vec, top_k=candidate)]
        else:
            vector_hits = []
        if mode in ("hybrid", "keyword"):
            keyword_hits = self.bm25.search(query, top_n=candidate)
        else:
            keyword_hits = []

        if mode == "hybrid":
            fused = rrf_fuse(vector_hits, keyword_hits, top_k)
        elif mode == "vector":
            fused = sorted(vector_hits, key=lambda x: x[1], reverse=True)[:top_k]
        else:  # keyword
            fused = sorted(keyword_hits, key=lambda x: x[1], reverse=True)[:top_k]

        results = []
        for rid, score in fused:
            rec = self.store.get(rid)
            if rec is None:
                continue
            if type and rec.type.value != type:
                continue
            if tag and tag not in rec.tags:
                continue
            results.append({
                "id": rec.id,
                "content": rec.content,
                "score": score,
                "type": rec.type.value,
                "source": rec.source,
                "tags": rec.tags,
                "created_at": rec.created_at,
            })
        return results