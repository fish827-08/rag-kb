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
    English: score(d) = Σ 1/(RRF_K + rank_i(d)), rank starting at 1; take the top_k by fused score descending.
    N25: variadic args (2 or 3 routes depending on the sparse toggle); dual-route behavior matches the original.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (rid, _score) in enumerate(ranked, start=1):
            scores[rid] = scores.get(rid, 0.0) + 1 / (RRF_K + rank)
    ranked_fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked_fused[:top_k]


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
        (client, project) 隔离（v2 spec 2.3）：memory 记录仅返回归属该
        client+project 的，doc_chunk/web_chunk（共享知识库）不受隔离；
        agent_id 仅作兼容冗余不参与过滤；
        输出 [{id, content, score, type, source, tags, created_at, agent_id}]，
        score 为融合分。
        English: mode: hybrid/vector/keyword; each route takes 3*top_k candidates;
        type/tag filtering happens after fusion; (client, project) isolation (v2 spec 2.3):
        memory records only return those owned by the matching client+project, while
        doc_chunk/web_chunk (shared knowledge base) are not isolated; agent_id is a
        compatibility redundancy and does not filter; output is
        [{id, content, score, type, source, tags, created_at, agent_id}]."""
        candidate = 3 * top_k
        if mode in ("hybrid", "vector"):
            vec = self.embedder.embed_query(query)
            # 改进项 3：隔离下推到 Chroma where——memory 按 (client, project) 查、
            # doc/web 共享知识单独查，两段候选按相似度降序合并恢复全局排名
            # （RRF rank 语义与原单次查询一致；跨客户端记忆不再挤占候选池）。
            # 默认桶（project=""）无法用 where 表达（Chroma 1.5.9 拒绝空串值且
            # 缺键不匹配），仅 client 下推，项目精确过滤留在融合后 Python 侧。
            mem_where: dict = {"$and": [{"type": RecordType.MEMORY.value},
                                        {"client": client}]}
            if project:
                mem_where["$and"].append({"project": project})
            shared_where = {"type": {"$in": [RecordType.DOC_CHUNK.value,
                                             RecordType.WEB_CHUNK.value]}}
            mem_hits = self.store.query(vec, top_k=candidate, where=mem_where)
            shared_hits = self.store.query(vec, top_k=candidate, where=shared_where)
            merged = sorted(mem_hits + shared_hits, key=lambda x: x[1], reverse=True)
            # 按 id 去重保留最高分：真实 Chroma 两段结果（memory / doc+web）天然不相交，
            # 此步为防御（降级实现/测试替身可能返回重叠集）；插入序即分数降序
            seen: dict[str, tuple] = {}
            for rec, s in merged:
                if rec.id not in seen:
                    seen[rec.id] = (rec, s)
            vector_hits = [(r.id, s) for r, s in seen.values()]
        else:
            vector_hits = []
        if mode in ("hybrid", "keyword"):
            # 改进项 3：BM25 排序前过滤不可见 memory（filter 不改变 BM25 分数）
            keyword_hits = self.bm25.search(
                query, top_n=candidate,
                filter_fn=lambda rid: self._visible(rid, client, project))
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
            if sparse_hits:
                fused = rrf_fuse(vector_hits, keyword_hits, sparse_hits,
                                 top_k=candidate)
            else:
                fused = rrf_fuse(vector_hits, keyword_hits, top_k=candidate)
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
            # v2 (client, project) 隔离：个人记忆（memory）只返回归属该
            # client+project 的；共享知识（doc_chunk/web_chunk）对所有客户端可见
            if rec.type == RecordType.MEMORY and (
                    rec.client != client or (rec.project or "") != (project or "")):
                continue
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

    def _visible(self, record_id: str, client: str, project: str) -> bool:
        """检索隔离可见性（改进项 3，v2 spec 2.3）：memory 仅 (client, project)
        归属者可见，doc/web 共享可见；元信息缺失（旧语料/未知 id）不拦截（防御）。
        English: Retrieval isolation visibility (improvement #3, v2 spec 2.3): memory
        records visible only to their (client, project) owner, doc/web chunks shared;
        records without meta info are not blocked (defensive fallback)."""
        meta = self.bm25.meta_of(record_id)
        if meta is None:
            return True
        rec_client, rec_project, rec_type = meta
        if rec_type != RecordType.MEMORY.value:
            return True
        return rec_client == client and (rec_project or "") == (project or "")