"""N22b 新鲜度权重与governance API（TASK-0070，A3 spec §3.3+§4）。

freshness_boost 纯函数 + compute_stats + retriever 排序应用点（与衰减正交相乘）+ 端点测试。
"""
from datetime import datetime, timedelta

import pytest

from kb.governance import compute_stats, freshness_boost


# ---- freshness_boost 纯函数测试 ----

class TestFreshnessBoost:
    """freshness_boost 纯函数（A3 spec §3.3）。"""

    def test_boost范围_1到1_3(self):
        """boost 范围 [1, 1+α] = [1, 1.3]（α=0.3 默认）。"""
        assert 1.0 <= freshness_boost(0.0) <= 1.3
        assert 1.0 <= freshness_boost(100.0) <= 1.3
        assert freshness_boost(100.0) == pytest.approx(1.0, abs=0.01)  # 很旧→接近1

    def test_新记忆权重高于旧(self):
        """新记忆（0天）boost > 旧记忆（30天）boost。"""
        new_boost = freshness_boost(0.0)
        old_boost = freshness_boost(30.0)
        assert new_boost > old_boost
        # 0天：1+0.3*exp(0)=1.3；30天：1+0.3*exp(-1.5)≈1+0.3*0.223≈1.067
        assert new_boost == pytest.approx(1.3, abs=0.01)
        assert old_boost == pytest.approx(1.067, abs=0.01)

    def test_默认参数(self):
        """默认 beta=0.05, alpha=0.3（A3 spec §7）。"""
        assert freshness_boost(0.0) == pytest.approx(1.3, abs=0.01)

    def test_负天数钳制为0(self):
        """负天数（未来更新时间）钳制为 0，不产生 >1.3 的异常 boost。"""
        assert freshness_boost(-5.0) == pytest.approx(1.3, abs=0.01)

    def test_alpha为0时boost恒为1(self):
        """alpha=0 时 boost=1.0（无新鲜度加权）。"""
        assert freshness_boost(0.0, alpha=0.0) == pytest.approx(1.0, abs=1e-9)

    def test_beta为0时boost恒为1加alpha(self):
        """beta=0 时 recency=1，boost=1+alpha（所有记录相同加权）。"""
        assert freshness_boost(100.0, beta=0.0, alpha=0.3) == pytest.approx(1.3, abs=1e-9)


# ---- compute_stats 纯函数测试 ----

class _FakeRecordForStats:
    """mock Record for compute_stats：含 access_count/last_accessed/created_at。"""
    def __init__(self, access_count=0, last_accessed="", created_at=None):
        self.access_count = access_count
        self.last_accessed = last_accessed
        self.created_at = created_at or datetime(2026, 1, 1).isoformat()


class TestComputeStats:
    """compute_stats 纯函数（A3 spec §4.2，TASK-0070）。"""

    def test_空记录返回0(self):
        """空记录迭代器返回全 0。"""
        result = compute_stats([])
        assert result["total_count"] == 0
        assert result["avg_access_count"] == 0.0
        assert result["stale_90d_count"] == 0

    def test_正常统计(self):
        """正常记录统计：总数/均access_count/超90天。"""
        now = datetime(2026, 8, 27, 12, 0, 0)
        records = [
            _FakeRecordForStats(access_count=5, last_accessed=now.isoformat()),
            _FakeRecordForStats(access_count=15, last_accessed=(now - timedelta(days=10)).isoformat()),
            _FakeRecordForStats(access_count=0, last_accessed="",
                                 created_at=(now - timedelta(days=100)).isoformat()),
        ]
        result = compute_stats(records, now=now)
        assert result["total_count"] == 3
        assert result["avg_access_count"] == pytest.approx(6.67, abs=0.01)  # (5+15+0)/3
        assert result["stale_90d_count"] == 1  # 第3条 created_at 100天前

    def test_last_accessed为空用created_at(self):
        """last_accessed 为空时用 created_at 计算是否超90天（A3 spec §3.1）。"""
        now = datetime(2026, 8, 27, 12, 0, 0)
        records = [
            _FakeRecordForStats(last_accessed="", created_at=(now - timedelta(days=95)).isoformat()),
        ]
        result = compute_stats(records, now=now)
        assert result["stale_90d_count"] == 1

    def test_未命中90天不算stale(self):
        """89天未命中不算 stale。"""
        now = datetime(2026, 8, 27, 12, 0, 0)
        records = [
            _FakeRecordForStats(last_accessed=(now - timedelta(days=89)).isoformat()),
        ]
        result = compute_stats(records, now=now)
        assert result["stale_90d_count"] == 0


