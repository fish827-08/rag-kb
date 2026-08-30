"""N23b 治理操作审计日志（TASK-0073，A3 spec §5）。

audit 函数 + service 去重点调用 + retriever 衰减/新鲜度审计。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kb.audit import log_governance_event, reset_audit_logger


@pytest.fixture(autouse=True)
def _reset_audit():
    """每个测试前后重置审计 logger 单例，避免 handler 残留。"""
    reset_audit_logger()
    yield
    reset_audit_logger()


# ---- audit 函数测试 ----

class TestAuditLogger:
    """log_governance_event 纯函数（TASK-0073）。"""

    def test_写入JSON行可解析(self, tmp_path):
        """写入一条审计日志，JSON 行格式可解析。"""
        log_governance_event("dedup_blocked", "rec-001",
                             {"similarity": 0.95, "duplicate_of": "rec-001"},
                             log_dir=tmp_path)
        log_file = tmp_path / "governance-audit.log"
        assert log_file.exists()
        lines = log_file.read_text(encoding="utf-8-sig").strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["operation"] == "dedup_blocked"
        assert event["record_id"] == "rec-001"
        assert event["namespace"] == "default"
        assert event["detail"]["similarity"] == 0.95
        assert event["detail"]["duplicate_of"] == "rec-001"
        assert "timestamp" in event

    def test_多条日志每行一个JSON(self, tmp_path):
        """多条审计日志，每行一个 JSON 对象。"""
        log_governance_event("op1", "r1", {"k": 1}, log_dir=tmp_path)
        log_governance_event("op2", "r2", {"k": 2}, log_dir=tmp_path)
        log_file = tmp_path / "governance-audit.log"
        lines = log_file.read_text(encoding="utf-8-sig").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["operation"] == "op1"
        assert json.loads(lines[1])["operation"] == "op2"

    def test_namespace可自定义(self, tmp_path):
        """namespace 参数可自定义。"""
        log_governance_event("dedup_blocked", "r1", {}, namespace="my-ns",
                             log_dir=tmp_path)
        log_file = tmp_path / "governance-audit.log"
        event = json.loads(log_file.read_text(encoding="utf-8-sig").strip())
        assert event["namespace"] == "my-ns"

    def test_detail为空时默认空dict(self, tmp_path):
        """detail 为 None 时默认空 dict。"""
        log_governance_event("op", "r1", None, log_dir=tmp_path)
        log_file = tmp_path / "governance-audit.log"
        event = json.loads(log_file.read_text(encoding="utf-8-sig").strip())
        assert event["detail"] == {}

    def test_审计失败不阻塞主流程(self, tmp_path, monkeypatch):
        """审计日志写入失败时不抛异常，不阻塞主流程。"""
        # mock _get_audit_logger 抛异常
        import kb.audit as audit_mod
        def _boom(log_dir=None):
            raise RuntimeError("磁盘满")
        monkeypatch.setattr(audit_mod, "_get_audit_logger", _boom)
        # 不应抛异常
        log_governance_event("op", "r1", {}, log_dir=tmp_path)


# ---- service 去重审计测试 ----

class TestServiceDedupAudit:
    """service.add_memory 去重409拦截点调用审计（TASK-0073）。"""

    def test_去重拦截时写审计日志(self, env_isolated, tmp_path, monkeypatch):
        """去重命中时写 dedup_blocked 审计日志（含 similarity/duplicate_of）。"""
        from kb.api import create_app
        from fastapi.testclient import TestClient
        # mock check_duplicate 返回命中
        import kb.governance as gov
        def _fake_check(content, store, embedder, threshold=0.92):
            return ("existing-rec-001", 0.95)
        monkeypatch.setattr(gov, "check_duplicate", _fake_check)
        # 设置日志目录到 tmp_path，开启去重
        monkeypatch.setenv("KB_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setenv("KB_DEDUP_ENABLED", "true")
        from kb import config
        config.get_settings.cache_clear()
        client = TestClient(create_app())
        with client:
            # 开启去重
            r = client.post("/api/v1/memories", json={"content": "测试内容"})
        # 去重命中应返回 409
        assert r.status_code == 409
        # 审计日志应存在
        log_file = tmp_path / "logs" / "governance-audit.log"
        assert log_file.exists(), f"审计日志不存在: {log_file}"
        event = json.loads(log_file.read_text(encoding="utf-8-sig").strip())
        assert event["operation"] == "dedup_blocked"
        assert event["record_id"] == "existing-rec-001"
        assert event["detail"]["similarity"] == 0.95
        assert event["detail"]["duplicate_of"] == "existing-rec-001"

    def test_去重审计关闭时不写日志(self, env_isolated, tmp_path, monkeypatch):
        """audit_dedup_enabled=false 时去重命中不写审计日志。"""
        from kb.api import create_app
        from fastapi.testclient import TestClient
        import kb.governance as gov
        def _fake_check(content, store, embedder, threshold=0.92):
            return ("existing-rec-002", 0.93)
        monkeypatch.setattr(gov, "check_duplicate", _fake_check)
        monkeypatch.setenv("KB_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setenv("KB_DEDUP_ENABLED", "true")
        monkeypatch.setenv("KB_AUDIT_DEDUP_ENABLED", "false")
        from kb import config
        config.get_settings.cache_clear()
        client = TestClient(create_app())
        with client:
            r = client.post("/api/v1/memories", json={"content": "测试内容"})
        assert r.status_code == 409
        log_file = tmp_path / "logs" / "governance-audit.log"
        # 审计关闭时不应写日志（文件不存在或为空）
        if log_file.exists():
            assert log_file.read_text(encoding="utf-8-sig").strip() == ""


# ---- retriever 衰减/新鲜度审计测试 ----

class _FakeType:
    def __init__(self, value):
        self.value = value


class _FakeRecord:
    def __init__(self, rid, access_count=0, last_accessed="", created_at=None,
                 updated_at=None, content="", agent_id="default"):
        self.id = rid
        self.access_count = access_count
        self.last_accessed = last_accessed
        self.created_at = created_at or datetime(2026, 1, 1).isoformat()
        self.updated_at = updated_at or self.created_at
        self.type = _FakeType("memory")
        self.tags = []
        self.source = "s"
        self.content = content
        self.agent_id = agent_id  # A 节点：Agent 归属（默认 default 隔离不生效）


class _FakeStore:
    def __init__(self, records):
        self._records = {r.id: r for r in records}

    def query(self, vec, top_k=10, where=None):
        return [(r, 0.9 - i * 0.1) for i, r in enumerate(self._records.values())][:top_k]

    def get(self, rid):
        return self._records.get(rid)

    def get_many(self, ids):
        """mock 批量读取（N27），与 store.get_many 契约一致。"""
        return {rid: r for rid in ids if (r := self._records.get(rid)) is not None}

    def increment_access(self, rid):
        pass


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
    def __init__(self, decay_enabled=False, decay_lambda=0.02, decay_gamma=0.3,
                 freshness_enabled=False, freshness_beta=0.05, freshness_alpha=0.3,
                 audit_decay_enabled=False, audit_freshness_enabled=False):
        self.decay_enabled = decay_enabled
        self.decay_lambda = decay_lambda
        self.decay_gamma = decay_gamma
        self.freshness_enabled = freshness_enabled
        self.freshness_beta = freshness_beta
        self.freshness_alpha = freshness_alpha
        self.audit_decay_enabled = audit_decay_enabled
        self.audit_freshness_enabled = audit_freshness_enabled


class TestRetrieverAudit:
    """retriever 衰减/新鲜度点调用审计（TASK-0073，默认关可配）。"""

    def _make_retriever(self, records, **kwargs):
        from kb.retriever import HybridRetriever
        store = _FakeStore(records)
        settings = _FakeSettings(**kwargs)
        return HybridRetriever(store, _FakeBM25(), _FakeEmbedder(), settings=settings)

    def test_衰减审计开启时写日志(self, tmp_path, monkeypatch):
        """audit_decay_enabled=true 时衰减应用写 decay_applied 审计日志。"""
        now = datetime.now()
        records = [
            _FakeRecord("a", access_count=0, last_accessed="",
                        created_at=(now - timedelta(days=90)).isoformat()),
        ]
        ret = self._make_retriever(records, decay_enabled=True,
                                    audit_decay_enabled=True)
        # mock 审计日志目录到 tmp_path
        import kb.audit as audit_mod
        orig_get = audit_mod._get_audit_logger
        def _get_with_dir(log_dir=None):
            return orig_get(tmp_path)
        monkeypatch.setattr(audit_mod, "_get_audit_logger", _get_with_dir)
        ret.search("test", top_k=2, mode="vector")
        log_file = tmp_path / "governance-audit.log"
        assert log_file.exists()
        event = json.loads(log_file.read_text(encoding="utf-8-sig").strip())
        assert event["operation"] == "decay_applied"
        assert event["record_id"] == "a"
        assert "decay_factor" in event["detail"]
        assert "original_score" in event["detail"]
        assert "final_score" in event["detail"]

    def test_衰减审计默认关不写日志(self, tmp_path, monkeypatch):
        """audit_decay_enabled=false（默认）时衰减应用不写审计日志。"""
        now = datetime.now()
        records = [
            _FakeRecord("a", created_at=(now - timedelta(days=90)).isoformat()),
        ]
        ret = self._make_retriever(records, decay_enabled=True,
                                    audit_decay_enabled=False)
        import kb.audit as audit_mod
        orig_get = audit_mod._get_audit_logger
        def _get_with_dir(log_dir=None):
            return orig_get(tmp_path)
        monkeypatch.setattr(audit_mod, "_get_audit_logger", _get_with_dir)
        ret.search("test", top_k=2, mode="vector")
        log_file = tmp_path / "governance-audit.log"
        # 审计关闭时不写日志（文件不存在或为空）
        if log_file.exists():
            assert log_file.read_text(encoding="utf-8-sig").strip() == ""

    def test_新鲜度审计开启时写日志(self, tmp_path, monkeypatch):
        """audit_freshness_enabled=true 时新鲜度应用写 freshness_applied 审计日志。"""
        now = datetime.now()
        records = [
            _FakeRecord("b", updated_at=now.isoformat()),
        ]
        ret = self._make_retriever(records, freshness_enabled=True,
                                    audit_freshness_enabled=True)
        import kb.audit as audit_mod
        orig_get = audit_mod._get_audit_logger
        def _get_with_dir(log_dir=None):
            return orig_get(tmp_path)
        monkeypatch.setattr(audit_mod, "_get_audit_logger", _get_with_dir)
        ret.search("test", top_k=2, mode="vector")
        log_file = tmp_path / "governance-audit.log"
        assert log_file.exists()
        event = json.loads(log_file.read_text(encoding="utf-8-sig").strip())
        assert event["operation"] == "freshness_applied"
        assert event["record_id"] == "b"
        assert "boost" in event["detail"]
