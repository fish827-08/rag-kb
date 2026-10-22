"""记忆关联（轻量知识图谱）验收测试：建边/列边/删边、约束、检索扩展与接口。

English: Acceptance tests for memory links: create/list/remove, constraints,
retrieval expansion, and REST/MCP surfaces.
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def svc(env_isolated):
    from kb.service import KBService
    return KBService()


def _mem(svc, content, project=None, tags=None):
    return svc.add_memory(content, tags=tags, project=project)


def test_建边后双向可见且带邻居内容(svc):
    a = _mem(svc, "项目后端使用 FastAPI 构建")
    b = _mem(svc, "接口统一返回 JSON")
    r = svc.link_memories(a.id, b.id)
    assert r["ok"] is True and r["created"] is True

    la = svc.list_links(a.id)
    assert la["record_id"] == a.id
    assert len(la["links"]) == 1
    link = la["links"][0]
    assert link["id"] == b.id
    assert link["direction"] == "out"
    assert link["relation"] == "related"
    assert link["content"] == b.content

    lb = svc.list_links(b.id)
    assert lb["links"][0]["id"] == a.id
    assert lb["links"][0]["direction"] == "in"


def test_重复建边幂等且取消关联双向清理(svc):
    a, b = _mem(svc, "记忆 A"), _mem(svc, "记忆 B")
    assert svc.link_memories(a.id, b.id)["created"] is True
    assert svc.link_memories(a.id, b.id)["created"] is False
    assert len(svc.list_links(a.id)["links"]) == 1

    assert svc.unlink_memories(a.id, b.id) == 1
    assert svc.list_links(a.id)["links"] == []
    assert svc.list_links(b.id)["links"] == []


def test_双向建边合并为both且可按关系过滤(svc):
    a, b = _mem(svc, "记忆 A"), _mem(svc, "记忆 B")
    svc.link_memories(a.id, b.id, relation="related")
    svc.link_memories(b.id, a.id, relation="related")
    links = svc.list_links(a.id)["links"]
    assert len(links) == 1
    assert links[0]["direction"] == "both"

    svc.link_memories(a.id, b.id, relation="depends_on")
    assert len(svc.list_links(a.id)["links"]) == 2
    only = svc.list_links(a.id, relation="depends_on")["links"]
    assert len(only) == 1 and only[0]["relation"] == "depends_on"


def test_自关联非法关系与缺失记录被拒绝(svc):
    from kb.links import LinkError
    a, b = _mem(svc, "记忆 A"), _mem(svc, "记忆 B")

    with pytest.raises(LinkError) as exc:
        svc.link_memories(a.id, a.id)
    assert exc.value.code == "SELF_LINK"

    with pytest.raises(LinkError) as exc:
        svc.link_memories(a.id, b.id, relation="likes")
    assert exc.value.code == "INVALID_RELATION"

    with pytest.raises(LinkError) as exc:
        svc.link_memories(a.id, "missing-id")
    assert exc.value.code == "NOT_FOUND"

    with pytest.raises(LinkError) as exc:
        svc.list_links("missing-id")
    assert exc.value.code == "NOT_FOUND"


def test_非记忆记录不允许关联(svc, tmp_path):
    from kb.links import LinkError
    p = tmp_path / "note.txt"
    p.write_text("文档内容", encoding="utf-8")
    assert svc.add_document(p)["chunks"] == 1
    doc = svc.list_records(type="doc_chunk")[0][0]
    a = _mem(svc, "记忆 A")

    with pytest.raises(LinkError) as exc:
        svc.link_memories(a.id, doc.id)
    assert exc.value.code == "MEMORY_ONLY"


def test_跨作用域关联被拒绝而同项目可关联(svc):
    from kb.links import LinkError
    g = _mem(svc, "全局记忆")
    p1 = _mem(svc, "项目记忆 1", project="proj1")
    p2 = _mem(svc, "项目记忆 2", project="proj1")

    with pytest.raises(LinkError) as exc:
        svc.link_memories(g.id, p1.id)
    assert exc.value.code == "CROSS_SCOPE"

    r = svc.link_memories(p1.id, p2.id, project="proj1")
    assert r["ok"] is True
    assert svc.list_links(p1.id, project="proj1")["links"][0]["id"] == p2.id


def test_关联上限(svc):
    from kb.links import LinkError
    svc.settings.link_max_per_record = 2
    center = _mem(svc, "中心记忆")
    n1, n2, n3 = _mem(svc, "邻居 1"), _mem(svc, "邻居 2"), _mem(svc, "邻居 3")
    svc.link_memories(center.id, n1.id)
    svc.link_memories(center.id, n2.id)
    with pytest.raises(LinkError) as exc:
        svc.link_memories(center.id, n3.id)
    assert exc.value.code == "LIMIT_REACHED"
    # 已达上限时重复建既有边仍幂等，不应报错
    assert svc.link_memories(center.id, n1.id)["created"] is False


def test_删除记忆清理关联(svc):
    from kb.links import LinkError
    a, b = _mem(svc, "记忆 A"), _mem(svc, "记忆 B")
    svc.link_memories(a.id, b.id)
    assert svc.delete_memory(a.id) is True
    assert svc.list_links(b.id)["links"] == []
    with pytest.raises(LinkError):
        svc.list_links(a.id)


def test_检索自动扩展一跳邻居并带_via(svc):
    a = _mem(svc, "蓝鲸项目的发布代号是深海行动")
    b = _mem(svc, "深海行动需要提前预约运维窗口")
    _mem(svc, "完全无关的天气记录")
    svc.link_memories(a.id, b.id)

    hits = svc.search("蓝鲸项目发布代号", top_k=1)
    ids = [h["id"] for h in hits]
    assert a.id in ids and b.id in ids
    extra = next(h for h in hits if h["id"] == b.id)
    assert extra["via"] == {"id": a.id, "relation": "related"}


def test_关闭扩展开关后不追加邻居(svc):
    a = _mem(svc, "蓝鲸项目的发布代号是深海行动")
    b = _mem(svc, "深海行动需要提前预约运维窗口")
    svc.link_memories(a.id, b.id)
    svc.settings.link_expand_enabled = False

    hits = svc.search("蓝鲸项目发布代号", top_k=1)
    assert b.id not in [h["id"] for h in hits]


def test_扩展数量受上限约束(svc):
    center = _mem(svc, "唯一的锚点记忆")
    for i in range(5):
        n = _mem(svc, f"外围邻居 {i}")
        svc.link_memories(center.id, n.id)

    hits = svc.search("唯一的锚点记忆", top_k=1)
    extras = [h for h in hits if h.get("via")]
    assert len(extras) == svc.settings.link_expand_limit


def test_REST_关联端点(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app

    with TestClient(create_app()) as c:
        a = c.post("/api/v1/memories", json={"content": "REST 记忆 A"}).json()
        b = c.post("/api/v1/memories", json={"content": "REST 记忆 B"}).json()

        r = c.post(f"/api/v1/memories/{a['id']}/links",
                   json={"to_id": b["id"], "relation": "related"})
        assert r.status_code == 200 and r.json()["ok"] is True

        lst = c.get(f"/api/v1/memories/{a['id']}/links").json()
        assert lst["links"][0]["id"] == b["id"]

        self_link = c.post(f"/api/v1/memories/{a['id']}/links",
                           json={"to_id": a["id"]})
        assert self_link.status_code == 422
        assert self_link.json()["error"] == "SELF_LINK"

        missing = c.post(f"/api/v1/memories/{a['id']}/links",
                         json={"to_id": "nope"})
        assert missing.status_code == 404

        d = c.delete(f"/api/v1/memories/{a['id']}/links/{b['id']}")
        assert d.status_code == 200 and d.json()["removed"] == 1
        assert c.get(f"/api/v1/memories/{a['id']}/links").json()["links"] == []


def test_MCP_关联工具(svc, monkeypatch):
    import kb.mcp as m
    monkeypatch.setattr(m, "_service", svc)

    a = m.write_memory("MCP 记忆 A")
    b = m.write_memory("MCP 记忆 B")
    assert m.link_memory(a["id"], b["id"])["ok"] is True
    assert m.list_links(a["id"])["links"][0]["id"] == b["id"]
    assert m.unlink_memory(a["id"], b["id"])["removed"] == 1
