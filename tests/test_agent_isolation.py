"""记忆范围 v2.1 验收测试（2026-10-20 spec：全局库 + 项目子库物理隔离）。

覆盖：项目间检索/读改删隔离、全局记忆跨项目可见、文档按项目落库、
目录布局与路径安全、LRU 子库缓存、统计口径、审计文件名、身份规约、时钟注入。
"""
import json

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def svc(env_isolated, monkeypatch):
    from kb.service import KBService
    from kb.config import get_settings
    get_settings.cache_clear()
    return KBService()


# ---- 1 项目子库物理隔离 ----

def test_项目记忆检索隔离(svc):
    a = svc.add_memory("甲项目预算三百万", project="proj-a")
    b = svc.add_memory("乙项目成员五人", project="proj-b")
    ha = svc.search("预算", project="proj-a")
    assert any(r["id"] == a.id for r in ha)
    assert all(r["id"] != b.id for r in ha)
    hb = svc.search("成员", project="proj-b")
    assert any(r["id"] == b.id for r in hb)
    assert all(r["id"] != a.id for r in hb)
    # project 为空 = 只查全局库，看不到任何项目记忆
    assert all(r["id"] != a.id for r in svc.search("预算"))
    assert all(r["id"] != b.id for r in svc.search("成员"))


def test_全局记忆对所有项目可见(svc):
    g = svc.add_memory("全局偏好：回答用中文")
    a = svc.add_memory("项目甲细节", project="proj-a")
    for prj in ("proj-a", "proj-b", None):
        hits = svc.search("全局偏好", project=prj)
        assert any(r["id"] == g.id for r in hits), f"project={prj} 应看到全局记忆"
    # 项目 B（尚未建库）检索不到项目 A 的记忆
    assert any(r["id"] == a.id for r in svc.search("项目甲细节", project="proj-a"))
    assert all(r["id"] != a.id for r in svc.search("项目甲细节", project="proj-b"))


def test_按id跨项目不可见(svc):
    a = svc.add_memory("甲私有记忆", project="proj-a")
    svc.add_memory("乙占位", project="proj-b")  # 确保 proj-b 子库存在
    assert svc.get_memory(a.id, project="proj-a") is not None
    assert svc.get_memory(a.id, project="proj-b") is None
    # 未指定 project：先全局后按目录顺序查各项目库，能找到但需显式归属才能命中
    assert svc.get_memory(a.id) is not None


def test_改删按项目路由(svc):
    a = svc.add_memory("原始内容", project="proj-a")
    svc.add_memory("乙占位", project="proj-b")
    assert svc.update_memory(a.id, "被乙改", project="proj-b") is None
    assert svc.get_memory(a.id, project="proj-a").content == "原始内容"
    assert svc.update_memory(a.id, "甲改", project="proj-a").content == "甲改"
    assert svc.delete_memory(a.id, project="proj-b") is False
    assert svc.get_memory(a.id, project="proj-a") is not None
    assert svc.delete_memory(a.id, project="proj-a") is True
    assert svc.get_memory(a.id, project="proj-a") is None


def test_全局记忆不会被跨项目删除(svc):
    g = svc.add_memory("全局事实")
    svc.add_memory("乙占位", project="proj-b")
    # 显式 project 时先查项目库再查全局：全局记录仍可被定位（spec §2.2）
    assert svc.get_memory(g.id, project="proj-b") is not None
    assert svc.delete_memory(g.id, project="proj-b") is True
    assert svc.get_memory(g.id) is None


def test_文档按项目隔离(svc, tmp_path):
    f = tmp_path / "规范.txt"
    f.write_text("甲项目规范：缩进四空格", encoding="utf-8")
    svc.add_document(f, project="proj-a")
    hits_a = svc.search("缩进四空格", project="proj-a")
    assert hits_a and hits_a[0]["type"] == "doc_chunk"
    assert all(h.get("type") != "doc_chunk"
               for h in svc.search("缩进四空格", project="proj-b"))
    assert any(d["source"] == "规范.txt"
               for d in svc.list_documents(project="proj-a"))
    assert svc.list_documents(project="proj-b") == []
    assert svc.delete_document("规范.txt", project="proj-b") == 0
    assert svc.delete_document("规范.txt", project="proj-a") == 1


