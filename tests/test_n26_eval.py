"""N26 最小评测基准测试：load_dataset / compute_metrics / run_eval（A3.5 spec §3.5）。

覆盖：
- load_dataset：JSONL 加载与字段校验 / 真实数据集规模与难度分布
- compute_metrics：Recall@1 / Recall@5 / MRR 手工可算
- run_eval：注入 fake service 的迷你数据集端到端（写入→检索→指标→分难度）
run_eval 全程 mock 检索结果，不加载真实模型。
"""
import json
from pathlib import Path

import pytest


# ---------- load_dataset ----------

class TestLoadDataset:
    """JSONL 数据集加载与校验。"""

    def _write(self, tmp_path, items):
        p = tmp_path / "ds.jsonl"
        p.write_text("\n".join(json.dumps(i, ensure_ascii=False)
                               for i in items), encoding="utf-8")
        return p

    def test_加载合法jsonl(self, tmp_path):
        from kb.eval import load_dataset
        p = self._write(tmp_path, [
            {"qid": 1, "question": "问1？", "corpus": "答1。", "tags": ["eval", "keyword"]},
            {"qid": 2, "question": "问2？", "corpus": "答2。", "tags": ["eval", "semantic"]},
        ])
        ds = load_dataset(p)
        assert len(ds) == 2
        assert ds[0]["qid"] == 1
        assert ds[1]["tags"] == ["eval", "semantic"]

    def test_字段缺失报错(self, tmp_path):
        from kb.eval import load_dataset, EvalDatasetError
        p = self._write(tmp_path, [
            {"qid": 1, "question": "问1？"}])  # 缺 corpus
        with pytest.raises(EvalDatasetError):
            load_dataset(p)

    def test_文件缺失报错(self, tmp_path):
        from kb.eval import load_dataset, EvalDatasetError
        with pytest.raises(EvalDatasetError):
            load_dataset(tmp_path / "missing.jsonl")

    def test_真实数据集_50条_难度覆盖三类(self):
        """随仓库分发的 tests/eval_zh_50.jsonl：规模与难度分布约束。"""
        from kb.eval import load_dataset
        repo_root = Path(__file__).resolve().parent.parent
        ds = load_dataset(repo_root / "tests" / "eval_zh_50.jsonl")
        assert len(ds) == 50
        assert len({item["qid"] for item in ds}) == 50  # qid 唯一
        diffs = [t for item in ds
                 for t in item["tags"]
                 if t in ("keyword", "semantic", "distractor")]
        assert set(diffs) == {"keyword", "semantic", "distractor"}
        # 每类至少 10 条（难度均衡）
        for d in ("keyword", "semantic", "distractor"):
            assert sum(1 for item in ds if d in item["tags"]) >= 10


# ---------- compute_metrics ----------

class TestComputeMetrics:
    """Recall@1 / Recall@5 / MRR 手工可算（spec §3.5）。"""

    def test_手工计算(self):
        from kb.eval import compute_metrics
        # 4 条：rank 1 / 3 / None / 5
        # recall@1 = 1/4；recall@5 = 3/4；MRR = (1 + 1/3 + 0 + 1/5) / 4
        m = compute_metrics([1, 3, None, 5])
        assert m["recall_at_1"] == pytest.approx(0.25)
        assert m["recall_at_5"] == pytest.approx(0.75)
        assert m["mrr"] == pytest.approx((1 + 1 / 3 + 0 + 0.2) / 4)

    def test_全部命中第一名(self):
        from kb.eval import compute_metrics
        m = compute_metrics([1, 1, 1])
        assert m["recall_at_1"] == 1.0
        assert m["mrr"] == 1.0

    def test_全部未命中(self):
        from kb.eval import compute_metrics
        m = compute_metrics([None, None])
        assert m["recall_at_1"] == 0.0
        assert m["recall_at_5"] == 0.0
        assert m["mrr"] == 0.0

    def test_空列表零除安全(self):
        from kb.eval import compute_metrics
        m = compute_metrics([])
        assert m["recall_at_1"] == 0.0
        assert m["mrr"] == 0.0

    def test_rank超过5不计入recall5但计mrr(self):
        from kb.eval import compute_metrics
        m = compute_metrics([7])  # top_k=10 场景：rank 7 > 5
        assert m["recall_at_5"] == 0.0
        assert m["mrr"] == pytest.approx(1 / 7)