# ---- retriever 排序应用点测试（mock，不依赖真实模型）----

class _FakeType:
    def __init__(self, value):
        self.value = value


class _FakeRecordForRetriever:
    """mock Record for retriever：含 access_count/last_accessed/created_at/updated_at。"""
    def __init__(self, rid, access_count=0, last_accessed="", created_at=None,
                 updated_at=None, type_value="memory", tags=None, source="s", content="",
                 agent_id="default"):
        self.id = rid
        self.access_count = access_count
        self.last_accessed = last_accessed
        self.created_at = created_at or datetime(2026, 1, 1).isoformat()
        self.updated_at = updated_at or self.created_at
        self.type = _FakeType(type_value)
        self.tags = tags or []
        self.source = source
        self.content = content
        self.agent_id = agent_id  # A 节点：Agent 归属（默认 default 隔离不生效）


class _FakeStore:
    def __init__(self, records):
        self._records = {r.id: r for r in records}

    def query(self, vec, top_k=10):
        return [(r, 0.9 - i * 0.1) for i, r in enumerate(self._records.values())][:top_k]

    def get(self, rid):
        return self._records.get(rid)

    def get_many(self, ids):
        """mock 批量读取（N27），与 store.get_many 契约一致。"""
        return {rid: r for rid in ids if (r := self._records.get(rid)) is not None}

    def increment_access(self, rid):
        """mock 命中计数（TASK-0067），空实现。"""
        pass


class _FakeEmbedder:
    def embed_query(self, query):
        return [0.1, 0.2, 0.3]


class _FakeBM25:
    def search(self, query, top_n=10):
        return []


class _FakeSettings:
    def __init__(self, decay_enabled=False, decay_lambda=0.02, decay_gamma=0.3,
                 freshness_enabled=False, freshness_beta=0.05, freshness_alpha=0.3):
        self.decay_enabled = decay_enabled
        self.decay_lambda = decay_lambda
        self.decay_gamma = decay_gamma
        self.freshness_enabled = freshness_enabled
        self.freshness_beta = freshness_beta
        self.freshness_alpha = freshness_alpha


