"""遗忘机制升级验收测试：全作用域候选、保护规则、级联删除与备份。

English: Acceptance tests for the forgetting upgrade: cross-scope candidates,
protection rules, cascade deletion, and pre-delete backups.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from kb.models import RecordType


def _mock_record(rid, content="test", last_accessed="", created_at=None,
                 tags=None, rtype=RecordType.MEMORY):
    r = MagicMock()
    r.id = rid
    r.content = content
    r.type = rtype
    r.tags = tags or []
    r.last_accessed = last_accessed
    r.created_at = created_at or (datetime.now() - timedelta(days=100)).isoformat()
    return r


class TestFindStaleRecordFilters:
    """find_stale_records 新增的类型/标签/ID 过滤。"""

    def test_按类型过滤只保留memory(self):
        from kb.cli import find_stale_records
        now = datetime(2026, 8, 28, 12, 0, 0)
        old = (now - timedelta(days=120)).isoformat()
        mem = _mock_record("m1", last_accessed=old)
        doc = _mock_record("d1", last_accessed=old, rtype=RecordType.DOC_CHUNK)
        result = find_stale_records([mem, doc], days=90, now=now,
                                    types=[RecordType.MEMORY])
        assert [r.id for r, _ in result] == ["m1"]

    def test_按标签跳过受保护记录(self):
        from kb.cli import find_stale_records
        now = datetime(2026, 8, 28, 12, 0, 0)
        old = (now - timedelta(days=120)).isoformat()
        pinned = _mock_record("r1", last_accessed=old, tags=["pinned", "重要"])
        normal = _mock_record("r2", last_accessed=old, tags=["日常"])
        result = find_stale_records([pinned, normal], days=90, now=now,
                                    keep_tags=("pinned",))
        assert [r.id for r, _ in result] == ["r2"]

    def test_按ID跳过关联保护记录(self):
        from kb.cli import find_stale_records
        now = datetime(2026, 8, 28, 12, 0, 0)
        old = (now - timedelta(days=120)).isoformat()
        linked = _mock_record("r1", last_accessed=old)
        free = _mock_record("r2", last_accessed=old)
        result = find_stale_records([linked, free], days=90, now=now,
                                    keep_ids={"r1"})
        assert [r.id for r, _ in result] == ["r2"]


@pytest.mark.integration
class TestCollectForgetCandidates:
    """collect_forget_candidates：跨作用域收集与保护规则。"""

    @pytest.fixture
    def svc(self, env_isolated):
        from kb.service import KBService
        return KBService()

    @pytest.fixture
    def future(self):
        return datetime.now() + timedelta(days=200)

    @pytest.fixture
    def seeded(self, svc):
        g = svc.add_memory("全局库记忆")
        a = svc.add_memory("alpha 项目记忆", project="alpha")
        b = svc.add_memory("beta 项目记忆", project="beta")
        return {"g": g, "a": a, "b": b}

    def test_覆盖全局与全部项目子库(self, svc, seeded, future):
        from kb.cli import collect_forget_candidates
        candidates, stats = collect_forget_candidates(
            svc, 90, now=future)
        assert {c.scope for c in candidates} == {"", "alpha", "beta"}
        assert {c.record.id for c in candidates} == {
            seeded["g"].id, seeded["a"].id, seeded["b"].id}
        assert stats["candidates"] == 3
        assert stats["scanned"] == 3

    def test_按项目与全局限定(self, svc, seeded, future):
        from kb.cli import collect_forget_candidates
        only_alpha, _ = collect_forget_candidates(
            svc, 90, project="alpha", now=future)
        assert {c.scope for c in only_alpha} == {"alpha"}
        assert {c.record.id for c in only_alpha} == {seeded["a"].id}

        only_global, _ = collect_forget_candidates(
            svc, 90, global_only=True, now=future)
        assert {c.scope for c in only_global} == {""}
        assert {c.record.id for c in only_global} == {seeded["g"].id}

    def test_默认跳过文档且可显式纳入(self, svc, seeded, future, tmp_path):
        from kb.cli import collect_forget_candidates
        doc = tmp_path / "note.md"
        doc.write_text("这是一份项目文档内容，用于验证遗忘命令不会误删文档 chunk。",
                       encoding="utf-8")
        ing = svc.add_document(doc, project="alpha")
        assert ing["chunks"] >= 1
        doc_ids = {r.id for r in svc.scopes.get("alpha").store.iter_all()
                   if r.type is RecordType.DOC_CHUNK}

        candidates, stats = collect_forget_candidates(svc, 90, now=future)
        assert not (doc_ids & {c.record.id for c in candidates})
        assert stats["documents_skipped"] >= 1

        included, _ = collect_forget_candidates(
            svc, 90, now=future, include_documents=True)
        assert doc_ids <= {c.record.id for c in included}

    def test_标签保护(self, svc, future):
        from kb.cli import collect_forget_candidates
        keep = svc.add_memory("需要长期保留的记忆", tags=["pinned"])
        free = svc.add_memory("可遗忘的记忆")
        candidates, stats = collect_forget_candidates(
            svc, 90, now=future, keep_tags=("pinned",))
        ids = {c.record.id for c in candidates}
        assert keep.id not in ids
        assert free.id in ids
        assert stats["tagged_skipped"] == 1

    def test_关联保护与解除(self, svc, future):
        from kb.cli import collect_forget_candidates
        a = svc.add_memory("有关联的记忆 A")
        b = svc.add_memory("有关联的记忆 B")
        svc.link_memories(a.id, b.id)

        protected, stats = collect_forget_candidates(svc, 90, now=future)
        assert {c.record.id for c in protected} == set()
        assert stats["linked_skipped"] == 2

        forced, _ = collect_forget_candidates(
            svc, 90, now=future, keep_linked=False)
        assert {c.record.id for c in forced} == {a.id, b.id}

    def test_天数下限校验(self, svc, future):
        from kb.cli import collect_forget_candidates
        with pytest.raises(ValueError):
            collect_forget_candidates(svc, 0, now=future)


@pytest.mark.integration
class TestBatchDeleteRecords:
    """batch_delete_records：级联清理与审计。"""

    @pytest.fixture
    def svc(self, env_isolated):
        from kb.service import KBService
        return KBService()

    def test_删除记忆级联清理索引关联与审计(self, svc, tmp_path):
        a = svc.add_memory("待删除的全局记忆")
        b = svc.add_memory("保留的全局记忆")
        svc.link_memories(a.id, b.id)

        deleted = svc.batch_delete_records(
            [a.id], agent_id="pytest", client="pytest-forget")
        assert deleted == 1
        assert svc.store.get(a.id) is None
        assert a.id not in svc.bm25._docs
        assert svc.list_links(b.id)["links"] == []
        # 全局 count.txt 同步（ScopeManager 打开时写入，删除后需刷新）
        assert (tmp_path / "count.txt").read_text(encoding="utf-8") == "1"
        events = svc.query_access_audit(client="pytest-forget", action="forget")
        assert any(e["record_id"] is None or e.get("hits") == 1 for e in events)

    def test_删除项目库记录并同步子库计数(self, svc, tmp_path):
        r = svc.add_memory("项目子库待删记忆", project="alpha")
        deleted = svc.batch_delete_records(
            [r.id], project="alpha", client="pytest-forget")
        assert deleted == 1
        assert svc.scopes.get("alpha").store.get(r.id) is None
        assert (tmp_path / "projects" / "alpha" / "count.txt").read_text(
            encoding="utf-8") == "0"

    def test_不存在的ID跳过(self, svc):
        assert svc.batch_delete_records(["missing-id"]) == 0

    def test_空列表返回零(self, svc):
        assert svc.batch_delete_records([]) == 0


class TestForgetBackup:
    """删除前备份文件。"""

    def test_备份为JSONL且保留完整内容(self, tmp_path):
        from kb.cli import ForgetCandidate, write_forget_backup
        r = _mock_record("r1", content="完整记忆内容", tags=["a"])
        r.source = None
        r.namespace = "default"
        r.project = ""
        r.updated_at = r.created_at
        r.access_count = 3
        r.importance = 0.5
        path = write_forget_backup(
            [ForgetCandidate(scope="alpha", record=r, days=123.4)],
            tmp_path)
        assert path.parent == tmp_path / "forget-backups"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        item = json.loads(lines[0])
        assert item["id"] == "r1"
        assert item["scope"] == "alpha"
        assert item["content"] == "完整记忆内容"
        assert item["tags"] == ["a"]
        assert item["days"] == 123.4
