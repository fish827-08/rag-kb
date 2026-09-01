"""混合检索：向量 + BM25 双路，RRF 融合。
English: Hybrid retrieval fusing the vector and BM25 routes via RRF."""
import threading
from datetime import datetime

from kb.bm25 import BM25Index
from kb.embedder import Embedder
from kb.models import RecordType

RRF_K = 60


def rrf_fuse(*ranked_lists, top_k: int) -> list[tuple[str, float]]:
    """score(d) = Σ 1/(RRF_K + rank_i(d))，rank 从 1 起；按融合分降序取 top_k。

    N25：可变参数（2 路或 3 路，路数由稀疏开关决定），双路行为与原实现一致。
    N32（2026-09-01）：hybrid 默认路径已改走 weighted_fuse，本函数保留供
    兼容引用（既有测试直接调用）与明细对比。
    English: score(d) = Σ 1/(RRF_K + rank_i(d)), rank starting at 1; take the top_k by fused score descending.
    N25: variadic args (2 or 3 routes depending on the sparse toggle); dual-route behavior matches the original.
    N32: hybrid now defaults to weighted_fuse; this function is kept for the existing tests and comparison.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (rid, _score) in enumerate(ranked, start=1):
            scores[rid] = scores.get(rid, 0.0) + 1 / (RRF_K + rank)
    ranked_fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked_fused[:top_k]


def _minmax_norm(ranked: list[tuple[str, float]]) -> dict[str, float]:
    """把一路分数独立 min-max 归一化到 [0,1]（N32）。

    该路 max==min 时：非零记 1.0（唯一候选视为相关），全零记 0.0。
    English: Min-max normalize one route's scores to [0,1] (N32); when
    max==min: 1.0 if non-zero (a sole candidate counts as relevant), else 0.0."""
    if not ranked:
        return {}
    scores = [s for _, s in ranked]
    lo, hi = min(scores), max(scores)
    if hi == lo:
        val = 1.0 if hi != 0.0 else 0.0
        return {rid: val for rid, _ in ranked}
    span = hi - lo
    return {rid: (s - lo) / span for rid, s in ranked}


def weighted_fuse(*ranked_lists, top_k: int) -> list[tuple[str, float]]:
    """归一化评分加权融合：每路独立 min-max 归一化后均权平均，保留真实分数区分度。

    N32（2026-09-01）取代 hybrid 默认路径的 RRF 排名融合：
    RRF 把融合分压缩到 ~0.001 噪声级——「双路第 1」与「双路第 15」仅差万分之几，
    含外语/专有名词的真实相关记录在 top_k 截断时被无关记录挤出（中文 query
    查不到英文歌名/项目专有名词）。加权融合后每路分数信息完整保留：
    score(d) = Σ norm_i(d) / 生效路数，不再按位次抹平。
    N33（2026-09-01）按"生效路数"均权：文档缺席某路（如跨语言记录在 BM25 路
    无词面重叠必然 0 分）则不再计入分母。原先统一 ÷ 总路数会把向量分系统性
    砍半——中文 query 下英文/日文记忆的 1.0 归一化分被 /2 稀释到 0.5 以下，
    被同语言双路命中的记录挤出 top_k。改为 ÷ 出现路数后：单路出现得原分，
    双路同现仍等效均权（同语言场景零行为变化）。
    English: Score-weighted fusion — normalize each route then average over the
    routes a record actually appears in (N32/N33). The min-max normalized score
    keeps real separation; dividing by total route count systematically halved
    cross-language records (which always miss the BM25 route), pushing them out
    of top_k — the N33 fix averages over present routes only."""
    norms: dict[str, list[float]] = {}
    for ranked in ranked_lists:
        for rid, norm in _minmax_norm(ranked).items():
            norms.setdefault(rid, []).append(norm)
    fused = [(rid, sum(vals) / len(vals)) for rid, vals in norms.items()]
    fused.sort(key=lambda x: x[1], reverse=True)
    return fused[:top_k]


class HybridRetriever:
    """混合检索器；mode 支持 hybrid / vector / keyword。
    English: Hybrid retriever; mode supports hybrid / vector / keyword."""

    def __init__(self, store, bm25: BM25Index, embedder: Embedder,
                 settings=None, reranker=None,
                 sparse_embedder=None, sparse_index=None,
                 write_lock=None):
        self.store = store
        self.bm25 = bm25
        self.embedder = embedder
        self.settings = settings  # TASK-0068：衰减配置（decay_enabled/lambda/gamma），None=不应用衰减
        self.reranker = reranker  # N24：CrossEncoder 精排器（None=不精排）
        self.sparse_embedder = sparse_embedder  # N25：稀疏编码器（None=无稀疏路）
        self.sparse_index = sparse_index        # N25：稀疏倒排索引（None=无稀疏路）
        # 改进项 2：服务层写锁注入（None=无锁，测试直构检索器时零行为变化）。
        # 命中计数的异步更新也是 Chroma 写操作，须与 CRUD 写路径串行化。
        self._write_lock = write_lock

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               type: str | None = None, tag: str | None = None,
               agent_id: str = "default",
               client: str = "default",
               project: str = "") -> list[dict]:
        """mode: hybrid/vector/keyword；每路取 3*top_k 候选；
        type/tag 过滤在融合后进行（过滤后不足 top_k 属正常）；
        v3（2026-08-31）：所有记忆（memory）与知识（doc/web）全共享，不再按
        (client, project) 隔离——任何客户端/任务均可检索全部记录；
        client/project/agent_id 仅作审计与兼容冗余，不参与过滤；
        输出 [{id, content, score, type, source, tags, created_at, agent_id}]，
        score 为融合分。
        English: mode: hybrid/vector/keyword; each route takes 3*top_k candidates;
        type/tag filtering happens after fusion;
        v3: all memories and shared knowledge (doc/web) are fully shared without
        (client, project) isolation — every client/task can retrieve any record;
        client/project/agent_id are audit/compatibility only and never filter;
        output is [{id, content, score, type, source, tags, created_at, agent_id}]."""
        candidate = max(30, 3 * top_k)  # N32：候选窗放宽，缓解小 top_k 下相关记录早截断
        if mode in ("hybrid", "vector"):
            vec = self.embedder.embed_query(query)
            # v3：一次全量查询取全局候选（memory + doc/web 同池），按相似度降序；
            # 原 v2 的 (client, project) where 拆段已移除
            merged = sorted(self.store.query(vec, top_k=candidate),
                            key=lambda x: x[1], reverse=True)
            # 按 id 去重保留最高分（防御：降级实现/测试替身可能返回重叠集）；
            # 插入序即分数降序
            seen: dict[str, tuple] = {}
            for rec, s in merged:
                if rec.id not in seen:
                    seen[rec.id] = (rec, s)
            vector_hits = [(r.id, s) for r, s in seen.values()]
        else:
            vector_hits = []
        if mode in ("hybrid", "keyword"):
            # v3：BM25 全量检索，不再按 (client, project) 排序前过滤
            keyword_hits = self.bm25.search(query, top_n=candidate)
        else:
            keyword_hits = []
        # N25 稀疏第三路（A3.5 spec §3.3）：仅 hybrid 且 sparse_enabled 且组件
        # 已注入时启用；SparseEmbedder 异常按不可用处理（双路兜底，不中断检索）
        sparse_hits: list[tuple[str, float]] = []
        sparse_on = (mode == "hybrid"
                     and self.sparse_embedder is not None
                     and self.sparse_index is not None
                     and self.settings is not None
                     and getattr(self.settings, "sparse_enabled", False))
        if sparse_on:
            try:
                qvec = self.sparse_embedder.encode([query])[0]
                sparse_hits = self.sparse_index.search(qvec, top_n=candidate)
            except Exception:
                sparse_hits = []

        if mode == "hybrid":
            # N32（2026-09-01）：hybrid 默认改归一化评分加权融合（保留真实分数
            # 区分度；RRF 排名融合会把相关记录与噪声记录压到同一个噪声带上）。
            if sparse_hits:
                fused = weighted_fuse(vector_hits, keyword_hits, sparse_hits,
                                      top_k=candidate)
            else:
                fused = weighted_fuse(vector_hits, keyword_hits, top_k=candidate)
        elif mode == "vector":
            fused = sorted(vector_hits, key=lambda x: x[1], reverse=True)[:candidate]
        else:  # keyword（BM25 不受衰减影响，A3 spec §3.1）
            # N24：截断放宽到 candidate（非 rerank 路径最终仍截 top_k，零行为变化；
            # rerank 开启时从完整候选池精排）
            fused = sorted(keyword_hits, key=lambda x: x[1], reverse=True)[:candidate]

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

        # N24 rerank 挂接（A3.5 spec §3.3）：治理重排后、截断前——精排最后
        # （治理是软信号，CrossEncoder 是精排，精排结果即最终排序依据）。
        # 默认关 / 未注入 reranker：直接截断（零行为变化）；异常由 reranker 内部降级。
        rerank_on = (self.reranker is not None
                     and self.settings is not None
                     and getattr(self.settings, "rerank_enabled", False))
        if rerank_on:
            top_n = int(getattr(self.settings, "rerank_top_n", 20))
            candidates = fused[:top_n]
            recs_by_id = self.store.get_many([rid for rid, _ in candidates])
            cands = [{"id": rid, "content": rec.content}
                     for rid, _ in candidates
                     if (rec := recs_by_id.get(rid)) is not None]
            try:
                reranked = self.reranker.rerank(query, cands, top_k=top_k)
                fused = [(c["id"], c.get("rerank_score", 0.0)) for c in reranked]
            except Exception:
                # 防御兜底：reranker 实现异常时不中断检索，退回截断（Reranker
                # 内部已降级，此处再兜一层保证主路径永远可达）
                fused = fused[:top_k]
        else:
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
            # v3（2026-08-31）：无 (client, project) 隔离——memory 与 doc/web
            # 全共享，任何客户端/任务均可读；隔离过滤已移除
            results.append({
                "id": rec.id,
                "content": rec.content,
                "score": score,
                "type": rec.type.value,
                "source": rec.source,
                "tags": rec.tags,
                "created_at": rec.created_at,
                "agent_id": rec.agent_id,
            })
        # N21a/TASK-0067：命中记录异步更新 access_count+1 / last_accessed=now
        # （daemon 线程，不阻塞检索返回；increment_access 内部捕获异常记 WARNING）
        # 改进项 2：更新为 Chroma 写操作，持有服务层写锁与 CRUD 串行化
        if results:
            hit_ids = [r["id"] for r in results]

            def _touch() -> None:
                if self._write_lock is not None:
                    with self._write_lock:
                        self.store.increment_access(hit_ids)
                else:
                    self.store.increment_access(hit_ids)

            threading.Thread(target=_touch, daemon=True).start()
        return results