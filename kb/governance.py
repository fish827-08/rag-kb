"""记忆治理 A3：访问频率衰减纯函数模块（N21b，TASK-0068）+ 语义去重（N22a，TASK-0069）。

纯函数优先：输入标量输出分数，不依赖 Record/Store/配置，可直接单测。
衰减公式（A3 spec §3.1）：
  decay_factor = exp(-λ * days_since_last_accessed) * (1 + γ * log₂(1 + access_count))
  final_score = rrf_score * decay_factor

语义去重（A3 spec §3.2，卡片简化为 409 拦截）：
  check_duplicate(content, store, embedder, threshold) → (existing_id|None, similarity)
  写入前向量检索 top1，相似度 ≥ 阈值视为重复，service 层抛 DuplicateError，api 层返回 409。

默认 KB_DECAY_ENABLED=false / KB_DEDUP_ENABLED=false，零行为变化。
BM25 路径不受衰减影响（仅向量/RRF 排序受影响）。
"""
import math
from datetime import datetime


class DuplicateError(Exception):
    """语义去重命中异常（N22a/TASK-0069）。

    service.add_memory 在 dedup_enabled 且 check_duplicate 命中时抛出；
    api.create_memory 捕获后返回 409 + duplicate_of 字段。
    """

    def __init__(self, existing_id: str, similarity: float):
        self.existing_id = existing_id
        self.similarity = similarity
        super().__init__(f"语义重复（相似度 {similarity:.3f}），已有记录 {existing_id}")


def compute_decay_factor(days_since_last_accessed: float,
                         access_count: int,
                         lambda_: float = 0.02,
                         gamma: float = 0.3) -> float:
    """计算衰减因子（A3 spec §3.1）。

    decay_factor = exp(-λ * days) * (1 + γ * log₂(1 + access_count))

    参数：
        days_since_last_accessed: 距上次访问的天数（>=0，last_accessed 为空时用 created_at）
        access_count: 累计访问次数（>=0，新记录=0）
        lambda_: 衰减速率 λ（/天），默认 0.02（半衰期≈35天）
        gamma: 高频访问加权系数 γ，默认 0.3（access_count=10→约2.0倍）

    返回：
        衰减因子（>0）；高频访问记忆获得加权，长期未访问记忆降权。
    """
    if days_since_last_accessed < 0:
        days_since_last_accessed = 0.0
    if access_count < 0:
        access_count = 0
    recency = math.exp(-lambda_ * days_since_last_accessed)
    frequency = 1.0 + gamma * math.log2(1.0 + access_count)
    return recency * frequency


def apply_decay(rrf_score: float, decay_factor: float) -> float:
    """对 RRF 融合分应用衰减因子（A3 spec §3.1）。

    final_score = rrf_score * decay_factor

    参数：
        rrf_score: RRF 融合分（>=0）
        decay_factor: 衰减因子（compute_decay_factor 的返回值）

    返回：
        衰减后的最终排序分。
    """
    return rrf_score * decay_factor


def days_since(last_accessed: str, created_at: str,
               now: datetime | None = None) -> float:
    """计算距上次访问的天数；last_accessed 为空时用 created_at（A3 spec §3.1）。

    参数：
        last_accessed: 上次访问时间（ISO 格式字符串，空串=""表示从未访问）
        created_at: 记录创建时间（ISO 格式字符串，从未访问时用此替代）
        now: 当前时间（None=datetime.now()）

    返回：
        天数差（>=0，未来时间视为0）。解析失败时返回0.0（安全降级）。
    """
    if now is None:
        now = datetime.now()
    ref_str = last_accessed if last_accessed else created_at
    if not ref_str:
        return 0.0
    try:
        ref = datetime.fromisoformat(ref_str)
    except (ValueError, TypeError):
        return 0.0
    delta = (now - ref).total_seconds() / 86400.0
    return max(0.0, delta)


def freshness_boost(days_since_updated: float,
                    beta: float = 0.05,
                    alpha: float = 0.3) -> float:
    """计算新鲜度加权因子（A3 spec §3.3）。

    recency = exp(-β * days_since_updated)
    boost = 1 + α * recency

    与衰减（§3.1）正交：衰减看 last_accessed（访问冷热），新鲜度看 updated_at
    （内容新旧），两者独立可叠加（正交相乘）。

    参数：
        days_since_updated: 距上次更新的天数（>=0，用 updated_at）
        beta: 新鲜度衰减速率 β（/天），默认 0.05（半衰期≈14天）
        alpha: 新鲜度加权上限系数 α，默认 0.3（boost 范围 [1, 1.3]）

    返回：
        新鲜度加权因子（范围 [1, 1+α]，新记忆接近 1+α，旧记忆接近 1）。
    """
    if days_since_updated < 0:
        days_since_updated = 0.0
    recency = math.exp(-beta * days_since_updated)
    return 1.0 + alpha * recency


def compute_stats(records, now: datetime | None = None) -> dict:
    """计算治理统计（A3 spec §4.2，TASK-0070）：纯函数，输入记录迭代器输出统计 dict。

    返回：
        total_count: 总记录数
        avg_access_count: 平均 access_count（保留2位小数）
        stale_90d_count: 超 90 天未命中数（last_accessed 为空时用 created_at）

    access_count/last_accessed 用 getattr 兼容 TASK-0067 未合入（未合入时均为 0/""）。
    """
    if now is None:
        now = datetime.now()
    total = 0
    access_sum = 0
    stale_90d = 0
    for rec in records:
        total += 1
        ac = getattr(rec, "access_count", 0) or 0
        access_sum += ac
        last_accessed = getattr(rec, "last_accessed", "") or ""
        created_at = getattr(rec, "created_at", "") or ""
        days = days_since(last_accessed, str(created_at), now)
        if days > 90:
            stale_90d += 1
    avg_access = (access_sum / total) if total > 0 else 0.0
    return {
        "total_count": total,
        "avg_access_count": round(avg_access, 2),
        "stale_90d_count": stale_90d,
    }


# ---- 语义去重（N22a/TASK-0069）----

def check_duplicate(content: str, store, embedder,
                    threshold: float = 0.92) -> tuple[str | None, float]:
    """语义去重检查（A3 spec §3.2，top1 拦截）。

    对新内容做向量检索 top1，若最高余弦相似度 ≥ threshold 则视为重复，
    返回 (existing_id, similarity)；否则返回 (None, 0.0)。

    参数：
        content: 待写入的新内容文本
        store: ChromaStore 实例（有 query(embedding, top_k) 方法）
        embedder: Embedder 实例（有 embed_query(text) 方法）
        threshold: 余弦相似度阈值，默认 0.92（≥此值视为重复）

    返回：
        (existing_id, similarity) 命中重复；(None, 0.0) 无重复。

    异常降级：嵌入或检索失败时返回 (None, 0.0)（不阻塞写入，记 WARNING 由调用方处理）。
    """
    import logging
    logger = logging.getLogger("kb.governance")
    try:
        vec = embedder.embed_query(content)
        hits = store.query(vec, top_k=1)
        if not hits:
            return (None, 0.0)
        _rec, score = hits[0]
        if score >= threshold:
            return (_rec.id, float(score))
        return (None, 0.0)
    except Exception as e:
        logger.warning("语义去重检查失败，降级为不拦截: %s", e)
        return (None, 0.0)