# ---------- run_eval ----------

class _FakeRecord:
    def __init__(self, rid):
        self.id = rid


class _FakeService:
    """评测替身：写入时登记 id 映射；检索按预设排名返回结果。"""

    def __init__(self, rank_map=None):
        # rank_map: {question: [corpus_qid 按期望排名排列]}
        self.records = {}          # qid → record_id
        self._next = 0
        self.rank_map = rank_map or {}
        self.searches = []

    def add_memory(self, content, tags=None):
        self._next += 1
        rid = f"rec-{self._next}"
        self.records[content] = rid
        return _FakeRecord(rid)

    def search(self, question, top_k=5, mode="hybrid"):
        self.searches.append(question)
        order = self.rank_map.get(question, [])
        results = []
        for rank, qid in enumerate(order[:top_k], start=1):
            # 从 corpus 反查 record_id
            rid = None
            for content, r in self.records.items():
                if self._content_qid(content) == qid:
                    rid = r
                    break
            if rid:
                results.append({"id": rid, "score": 1.0 / rank,
                                "content": ""})
        # 填充干扰项到 top_k
        fill = 0
        while len(results) < top_k and self.records:
            fill += 1
            results.append({"id": f"noise-{fill}", "score": 0.01,
                            "content": ""})
        return results

    def _content_qid(self, content):
        # 测试数据集 corpus 前缀 "corpus-qN" 提取 qid
        return int(content.split("q")[1])


class TestRunEval:
    """run_eval 端到端（注入 fake service，spec §6.2 迷你数据集）。"""

    def _mini_dataset(self):
        return [
            {"qid": 1, "question": "问题一", "corpus": "corpus-q1",
             "tags": ["eval", "keyword"]},
            {"qid": 2, "question": "问题二", "corpus": "corpus-q2",
             "tags": ["eval", "keyword"]},
            {"qid": 3, "question": "问题三", "corpus": "corpus-q3",
             "tags": ["eval", "semantic"]},
        ]

    def test_迷你数据集指标正确(self):
        from kb.eval import run_eval
        # q1 排第 1；q2 排第 2；q3 未命中（排名只有 q1）
        svc = _FakeService(rank_map={
            "问题一": [1],          # q1 corpus 第一
            "问题二": [1, 2],      # q1 干扰在前，q2 第二
            "问题三": [],           # q3 未命中
        })
        report = run_eval(svc, self._mini_dataset(), top_k=5, mode="hybrid")
        assert report["count"] == 3
        assert report["recall_at_1"] == pytest.approx(1 / 3)
        assert report["recall_at_5"] == pytest.approx(2 / 3)
        assert report["mrr"] == pytest.approx((1 + 0.5 + 0) / 3)
        # 未命中清单含 q3
        assert [m["qid"] for m in report["misses"]] == [3]
        # 逐条检索顺序 = 数据集顺序
        assert svc.searches == ["问题一", "问题二", "问题三"]

    def test_分难度统计(self):
        from kb.eval import run_eval
        svc = _FakeService(rank_map={
            "问题一": [1],
            "问题二": [1, 2],
            "问题三": [1, 2, 3],  # q3 的 corpus 排第 3（前两位是干扰）
        })
        report = run_eval(svc, self._mini_dataset(), top_k=5, mode="hybrid")
        kw = report["by_difficulty"]["keyword"]
        sm = report["by_difficulty"]["semantic"]
        # keyword 类：rank 1 / 2 → recall@1=0.5, mrr=(1+0.5)/2
        assert kw["count"] == 2
        assert kw["recall_at_1"] == pytest.approx(0.5)
        assert kw["mrr"] == pytest.approx(0.75)
        # semantic 类：rank 3 → mrr=1/3
        assert sm["count"] == 1
        assert sm["mrr"] == pytest.approx(1 / 3)

    def test_报告含配置与延迟字段(self):
        from kb.eval import run_eval
        svc = _FakeService(rank_map={"问题一": [1], "问题二": [2],
                                     "问题三": [3]})
        report = run_eval(svc, self._mini_dataset(), top_k=5, mode="vector")
        assert report["top_k"] == 5
        assert report["mode"] == "vector"
        assert report["latency_ms_avg"] >= 0.0
