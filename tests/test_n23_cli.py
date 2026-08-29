"""N23a 维护 CLI 候选筛选逻辑单测（TASK-0072）。

覆盖：find_stale_records（超 N 天未命中筛选）、find_duplicate_pairs（相似度阈值筛选）。
纯函数测试，不启动 KBService/不加载模型。
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


def _mock_record(rid, content="test", last_accessed="", created_at=None,
                 importance=0.5):
    """构造 mock Record（仅含测试需要的属性）。"""
    r = MagicMock()
    r.id = rid
    r.content = content
    r.last_accessed = last_accessed
    r.created_at = created_at or (datetime.now() - timedelta(days=100)).isoformat()
    r.importance = importance
    return r


class TestFindStaleRecords:
    """find_stale_records：超 N 天未命中筛选。"""

    def test_超过天数的记录被选中(self):
        from kb.cli import find_stale_records
        now = datetime(2026, 8, 28, 12, 0, 0)
        old = _mock_record("r1", last_accessed=(now - timedelta(days=120)).isoformat())
        fresh = _mock_record("r2", last_accessed=(now - timedelta(days=10)).isoformat())
        result = find_stale_records([old, fresh], days=90, now=now)
        assert len(result) == 1
        assert result[0][0].id == "r1"
        assert result[0][1] > 90

    def test_未超天数的记录不选(self):
        from kb.cli import find_stale_records
        now = datetime(2026, 8, 28, 12, 0, 0)
        r = _mock_record("r1", last_accessed=(now - timedelta(days=30)).isoformat())
        result = find_stale_records([r], days=90, now=now)
        assert result == []

    def test_last_accessed为空用created_at(self):
        """last_accessed 为空时用 created_at 计算天数。"""
        from kb.cli import find_stale_records
        now = datetime(2026, 8, 28, 12, 0, 0)
        r = _mock_record("r1", last_accessed="",
                         created_at=(now - timedelta(days=100)).isoformat())
        result = find_stale_records([r], days=90, now=now)
        assert len(result) == 1
        assert result[0][1] > 90

    def test_按天数降序(self):
        from kb.cli import find_stale_records
        now = datetime(2026, 8, 28, 12, 0, 0)
        r1 = _mock_record("r1", last_accessed=(now - timedelta(days=100)).isoformat())
        r2 = _mock_record("r2", last_accessed=(now - timedelta(days=200)).isoformat())
        result = find_stale_records([r1, r2], days=90, now=now)
        assert result[0][0].id == "r2"  # 200天在前
        assert result[1][0].id == "r1"

    def test_空列表返回空(self):
        from kb.cli import find_stale_records
        assert find_stale_records([], days=90) == []

    def test_恰好等于天数不选(self):
        """days=90，恰好90天不选（严格 > days）。"""
        from kb.cli import find_stale_records
        now = datetime(2026, 8, 28, 12, 0, 0)
        r = _mock_record("r1", last_accessed=(now - timedelta(days=90)).isoformat())
        result = find_stale_records([r], days=90, now=now)
        assert result == []


class TestFindDuplicatePairs:
    """find_duplicate_pairs：相似度阈值筛选。"""

    def _vec(self, *vals):
        return list(vals)

    def test_相似度超阈值被选中(self):
        from kb.cli import find_duplicate_pairs
        r1 = _mock_record("r1", content="hello world")
        r2 = _mock_record("r2", content="hello world")
        # 相同向量 → 相似度 1.0
        pairs = find_duplicate_pairs([(r1, self._vec(1, 0, 0)),
                                       (r2, self._vec(1, 0, 0))], threshold=0.85)
        assert len(pairs) == 1
        assert pairs[0][2] == pytest.approx(1.0)

    def test_相似度低于阈值不选(self):
        from kb.cli import find_duplicate_pairs
        r1 = _mock_record("r1")
        r2 = _mock_record("r2")
        # 正交向量 → 相似度 0.0
        pairs = find_duplicate_pairs([(r1, self._vec(1, 0, 0)),
                                       (r2, self._vec(0, 1, 0))], threshold=0.85)
        assert pairs == []

    def test_低于阈值不选(self):
        """相似度 0.84 < 0.85 不选（严格 > threshold）。"""
        from kb.cli import find_duplicate_pairs
        r1 = _mock_record("r1")
        r2 = _mock_record("r2")
        import math
        angle = math.acos(0.84)
        v1 = [1.0, 0.0]
        v2 = [math.cos(angle), math.sin(angle)]
        pairs = find_duplicate_pairs([(r1, v1), (r2, v2)], threshold=0.85)
        assert pairs == []

    def test_无重复对(self):
        from kb.cli import find_duplicate_pairs
        r1 = _mock_record("r1")
        r2 = _mock_record("r2")
        r3 = _mock_record("r3")
        pairs = find_duplicate_pairs([(r1, self._vec(1, 0, 0)),
                                       (r2, self._vec(0, 1, 0)),
                                       (r3, self._vec(0, 0, 1))], threshold=0.85)
        assert pairs == []

    def test_按相似度降序(self):
        from kb.cli import find_duplicate_pairs
        r1 = _mock_record("r1")
        r2 = _mock_record("r2")
        r3 = _mock_record("r3")
        import math
        # r1=[1,0,0]；r2 与 r1 相似度 0.95；r3 与 r1 相似度 0.9，与 r2 相似度<0.85
        v1 = [1.0, 0.0, 0.0]
        a2 = math.acos(0.95)
        v2 = [math.cos(a2), math.sin(a2), 0.0]  # 与 v1 相似度 0.95
        v3 = [0.9, -0.1, math.sqrt(1 - 0.81 - 0.01)]  # 与 v1=0.9，与 v2≈0.77<0.85
        pairs = find_duplicate_pairs([(r1, v1), (r2, v2), (r3, v3)], threshold=0.85)
        assert len(pairs) == 2
        assert pairs[0][2] == pytest.approx(0.95, abs=0.01)  # 最高在前
        assert pairs[1][2] == pytest.approx(0.9, abs=0.01)

    def test_零向量跳过(self):
        """零向量 norm=0，跳过不计算（避免除零）。"""
        from kb.cli import find_duplicate_pairs
        r1 = _mock_record("r1")
        r2 = _mock_record("r2")
        pairs = find_duplicate_pairs([(r1, self._vec(0, 0, 0)),
                                       (r2, self._vec(1, 0, 0))], threshold=0.85)
        assert pairs == []

    def test_少于2条返回空(self):
        from kb.cli import find_duplicate_pairs
        r1 = _mock_record("r1")
        assert find_duplicate_pairs([(r1, [1, 0, 0])], threshold=0.85) == []
        assert find_duplicate_pairs([], threshold=0.85) == []
