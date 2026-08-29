"""最小评测基准：中文 QA 数据集 → Recall@1/@5 + MRR（N26，A3.5 spec §3.5）。

评测流程：
  独立 KB_DATA_DIR（CLI 层创建临时目录，不碰生产库）
    → 导入全部 corpus（每条独立成块，互为干扰项）
    → 逐条 question 检索（指定 mode/top_k，治理默认关）
    → 命中判定：corpus 记录 id ∈ top-k 结果
    → 指标：Recall@1 / Recall@5 / MRR = mean(1/rank)
    → 报告：总体 + 分难度 + 未命中清单 + 平均延迟

run_eval 与 service 解耦（鸭子类型：add_memory / search），可注入替身单测。

English: Minimal evaluation benchmark: Chinese QA dataset → Recall@1/@5 + MRR (N26, A3.5 spec §3.5).
Evaluation flow: isolated KB_DATA_DIR → import all corpus → retrieve per question → hit judgment
→ metrics (Recall@1 / Recall@5 / MRR) → report. run_eval is decoupled from service
(duck-typed add_memory / search), so a test stub can be injected for unit tests.
"""
import json
import time
from pathlib import Path

DIFFICULTY_TAGS = ("keyword", "semantic", "distractor")


class EvalDatasetError(Exception):
    """数据集加载失败（文件缺失 / JSONL 损坏 / 必填字段缺失）。
    English: Dataset load failure (missing file / corrupt JSONL / missing required fields)."""


def load_dataset(path) -> list[dict]:
    """加载 JSONL 数据集；每行 {"qid", "question", "corpus", "tags"}。

    字段缺失 / JSON 损坏 / 文件不存在抛 EvalDatasetError。
    English: Load a JSONL dataset; each line is {"qid", "question", "corpus", "tags"}.
    Missing fields / corrupt JSON / a nonexistent file raise EvalDatasetError."""
    path = Path(path)
    if not path.exists():
        raise EvalDatasetError(f"数据集不存在: {path}")
    items = []
    try:
        for line_no, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            for field in ("qid", "question", "corpus"):
                if field not in item:
                    raise EvalDatasetError(
                        f"第 {line_no} 行缺字段 {field}: {item}")
            items.append(item)
    except json.JSONDecodeError as e:
        raise EvalDatasetError(f"JSONL 解析失败（{path}）: {e}")
    return items


def compute_metrics(ranks: list[int | None]) -> dict:
    """由 1-based 排名列表计算 Recall@1 / Recall@5 / MRR。

    ranks: 每条问题的目标记录排名（1 起）；None=未进 top-k。
    空列表安全返回全 0。
    English: Compute Recall@1 / Recall@5 / MRR from a 1-based rank list.
    ranks: the target-record rank per question (starting at 1); None = not in top-k.
    An empty list safely returns all zeros."""
    n = len(ranks)
    if n == 0:
        return {"recall_at_1": 0.0, "recall_at_5": 0.0, "mrr": 0.0}
    hit1 = sum(1 for r in ranks if r == 1)
    hit5 = sum(1 for r in ranks if r is not None and r <= 5)
    mrr = sum(1 / r for r in ranks if r is not None) / n
    return {"recall_at_1": hit1 / n, "recall_at_5": hit5 / n, "mrr": mrr}


def _difficulty_of(item: dict) -> str | None:
    """提取难度标签（keyword/semantic/distractor）；无则 None。
    English: Extract the difficulty tag (keyword/semantic/distractor); None if absent."""
    for t in item.get("tags") or []:
        if t in DIFFICULTY_TAGS:
            return t
    return None


def run_eval(service, dataset: list[dict], top_k: int = 5,
             mode: str = "hybrid") -> dict:
    """端到端评测：写入 corpus → 逐条检索 → 指标汇总。

    service: KBService 或替身（需 add_memory(content, tags=...) / search(...)）。
    返回报告 dict：{count, top_k, mode, recall_at_1/5, mrr, latency_ms_avg,
                    by_difficulty, misses}。
    English: End-to-end evaluation: write corpus → retrieve per question → aggregate metrics.
    service: a KBService or stub (needs add_memory(content, tags=...) / search(...)).
    Returns a report dict: {count, top_k, mode, recall_at_1/5, mrr, latency_ms_avg,
    by_difficulty, misses}."""
    corpus_ids: dict[int, str] = {}  # qid → record_id
    for item in dataset:
        rec = service.add_memory(item["corpus"],
                                 tags=list(item.get("tags") or ["eval"]))
        corpus_ids[item["qid"]] = rec.id

    ranks: list[int | None] = []
    latency_total = 0.0
    misses = []
    for item in dataset:
        t0 = time.perf_counter()
        results = service.search(item["question"], top_k=top_k, mode=mode)
        latency_total += (time.perf_counter() - t0) * 1000.0
        target_id = corpus_ids[item["qid"]]
        rank = None
        for i, r in enumerate(results, start=1):
            if r["id"] == target_id:
                rank = i
                break
        ranks.append(rank)
        if rank is None:
            misses.append({"qid": item["qid"], "question": item["question"]})

    overall = compute_metrics(ranks)
    by_difficulty: dict[str, dict] = {}
    for tag in DIFFICULTY_TAGS:
        sub_ranks = [r for item, r in zip(dataset, ranks)
                     if _difficulty_of(item) == tag]
        if sub_ranks:
            by_difficulty[tag] = compute_metrics(sub_ranks)
            by_difficulty[tag]["count"] = len(sub_ranks)

    return {
        "count": len(dataset),
        "top_k": top_k,
        "mode": mode,
        "recall_at_1": overall["recall_at_1"],
        "recall_at_5": overall["recall_at_5"],
        "mrr": overall["mrr"],
        "latency_ms_avg": latency_total / max(1, len(dataset)),
        "by_difficulty": by_difficulty,
        "misses": misses,
    }
