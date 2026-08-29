"""N21a Record 元数据扩展 + 命中计数 + 衰减因子（TASK-0067，A3 spec §6.1）。

覆盖 spec §6.1 全部五个用例：
1. 新记录 access_count=0, last_accessed=""
2. last_accessed 为空时用 created_at 计算衰减
3. access_count 大 → decay_factor 提升（γ 项生效）
4. 90 天未命中 → decay_factor < 0.2
5. _clean_metadata 不过滤 access_count=0（0 是有效值），只过滤 None/""
附加：旧记录缺失字段读为默认值；increment_access 失败不抛异常。
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


class TestRecordMetadata:
    """Record 模型新增 access_count / last_accessed 字段。"""

    def test_record_metadata_access_count_default(self):
        """spec §6.1-1：新记录 access_count=0, last_accessed=""。"""
        from kb.models import Record
        r = Record(content="test")
        assert r.access_count == 0
        assert r.last_accessed == ""

    def test_from_chroma_old_record_missing_fields(self):
        """旧记录（无 access_count/last_accessed 字段）读为默认值 0/""。"""
        from kb.models import Record
        r = Record.from_chroma("id1", "content",
                                {"created_at": "2026-01-01T00:00:00",
                                 "updated_at": "2026-01-01T00:00:00"})
        assert r.access_count == 0
        assert r.last_accessed == ""

    def test_to_metadata_contains_new_fields(self):
        """to_metadata 输出含 access_count 与 last_accessed。"""
        from kb.models import Record
        r = Record(content="test", access_count=3,
                   last_accessed="2026-08-27T10:00:00")
        meta = r.to_metadata()
        assert meta["access_count"] == 3
        assert meta["last_accessed"] == "2026-08-27T10:00:00"


class TestDecayFactor:
    """衰减因子纯函数（A3 spec §3.1）：exp(-λ*days) * (1 + γ*log₂(1+access_count))。"""

    def test_decay_factor_never_accessed_uses_created_at(self):
        """spec §6.1-2：last_accessed 为空时用 created_at 计算。"""
        from kb.models import decay_factor
        created = (datetime.now() - timedelta(days=10)).isoformat()
        f = decay_factor(last_accessed="", created_at=created, access_count=0)
        assert 0 < f < 1.0  # 10 天前创建，应有衰减但不为负

    def test_decay_factor_high_access_boost(self):
        """spec §6.1-3：access_count 大 → decay_factor 提升（γ 项生效）。"""
        from kb.models import decay_factor
        now = datetime.now().isoformat()
        f_low = decay_factor(last_accessed=now, created_at=now, access_count=0)
        f_high = decay_factor(last_accessed=now, created_at=now, access_count=10)
        assert f_high > f_low
        assert f_high > 1.0  # 高频加权 > 1

    def test_decay_factor_old_unaccessed_decays(self):
        """spec §6.1-4：90 天未命中 → decay_factor < 0.2。"""
        from kb.models import decay_factor
        old = (datetime.now() - timedelta(days=90)).isoformat()
        f = decay_factor(last_accessed="", created_at=old, access_count=0)
        assert f < 0.2

    def test_decay_factor_invalid_date_returns_one(self):
        """无效日期字符串不崩溃，返回 1.0（不衰减）。"""
        from kb.models import decay_factor
        f = decay_factor(last_accessed="", created_at="not-a-date", access_count=0)
        assert f == 1.0


class TestCleanMetadata:
    """_clean_metadata 过滤 None 与空串，保留 0（有效值）。"""

    def test_chroma_metadata_skips_none(self):
        """spec §6.1-5：_clean_metadata 不过滤 access_count=0，只过滤 None/""。"""
        from kb.models import Record
        from kb.storage import _clean_metadata
        r = Record(content="test", source=None, access_count=0,
                   last_accessed="", tags=[])
        meta = _clean_metadata(r)
        assert "source" not in meta        # None 过滤
        assert "last_accessed" not in meta  # 空串过滤
        assert "tags" not in meta           # 空串（tags 逗号拼接为 ""）过滤
        assert meta["access_count"] == 0    # 0 是有效值，不过滤

    def test_clean_metadata_keeps_nonempty_values(self):
        """非空值全部保留。"""
        from kb.models import Record
        from kb.storage import _clean_metadata
        r = Record(content="test", source="src", access_count=5,
                   last_accessed="2026-08-27T10:00:00", tags=["a", "b"])
        meta = _clean_metadata(r)
        assert meta["source"] == "src"
        assert meta["access_count"] == 5
        assert meta["last_accessed"] == "2026-08-27T10:00:00"
        assert meta["tags"] == "a,b"


class TestIncrementAccess:
    """ChromaStore.increment_access：命中计数 +1，失败不抛异常。"""

    def _make_store(self):
        """构造绕过 __init__ 的 ChromaStore，手动注入 mock _col。"""
        from kb.storage import ChromaStore
        store = ChromaStore.__new__(ChromaStore)
        store._col = MagicMock()
        return store

    def test_increment_access_failure_does_not_raise(self):
        """异步更新失败（_col.update 抛异常）不向外抛，记 WARNING。"""
        store = self._make_store()
        store._col.update.side_effect = RuntimeError("db error")
        store.get = MagicMock(return_value=MagicMock(access_count=0, id="id1"))
        # 不应抛异常
        store.increment_access(["id1"])

    def test_increment_access_increments_count(self):
        """正常路径：access_count+1，last_accessed=now，调用 _col.update。"""
        from kb.models import Record
        store = self._make_store()
        rec = Record(content="test", access_count=3)
        store.get = MagicMock(return_value=rec)
        store.increment_access([rec.id])
        assert store._col.update.called
        call_kwargs = store._col.update.call_args
        metadata = call_kwargs.kwargs.get("metadatas") or call_kwargs[1].get("metadatas")
        assert metadata[0]["access_count"] == 4  # 3+1
        assert metadata[0]["last_accessed"]  # 非空（now）