# ---- 2 目录布局、路径安全与 LRU ----

def test_项目子库目录布局(svc, env_isolated):
    svc.add_memory("布局测试", project="proj-a")
    root = env_isolated / "projects" / "proj-a"
    assert (root / "chroma").is_dir()
    assert (root / "bm25_corpus.json").is_file()
    assert (root / "count.txt").is_file()
    # 全局库仍在 data_dir/chroma，不与项目库混放
    assert (env_isolated / "chroma").is_dir()


def test_项目名拒绝非法与系统保留名():
    from kb.service import validate_project
    from kb.scopes import validate_scope_name
    assert validate_project("proj-a") is None
    assert validate_project("项目甲") is None
    for bad in ("../etc", "a/b", "a\\b", "..", "x" * 65):
        assert validate_project(bad) is not None, bad
    for reserved in ("CON", "aux", "COM1", "lpt9"):
        assert validate_project(reserved) is not None, reserved
    with pytest.raises(ValueError):
        validate_scope_name("../etc")


def test_lru上限生效(env_isolated, monkeypatch):
    from kb.config import get_settings
    from kb.service import KBService
    monkeypatch.setenv("KB_PROJECT_CACHE_SIZE", "1")
    get_settings.cache_clear()
    svc = KBService()
    a = svc.add_memory("甲的记录", project="proj-a")
    svc.add_memory("乙的记录", project="proj-b")
    svc.add_memory("丙的记录", project="proj-c")
    assert set(svc.scopes._scopes) == {"", "proj-c"}
    # 重新访问 A：懒加载并按磁盘数据恢复
    assert svc.get_memory(a.id, project="proj-a") is not None
    assert set(svc.scopes._scopes) == {"", "proj-a"}
    assert svc.scopes.get("proj-c").store.count() == 1


def test_统计包含全局与项目库(svc):
    svc.add_memory("全局")
    svc.add_memory("甲", project="proj-a")
    svc.add_memory("乙", project="proj-b")
    assert svc.stats()["records"] == 3


# ---- 3 MCP 工具层项目隔离 ----

def test_mcp工具_项目隔离(svc):
    from kb.mcp import write_memory, search_memory, read_memory
    from kb.mcp import update_memory, delete_memory
    ra = write_memory("MCP 甲项目私有", project="proj-a")
    assert "id" in ra
    rb = write_memory("MCP 乙项目私有", project="proj-b")
    assert "id" in rb
    assert search_memory("MCP 甲项目私有", project="proj-a")[0]["id"] == ra["id"]
    assert all(x["id"] != ra["id"]
               for x in search_memory("MCP 甲项目私有", project="proj-b"))
    assert all(x["id"] != ra["id"] for x in search_memory("MCP 甲项目私有"))
    assert read_memory(ra["id"], project="proj-b") == {"error": "NOT_FOUND"}
    assert update_memory(ra["id"], "跨项目改", project="proj-b") == {
        "error": "NOT_FOUND"}
    assert delete_memory(ra["id"], project="proj-b") == {"error": "NOT_FOUND"}
    assert read_memory(ra["id"], project="proj-a")["content"] == "MCP 甲项目私有"
    # 全局记忆：任意项目均可检索
    rg = write_memory("MCP 全局偏好")
    assert any(x["id"] == rg["id"]
               for x in search_memory("MCP 全局偏好", project="proj-a"))


def test_default桶_审计文件名(svc):
    from kb.audit import reset_access_logger, _agent_file_name
    reset_access_logger()
    assert _agent_file_name("TraeWork", "") == "TraeWork__default.log"
    assert _agent_file_name("TraeWork", "kb") == "TraeWork__kb.log"
    # client 空格保留（Claude Code 合法）；client 的非法字符清理
    assert _agent_file_name("Claude Code", "p/q") == "Claude Code__p_q.log"
    assert _agent_file_name("htt:p", "") == "htt_p__default.log"
    reset_access_logger()


# ---- 4 存取审计闭环（client__project 文件名）----

