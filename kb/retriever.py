"""混合检索：向量 + BM25 双路，RRF 融合。"""
import threading
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

        # 治理排序应用点（TASK-0068 衰减 §3.1 + TASK-0070 新鲜度 §3.3）：仅 hybrid/vector 模式，BM25 不受影响
        # 默认全关零行为变化；衰减看 last_accessed（访问冷热），新鲜度看 updated_at（内容新旧），两者正交相乘
        decay_on = (self.settings is not None
                    and getattr(self.settings, "decay_enabled", False))
        freshness_on = (self.settings is not None
                        and getattr(self.settings, "freshness_enabled", False))
        if (decay_on or freshness_on) and mode != "keyword":
            from kb.governance import (apply_decay, compute_decay_factor,
                                        days_since, freshness_boost)
            # N23b/TASK-0073：治理审计开关（默认关，不阻塞主流程）
            audit_decay_on = (self.settings is not None
                              and getattr(self.settings, "audit_decay_enabled", False))
            audit_freshness_on = (self.settings is not None
                                  and getattr(self.settings, "audit_freshness_enabled", False))
            now = datetime.now()
            rescored = []
            # N27：批量取记录（消除循环内逐条 get 的 N+1）
            recs_by_id = self.store.get_many([rid for rid, _ in fused])
            for rid, score in fused:
                rec = recs_by_id.get(rid)
                if rec is None:
                    continue
                final = score
                # 衰减（TASK-0068，§3.1）：access_count/last_accessed 用 getattr 兼容 TASK-0067 未合入
                if decay_on:
                    access_count = getattr(rec, "access_count", 0) or 0
                    last_accessed = getattr(rec, "last_accessed", "") or ""
                    created_at = getattr(rec, "created_at", "") or ""
                    days = days_since(last_accessed, str(created_at), now)
                    decay = compute_decay_factor(days, access_count,
                                                  self.settings.decay_lambda,
                                                  self.settings.decay_gamma)
                    final = apply_decay(final, decay)
                    if audit_decay_on:
                        from kb.audit import log_governance_event
                        log_governance_event(
                            "decay_applied", rid,
                            {"decay_factor": round(decay, 6),
                             "original_score": round(score, 6),
                             "final_score": round(final, 6),
                             "access_count": access_count,
                             "days_since_access": round(days, 2)})
                # 新鲜度（TASK-0070，§3.3）：用 updated_at，与衰减正交相乘
                if freshness_on:
                    updated_at = getattr(rec, "updated_at", "") or ""
                    days_updated = days_since(str(updated_at), "", now)
                    boost = freshness_boost(days_updated,
                                            self.settings.freshness_beta,
                                            self.settings.freshness_alpha)
                    final *= boost
                    if audit_freshness_on:
                        from kb.audit import log_governance_event
                        log_governance_event(
                            "freshness_applied", rid,
                            {"boost": round(boost, 6),
                             "final_score": round(final, 6),
                             "days_since_updated": round(days_updated, 2)})
                rescored.append((rid, final))
            fused = sorted(rescored, key=lambda x: x[1], reverse=True)

        fused = fused[:top_k]

        results = []
        # N27：批量取记录（消除循环内逐条 get 的 N+1）
        recs_by_id = self.store.get_many([rid for rid, _ in fused])
        for rid, score in fused:
            rec = recs_by_id.get(rid)
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
        # N21a/TASK-0067：命中记录异步更新 access_count+1 / last_accessed=now
        # （daemon 线程，不阻塞检索返回；increment_access 内部捕获异常记 WARNING）
        if results:
            hit_ids = [r["id"] for r in results]
            threading.Thread(
                target=self.store.increment_access,
                args=(hit_ids,),
                daemon=True,
            ).start()
        return results