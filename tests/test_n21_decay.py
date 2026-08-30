"""N21b 衰减评分公式模块（TASK-0068，A3 spec §3.1）。

纯函数优先：governance.py 不依赖 Record/Store/配置，可直接单测。
retriever 排序应用点用 mock 验证（不依赖真实 embedding 模型）。
"""
from datetime import datetime, timedelta

import pytest

from kb.governance import apply_decay, compute_decay_factor, days_since


# ---- governance 纯函数测试 ----

class TestComputeDecayFactor:
    """compute_decay_factor 纯函数（A3 spec §3.1）。"""

    def test_90天未命中_衰减小于0_2(self):
        """90 天未访问 + access_count=0 → decay < 0.2（A3 spec：90天~16%）。"""
        decay = compute_decay_factor(90.0, 0, lambda_=0.02, gamma=0.3)
        assert decay < 0.2
        # 精确：exp(-0.02*90) * (1+0.3*log2(1)) = exp(-1.8) * 1 ≈ 0.1653
        assert decay == pytest.approx(0.1653, abs=0.01)

    def test_access_count_10_约2倍(self):
        """access_count=10 + 0天 → 约 2.0 倍（A3 spec：1+0.3*3.32≈2.0）。"""
        decay = compute_decay_factor(0.0, 10, lambda_=0.02, gamma=0.3)
        # exp(0) * (1 + 0.3 * log2(11)) = 1 * (1 + 0.3 * 3.459) ≈ 2.038
        assert 1.9 < decay < 2.1

    def test_新记录_access_count_0_0天_衰减为1(self):
        """新记录（0天未访问，access_count=0）→ decay=1.0（无衰减无加权）。"""
        decay = compute_decay_factor(0.0, 0, lambda_=0.02, gamma=0.3)
        assert decay == pytest.approx(1.0, abs=1e-9)

    def test_负天数_钳制为0(self):
        """负天数（未来时间）钳制为 0，不产生 >1 的异常衰减。"""
        decay = compute_decay_factor(-5.0, 0, lambda_=0.02, gamma=0.3)
        assert decay == pytest.approx(1.0, abs=1e-9)

    def test_负access_count_钳制为0(self):
        """负 access_count 钳制为 0。"""
        decay = compute_decay_factor(0.0, -3, lambda_=0.02, gamma=0.3)
        assert decay == pytest.approx(1.0, abs=1e-9)

    def test_默认参数(self):
        """默认 lambda_=0.02, gamma=0.3（A3 spec §3.1 默认值）。"""
        decay = compute_decay_factor(0.0, 0)
        assert decay == pytest.approx(1.0, abs=1e-9)

    def test_高频访问对抗衰减(self):
        """高频访问（access_count=100）即使 30 天未访问，衰减仍 > 0.5（γ 加权对抗 λ 衰减）。"""
        decay = compute_decay_factor(30.0, 100, lambda_=0.02, gamma=0.3)
        # exp(-0.6) * (1 + 0.3 * log2(101)) = 0.5488 * (1 + 0.3*6.658) = 0.5488 * 2.997 ≈ 1.645
        assert decay > 0.5


class TestApplyDecay:
    """apply_decay 纯函数。"""

    def test_基本乘法(self):
        """final_score = rrf_score * decay_factor。"""
        assert apply_decay(0.5, 0.8) == pytest.approx(0.4, abs=1e-9)

    def test_衰减为1_分数不变(self):
        """decay=1.0 时分数不变。"""
        assert apply_decay(0.3, 1.0) == pytest.approx(0.3, abs=1e-9)

    def test_衰减为0_分数为0(self):
        """decay=0 时分数为 0。"""
        assert apply_decay(0.5, 0.0) == pytest.approx(0.0, abs=1e-9)