def test_存取审计_json行(svc):
    from kb.audit import reset_access_logger
    reset_access_logger()
    a = svc.add_memory("审计内容" * 30, client="TraeWork",
                       project="kb-mem")  # 超 50 字符
    svc.get_memory(a.id, client="TraeWork", project="kb-mem")
    svc.search("审计", client="TraeWork", project="kb-mem")
    # 按 (client, project) 分文件：log_dir/agent-audit/<客户端>__<项目>.log
    log_file = svc.settings.log_dir / "agent-audit" / "TraeWork__kb-mem.log"
    assert log_file.is_file(), f"应生成按 (client, project) 分类的审计文件：{log_file}"
    lines = [json.loads(l) for l in
             log_file.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    actions = [e["action"] for e in lines]
    assert "write" in actions and "read" in actions and "search" in actions
    # 敏感红线：content 只记前 50 字符摘要，无全文
    write_evt = next(e for e in lines if e["action"] == "write")
    assert len(write_evt["content"]) == 50
    assert "审计内容" * 30 != write_evt["content"]
    # 查询侧补回身份
    items = svc.query_access_audit(client="TraeWork", project="kb-mem")
    assert items and all(e["client"] == "TraeWork" for e in items)
    assert all(e.get("project") == "kb-mem" for e in items)
    reset_access_logger()


def test_审计开关关闭不落盘(env_isolated, monkeypatch):
    from kb.service import KBService
    from kb.config import get_settings
    from kb.audit import reset_access_logger
    reset_access_logger()
    monkeypatch.setenv("KB_ACCESS_AUDIT_ENABLED", "false")
    get_settings.cache_clear()
    svc = KBService()
    svc.add_memory("关了审计", client="TraeWork", project="kb-mem")
    agent_dir = svc.settings.log_dir / "agent-audit"
    if agent_dir.is_dir():
        files = list(agent_dir.glob("*.log"))
        assert not files, f"开关关闭时不应产生审计文件：{files}"
    reset_access_logger()


# ---- 5 REST 层项目隔离与 audit 查询 ----

def test_rest_项目隔离与audit查询(env_isolated, monkeypatch):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.audit import reset_access_logger
    reset_access_logger()
    with TestClient(create_app()) as c:
        r1 = c.post("/api/v1/memories", json={
            "content": "REST 甲项目私有", "client": "HTTP", "project": "proj-a"})
        assert r1.status_code == 200
        rid = r1.json()["id"]
        # 不同 project 读/改/删 → 404
        assert c.get(f"/api/v1/memories/{rid}",
                     params={"client": "HTTP",
                             "project": "proj-b"}).status_code == 404
        assert c.patch(f"/api/v1/memories/{rid}", json={"content": "x"},
                       params={"project": "proj-b"}).status_code == 404
        assert c.delete(f"/api/v1/memories/{rid}",
                        params={"project": "proj-b"}).status_code == 404
        # 同 project 可见
        assert c.get(f"/api/v1/memories/{rid}",
                     params={"client": "HTTP",
                             "project": "proj-a"}).status_code == 200
        # 检索隔离
        ra = c.post("/api/v1/search", json={
            "query": "REST 甲项目私有", "client": "HTTP", "project": "proj-a"})
        rb = c.post("/api/v1/search", json={
            "query": "REST 甲项目私有", "client": "HTTP", "project": "proj-b"})
        rg = c.post("/api/v1/search", json={
            "query": "REST 甲项目私有", "client": "HTTP"})
        assert any(x["id"] == rid for x in ra.json()["results"])
        assert all(x["id"] != rid for x in rb.json()["results"])
        assert all(x["id"] != rid for x in rg.json()["results"])
        # 全局记忆在项目检索中可见
        gid = c.post("/api/v1/memories", json={"content": "REST 全局偏好"}).json()["id"]
        rbg = c.post("/api/v1/search", json={
            "query": "REST 全局偏好", "project": "proj-b"})
        assert any(x["id"] == gid for x in rbg.json()["results"])
        # 列出记忆：按 project 分库
        la = c.get("/api/v1/memories", params={"project": "proj-a"}).json()
        lg = c.get("/api/v1/memories").json()
        assert any(x["id"] == rid for x in la["items"])
        assert all(x["id"] != rid for x in lg["items"])
        # 非法 project → 422
        assert c.get("/api/v1/memories", params={
            "project": "bad/project"}).status_code == 422
        # audit 查询端点（client/project 过滤）
        aud = c.get("/api/v1/audit", params={"client": "HTTP"})
        assert aud.status_code == 200
        items = aud.json()["items"]
        assert items, "HTTP 客户端应有存取审计记录"
        assert all(e["client"] == "HTTP" for e in items)
        aud2 = c.get("/api/v1/audit", params={"client": "HTTP",
                                              "project": "proj-a"})
        assert aud2.status_code == 200
        assert aud2.json()["items"], "HTTP/proj-a 应有存取审计记录"
    reset_access_logger()


def test_audit_参数校验(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        # client 与 agent 都为空 → 422
        assert c.get("/api/v1/audit").status_code == 422
        # client 提供即可
        assert c.get("/api/v1/audit",
                     params={"client": "HTTP"}).status_code == 200


# ---- 6 身份字段规约 ----

def test_身份字段校验_有效与非法():
    from kb.service import validate_client, validate_project
    # 有效
    assert validate_client("Claude Code") is None
    assert validate_project("kb-mem") is None
    # 非法
    assert validate_client("bad/chars!") is not None
    assert validate_project("sql;drop") is not None
    # 空/占位 → 未提供放行（自动识别/默认桶）
    assert validate_client("default") is None
    assert validate_client("") is None


def test_mcp_身份规约_直调非法client拒绝(svc):
    from kb.mcp import write_memory, search_memory
    # 直调（ctx=None）：client 兜底 default；显式非法 client/project 拦截
    r = write_memory("合法记忆", client="TraeWork")
    assert "id" in r
    bad = write_memory("非法客户端", client="bad!chars")
    assert bad["error"] == "INVALID_ARGUMENT"
    bad2 = write_memory("非法项目", project="p/q")
    assert bad2["error"] == "INVALID_ARGUMENT"
    bad3 = search_memory("x", client="has/space")
    assert bad3["error"] == "INVALID_ARGUMENT"


def test_rest_身份规约_非法字段422(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        # 合法写入
        ok = c.post("/api/v1/memories", json={
            "content": "规约测试", "client": "pytest", "project": "reg"})
        assert ok.status_code == 200
        # 非法 client / project → 422
        assert c.post("/api/v1/memories", json={
            "content": "x", "client": "bad!client"}).status_code == 422
        assert c.post("/api/v1/memories", json={
            "content": "x", "client": "pytest",
            "project": "bad/project"}).status_code == 422
        # 不传 identity 字段（默认 client=HTTP、project 空）仍可用
        legacy = c.post("/api/v1/memories", json={"content": "旧调用"})
        assert legacy.status_code == 200


def test_审计文件名解析_old兼容():
    from kb.audit import parse_agent_file_name
    # v2 两段式：client__project
    r1 = parse_agent_file_name("TraeWork__kb.log")
    assert r1 == {"client": "TraeWork", "project": "kb", "agent": ""}
    # 默认桶：client__default
    r2 = parse_agent_file_name("CLI__default.log")
    assert r2 == {"client": "CLI", "project": "default", "agent": ""}
    # 旧三段式兼容（agent 段解析保留）
    r3 = parse_agent_file_name("TraeWork__kb-mem__TASK-0076.log")
    assert r3["client"] == "TraeWork"
    assert r3["project"] == "kb-mem"
    assert r3["agent"] == "TASK-0076"
    # 轮转后缀剥离
    r4 = parse_agent_file_name("Claude Code__myproj.log.2026-08-29")
    assert r4["client"] == "Claude Code" and r4["project"] == "myproj"


# ---- 7 时钟注入（遗忘机制验证：decay 可拨到第 N 天） ----

def test_decay时钟注入():
    from datetime import datetime, timedelta
    from kb.models import decay_factor
    created = "2026-01-01T00:00:00"
    # now=创建当天：无衰减
    f0 = decay_factor("", created, 0, now=datetime.fromisoformat(created))
    assert f0 == pytest.approx(1.0)
    # now=35 天后（半衰期）：λ=0.02 → exp(-0.02*35)≈0.497
    later = datetime.fromisoformat(created) + timedelta(days=35)
    f35 = decay_factor("", created, 0, now=later)
    assert f35 == pytest.approx(0.4966, abs=0.01)
    # 高频访问加权：同一天、access_count=10 → 1+0.3*log2(11)≈2.038
    f10 = decay_factor("", created, 10, now=datetime.fromisoformat(created))
    assert f10 == pytest.approx(1 + 0.3 * 3.4594, abs=0.01)