class TestRetrieverFreshness:
    """retriever 排序应用点（TASK-0070）：新鲜度与衰减正交相乘。"""

    def _make_retriever(self, records, decay_enabled=False, freshness_enabled=False):
        from kb.retriever import HybridRetriever
        store = _FakeStore(records)
        settings = _FakeSettings(decay_enabled=decay_enabled,
                                 freshness_enabled=freshness_enabled)
        return HybridRetriever(store, _FakeBM25(), _FakeEmbedder(), settings=settings)

    def test_新鲜度开启_新记录排前面(self):
        """freshness_enabled=true 时，新记录（updated_at 近）排前面。"""
        now = datetime.now()
        records = [
            _FakeRecordForRetriever("a", updated_at=(now - timedelta(days=100)).isoformat()),
            _FakeRecordForRetriever("b", updated_at=now.isoformat()),
        ]
        ret = self._make_retriever(records, freshness_enabled=True)
        hits = ret.search("test", top_k=2, mode="vector")
        # b 新→boost高→排第一；a 旧→boost低→排第二
        assert hits[0]["id"] == "b"
        assert hits[1]["id"] == "a"

    def test_新鲜度关闭_零变化(self):
        """freshness_enabled=false 时，排序与向量分降序一致（零行为变化，回归）。"""
        now = datetime.now()
        records = [
            _FakeRecordForRetriever("a", updated_at=(now - timedelta(days=100)).isoformat()),
            _FakeRecordForRetriever("b", updated_at=now.isoformat()),
        ]
        ret = self._make_retriever(records, freshness_enabled=False)
        hits = ret.search("test", top_k=2, mode="vector")
        # 关闭时：按向量分降序（a=0.9, b=0.8），a 排第一
        assert hits[0]["id"] == "a"
        assert hits[1]["id"] == "b"

    def test_衰减加新鲜度_正交相乘(self):
        """衰减+新鲜度都开启时，两者正交相乘（final = rrf * decay * freshness_boost）。"""
        now = datetime.now()
        records = [
            # a：旧+未访问→衰减低，但新更新→新鲜度高
            _FakeRecordForRetriever("a", access_count=0, last_accessed="",
                                    created_at=(now - timedelta(days=90)).isoformat(),
                                    updated_at=now.isoformat()),
            # b：新+高频→衰减少，但旧更新→新鲜度低
            _FakeRecordForRetriever("b", access_count=10, last_accessed=now.isoformat(),
                                    created_at=now.isoformat(),
                                    updated_at=(now - timedelta(days=100)).isoformat()),
        ]
        ret = self._make_retriever(records, decay_enabled=True, freshness_enabled=True)
        hits = ret.search("test", top_k=2, mode="vector")
        # 两者正交相乘，不崩溃，返回2条
        assert len(hits) == 2
        assert {h["id"] for h in hits} == {"a", "b"}

    def test_keyword模式不应用新鲜度(self):
        """keyword（BM25）模式不应用新鲜度（A3 spec §3.3：BM25不受影响）。"""
        now = datetime.now()
        records = [
            _FakeRecordForRetriever("a", content="keyword", updated_at=now.isoformat()),
        ]
        ret = self._make_retriever(records, freshness_enabled=True)
        hits = ret.search("test", top_k=2, mode="keyword")
        # mock BM25 返回空，keyword 模式结果为空，不触发新鲜度
        assert hits == []


# ---- 端点测试 ----

class TestGovernanceEndpoints:
    """/governance/stats 与 /governance/config 端点（TASK-0070）。"""

    def test_config端点返回结构(self, env_isolated):
        """/governance/config GET 返回衰减+新鲜度配置结构。"""
        from kb.api import create_app
        from fastapi.testclient import TestClient
        client = TestClient(create_app())
        with client:
            r = client.get("/api/v1/governance/config")
        assert r.status_code == 200
        data = r.json()
        assert "decay_enabled" in data
        assert "decay_lambda" in data
        assert "decay_gamma" in data
        assert "freshness_enabled" in data
        assert "freshness_beta" in data
        assert "freshness_alpha" in data
        # 默认全关
        assert data["decay_enabled"] is False
        assert data["freshness_enabled"] is False

    def test_stats端点空库返回0(self, env_isolated):
        """/governance/stats GET 空库返回全 0。"""
        from kb.api import create_app
        from fastapi.testclient import TestClient
        client = TestClient(create_app())
        with client:
            r = client.get("/api/v1/governance/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_count"] == 0
        assert data["avg_access_count"] == 0.0
        assert data["stale_90d_count"] == 0

    def test_stats端点有记录返回统计(self, env_isolated):
        """/governance/stats GET 有记录时返回正确统计。"""
        from kb.api import create_app
        from fastapi.testclient import TestClient
        client = TestClient(create_app())
        with client:
            # 写入2条记录
            client.post("/api/v1/memories", json={"content": "记录A", "tags": ["test"]})
            client.post("/api/v1/memories", json={"content": "记录B", "tags": ["test"]})
            r = client.get("/api/v1/governance/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_count"] == 2
        # TASK-0067 未合入时 access_count=0，avg=0
        assert data["avg_access_count"] == 0.0
        # 新记录不超90天
        assert data["stale_90d_count"] == 0