class TestDaysSince:
    """days_since 辅助函数（last_accessed 为空时用 created_at）。"""

    def test_last_accessed_正常(self):
        """last_accessed 非空时用 last_accessed 计算天数。"""
        now = datetime(2026, 8, 27, 12, 0, 0)
        last = (now - timedelta(days=10)).isoformat()
        created = (now - timedelta(days=100)).isoformat()
        assert days_since(last, created, now) == pytest.approx(10.0, abs=0.01)

    def test_last_accessed为空_用created_at(self):
        """last_accessed 为空串时用 created_at（从未命中=创建时间，A3 spec §3.1）。"""
        now = datetime(2026, 8, 27, 12, 0, 0)
        created = (now - timedelta(days=30)).isoformat()
        assert days_since("", created, now) == pytest.approx(30.0, abs=0.01)

    def test_两者都为空_返回0(self):
        """last_accessed 和 created_at 都为空时返回 0（安全降级）。"""
        assert days_since("", "", datetime.now()) == 0.0

    def test_解析失败_返回0(self):
        """非法 ISO 字符串返回 0（安全降级，不崩溃）。"""
        assert days_since("not-a-date", "also-bad", datetime.now()) == 0.0

    def test_未来时间_钳制为0(self):
        """last_accessed 在未来时钳制为 0（不产生负天数）。"""
        now = datetime(2026, 8, 27, 12, 0, 0)
        future = (now + timedelta(days=5)).isoformat()
        assert days_since(future, "", now) == 0.0


# ---- retriever 排序应用点测试（mock，不依赖真实模型）----

class _FakeType:
    """mock RecordType：有 .value 属性（retriever.py 用 rec.type.value）。"""
    def __init__(self, value):
        self.value = value


class _FakeRecord:
    """mock Record：含 access_count/last_accessed/created_at（TASK-0067 字段兼容）。"""
    def __init__(self, rid, content="c", access_count=0, last_accessed="",
                 created_at=None, type_value="memory", tags=None, source="s",
                 agent_id="default"):
        self.id = rid
        self.content = content
        self.access_count = access_count
        self.last_accessed = last_accessed
        self.created_at = created_at or datetime(2026, 1, 1).isoformat()
        self.type = _FakeType(type_value)
        self.tags = tags or []
        self.source = source
        self.agent_id = agent_id  # A 节点：Agent 归属（fetch 现有 fake 均 default）


class _FakeStore:
    """mock Store：query 返回固定向量命中，get 返回固定 Record。"""
    def __init__(self, records):
        self._records = {r.id: r for r in records}

    def query(self, vec, top_k=10, where=None):
        return [(r, 0.9 - i * 0.1) for i, r in enumerate(self._records.values())][:top_k]

    def increment_access(self, record_ids):
        """TASK-0067 合入后检索命中异步计数；测试 mock 空实现（计数逻辑由
        test_n21_metadata.py 覆盖，此处只需接口存在不抛 AttributeError）。"""
        pass

    def get(self, rid):
        return self._records.get(rid)

    def get_many(self, ids):
        """mock 批量读取（N27），与 store.get_many 契约一致。"""
        return {rid: r for rid in ids if (r := self._records.get(rid)) is not None}


class _FakeEmbedder:
    def embed_query(self, query):
        return [0.1, 0.2, 0.3]


class _FakeBM25:
    def search(self, query, top_n=10, filter_fn=None):
        return []

    def meta_of(self, record_id):
        """改进项 3 隔离过滤回调：mock 无元信息 → 返回 None（不拦截）。"""
        return None


class _FakeSettings:
    def __init__(self, decay_enabled=False, decay_lambda=0.02, decay_gamma=0.3):
        self.decay_enabled = decay_enabled
        self.decay_lambda = decay_lambda
        self.decay_gamma = decay_gamma


