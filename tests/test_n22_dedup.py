"""N22a 语义去重服务层单测（TASK-0069，A3 spec §3.2 卡片简化为 409 拦截）。

覆盖验收：
- check_duplicate：>=阈值返回(id,score)、<阈值返回(None,0)、空库返回(None,0)、异常降级(None,0)
- service.add_memory：dedup开+命中→抛DuplicateError、dedup开+未命中→正常入库、dedup关→零行为变化
- api.create_memory：DuplicateError→409+duplicate_of+similarity
- 阈值可配置
"""
from unittest.mock import MagicMock, patch

import pytest


class TestCheckDuplicate:
    """check_duplicate 纯函数（governance.py）。"""

    def _make_mocks(self, hits=None):
        store = MagicMock()
        store.query.return_value = hits or []
        embedder = MagicMock()
        embedder.embed_query.return_value = [0.1, 0.2, 0.3]
        return store, embedder

    def test_above_threshold_returns_existing_id(self):
        """相似度 >= 阈值 → 返回 (existing_id, similarity)。"""
        from kb.governance import check_duplicate
        rec = MagicMock()
        rec.id = "rec-001"
        store, embedder = self._make_mocks(hits=[(rec, 0.95)])
        result_id, score = check_duplicate("hello", store, embedder, threshold=0.92)
        assert result_id == "rec-001"
        assert score == 0.95

    def test_below_threshold_returns_none(self):
        """相似度 < 阈值 → 返回 (None, 0.0)。"""
        from kb.governance import check_duplicate
        rec = MagicMock()
        rec.id = "rec-001"
        store, embedder = self._make_mocks(hits=[(rec, 0.80)])
        result_id, score = check_duplicate("hello", store, embedder, threshold=0.92)
        assert result_id is None
        assert score == 0.0

    def test_empty_store_returns_none(self):
        """空库（无命中）→ 返回 (None, 0.0)。"""
        from kb.governance import check_duplicate
        store, embedder = self._make_mocks(hits=[])
        result_id, score = check_duplicate("hello", store, embedder)
        assert result_id is None
        assert score == 0.0

    def test_exception_degrades_to_none(self):
        """嵌入或检索异常 → 降级为 (None, 0.0)，不抛异常。"""
        from kb.governance import check_duplicate
        store, embedder = self._make_mocks()
        embedder.embed_query.side_effect = RuntimeError("embed failed")
        result_id, score = check_duplicate("hello", store, embedder)
        assert result_id is None
        assert score == 0.0

    def test_threshold_configurable(self):
        """阈值可配置：0.90 阈值下 0.91 命中，0.92 阈值下 0.91 不命中。"""
        from kb.governance import check_duplicate
        rec = MagicMock()
        rec.id = "rec-001"
        store, embedder = self._make_mocks(hits=[(rec, 0.91)])
        # 阈值 0.90 → 命中
        rid1, _ = check_duplicate("hello", store, embedder, threshold=0.90)
        assert rid1 == "rec-001"
        # 阈值 0.92 → 不命中
        rid2, _ = check_duplicate("hello", store, embedder, threshold=0.92)
        assert rid2 is None


class TestServiceAddMemory:
    """service.add_memory 去重接入（module-scoped KBService，减少 BGE-M3 加载）。"""

    @pytest.fixture(scope="module")
    def service(self):
        from kb.service import KBService
        return KBService()

    def test_dedup_enabled_duplicate_raises(self, service):
        """dedup 开 + check_duplicate 命中 → 抛 DuplicateError，不落库。"""
        from kb.governance import DuplicateError
        service.settings.dedup_enabled = True
        try:
            with patch("kb.governance.check_duplicate",
                       return_value=("existing-123", 0.95)):
                with pytest.raises(DuplicateError) as ei:
                    service.add_memory("hello world")
                assert ei.value.existing_id == "existing-123"
                assert ei.value.similarity == 0.95
        finally:
            service.settings.dedup_enabled = False

    def test_dedup_enabled_no_duplicate_writes(self, service):
        """dedup 开 + check_duplicate 未命中 → 正常入库。"""
        service.settings.dedup_enabled = True
        try:
            with patch("kb.governance.check_duplicate",
                       return_value=(None, 0.0)):
                r = service.add_memory("dedup no hit test unique 12345")
            assert r.id
            assert r.content == "dedup no hit test unique 12345"
        finally:
            service.settings.dedup_enabled = False

    def test_dedup_disabled_zero_behavior(self, service):
        """dedup 关 → 不调用 check_duplicate，正常入库（零行为变化）。"""
        service.settings.dedup_enabled = False
        with patch("kb.governance.check_duplicate") as mock_check:
            r = service.add_memory("dedup disabled test unique 67890")
            mock_check.assert_not_called()
        assert r.id
        assert r.content == "dedup disabled test unique 67890"


class TestApi409:
    """api.create_memory 捕获 DuplicateError 返回 409（module-scoped TestClient）。"""

    @pytest.fixture(scope="module")
    def client(self):
        from fastapi.testclient import TestClient
        from kb.api import create_app
        with TestClient(create_app()) as c:
            yield c

    def test_duplicate_returns_409(self, client):
        """DuplicateError → 409 + duplicate_of + similarity + error=DUPLICATE。"""
        from kb.governance import DuplicateError
        with patch.object(client.app.state.kb, "add_memory",
                          side_effect=DuplicateError("existing-456", 0.94)):
            r = client.post("/api/v1/memories", json={"content": "test"})
        assert r.status_code == 409
        body = r.json()
        assert body["error"] == "DUPLICATE"
        assert body["duplicate_of"] == "existing-456"
        assert body["similarity"] == 0.94
        assert "语义重复" in body["message"]

    def test_normal_write_returns_200(self, client):
        """正常写入 → 200（回归：去重关闭时零行为变化）。"""
        r = client.post("/api/v1/memories",
                         json={"content": "normal write unique 99999"})
        assert r.status_code == 200
        assert "id" in r.json()
