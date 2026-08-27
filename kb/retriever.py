"""混合检索：向量 + BM25 双路，RRF 融合。"""
from datetime import datetime

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

    def __init__(self, store, bm25: BM25Index, embedder: Embedder, settings=None):
        self.store = store
        self.bm25 = bm25
        self.embedder = embedder
        self.settings = settings  # TASK-0068：衰减配置（decay_enabled/lambda/gamma），None=不应用衰减

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
            fused = rrf_fuse(vector_hits, keyword_hits, candidate)
        elif mode == "vector":
            fused = sorted(vector_hits, key=lambda x: x[1], reverse=True)[:candidate]
        else:  # keyword（BM25 不受衰减影响，A3 spec §3.1）
            fused = sorted(keyword_hits, key=lambda x: x[1], reverse=True)[:top_k]

        # 衰减应用点（TASK-0068，A3 spec §3.1）：仅 hybrid/vector 模式，BM25 不受影响
        # 默认 decay_enabled=false 零行为变化；开启时对 RRF/向量融合分应用访问频率衰减
        if (self.settings is not None and getattr(self.settings, "decay_enabled", False)
                and mode != "keyword"):
            from kb.governance import apply_decay, compute_decay_factor, days_since
            now = datetime.now()
            rescored = []
            for rid, score in fused:
                rec = self.store.get(rid)
                if rec is None:
                    continue
                # TASK-0067 未合入时用默认值（0/""），合入后自动生效（零文件交集）
                access_count = getattr(rec, "access_count", 0) or 0
                last_accessed = getattr(rec, "last_accessed", "") or ""
                created_at = getattr(rec, "created_at", "") or ""
                days = days_since(last_accessed, str(created_at), now)
                decay = compute_decay_factor(days, access_count,
                                              self.settings.decay_lambda,
                                              self.settings.decay_gamma)
                rescored.append((rid, apply_decay(score, decay)))
            fused = sorted(rescored, key=lambda x: x[1], reverse=True)

        fused = fused[:top_k]

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