class TestRetrieverDecayApplication:
    """retriever 排序应用点（TASK-0068）：衰减开启时重排，关闭时零行为变化。"""

    def _make_retriever(self, records, decay_enabled=False):
        from kb.retriever import HybridRetriever
        store = _FakeStore(records)
        settings = _FakeSettings(decay_enabled=decay_enabled)
        return HybridRetriever(store, _FakeBM25(), _FakeEmbedder(), settings=settings)

    def test_衰减关闭_排序与现状一致(self):
        """KB_DECAY_ENABLED=false 时，排序与 RRF 融合完全一致（零行为变化，回归）。"""
        now = datetime.now()
        records = [
            _FakeRecord("a", access_count=0, last_accessed="",
                        created_at=(now - timedelta(days=90)).isoformat()),
            _FakeRecord("b", access_count=10, last_accessed=now.isoformat(),
                        created_at=(now - timedelta(days=100)).isoformat()),
        ]
        ret = self._make_retriever(records, decay_enabled=False)
        hits = ret.search("test", top_k=2, mode="vector")
        # 衰减关闭：按向量分降序（a=0.9, b=0.8），a 排第一
        assert hits[0]["id"] == "a"
        assert hits[1]["id"] == "b"

    def test_衰减开启_高频访问记录排前面(self):
        """KB_DECAY_ENABLED=true 时，高频访问记录（b）衰减后分数更高，排第一。"""
        now = datetime.now()
        records = [
            _FakeRecord("a", access_count=0, last_accessed="",
                        created_at=(now - timedelta(days=90)).isoformat()),
            _FakeRecord("b", access_count=10, last_accessed=now.isoformat(),
                        created_at=(now - timedelta(days=100)).isoformat()),
        ]
        ret = self._make_retriever(records, decay_enabled=True)
        hits = ret.search("test", top_k=2, mode="vector")
        # 衰减开启：a 衰减~0.165*0.9≈0.149，b 衰减~2.0*0.8≈1.6，b 排第一
        assert hits[0]["id"] == "b"
        assert hits[1]["id"] == "a"

    def test_衰减开启_keyword模式不应用衰减(self):
        """BM25（keyword）模式不受衰减影响（A3 spec §3.1）。"""
        now = datetime.now()
        records = [
            _FakeRecord("a", content="keyword match", access_count=0,
                        created_at=(now - timedelta(days=90)).isoformat()),
        ]
        # BM25 mock 返回空，keyword 模式结果为空，不触发衰减
        ret = self._make_retriever(records, decay_enabled=True)
        hits = ret.search("test", top_k=2, mode="keyword")
        assert hits == []  # mock BM25 返回空

    def test_衰减开启_90天未命中分数降低(self):
        """90 天未命中记录衰减后分数 < 原分数的 0.2 倍。"""
        now = datetime.now()
        records = [
            _FakeRecord("a", access_count=0, last_accessed="",
                        created_at=(now - timedelta(days=90)).isoformat()),
        ]
        ret = self._make_retriever(records, decay_enabled=True)
        hits = ret.search("test", top_k=1, mode="vector")
        assert hits[0]["score"] < 0.9 * 0.2  # 原分 0.9，衰减 < 0.2 倍


# ---- 配置项测试 ----

class TestConfigDecay:
    """kb/config.py 衰减配置项（TASK-0068，A3 spec §7）。"""

    def test_默认值(self, monkeypatch):
        """默认 decay_enabled=false, decay_lambda=0.02, decay_gamma=0.3（A3 spec §7）。

        _env_file=None：跳过本地 .env（A3 实战验证期 .env 会开启 KB_DECAY_ENABLED=true，
        污染"默认关闭零行为变化"断言——默认值指源码默认，非本机覆盖）。
        """
        from kb.config import Settings
        for k in ("KB_DECAY_ENABLED", "KB_DECAY_LAMBDA", "KB_DECAY_GAMMA"):
            monkeypatch.delenv(k, raising=False)
        s = Settings(_env_file=None)
        assert s.decay_enabled is False
        assert s.decay_lambda == pytest.approx(0.02, abs=1e-9)
        assert s.decay_gamma == pytest.approx(0.3, abs=1e-9)

    def test_环境变量覆盖(self, monkeypatch):
        """环境变量 KB_DECAY_* 覆盖默认值。"""
        from kb.config import Settings, get_settings
        get_settings.cache_clear()
        monkeypatch.setenv("KB_DECAY_ENABLED", "true")
        monkeypatch.setenv("KB_DECAY_LAMBDA", "0.05")
        monkeypatch.setenv("KB_DECAY_GAMMA", "0.5")
        s = Settings()
        assert s.decay_enabled is True
        assert s.decay_lambda == pytest.approx(0.05, abs=1e-9)
        assert s.decay_gamma == pytest.approx(0.5, abs=1e-9)
        get_settings.cache_clear()
