"""记忆范围重构验收测试（v2 spec：2026-08-30）。

覆盖：写入归属 (client, project)、检索隔离、读/改/删归属校验、默认桶、
共享知识开放、旧数据兼容、主键服务端生成、审计文件名 client__project、
REST/CLI audit 查询、身份字段规约。
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


# ---- 1 写入归属 (client, project) 与检索隔离 ----

def test_写入归属_检索隔离(svc):
    a = svc.add_memory("甲的记忆：预算三百万", client="TraeWork", project="proj-a")
    b = svc.add_memory("乙的记忆：成员五人", client="TraeWork", project="proj-b")
    # 同客户端不同项目：各检索只看到自己的 memory
    ha = svc.search("预算", client="TraeWork", project="proj-a")
    hb = svc.search("预算", client="TraeWork", project="proj-b")
    assert any(r["id"] == a.id for r in ha)
    assert not any(r["id"] == b.id for r in ha)
    assert any(r["id"] == b.id for r in hb)
    assert not any(r["id"] == a.id for r in hb)
    # 不同客户端同项目也隔离
    hc = svc.search("预算", client="Cursor", project="proj-a")
    assert not any(r["id"] == a.id for r in hc)


def test_默认桶_无project归属该客户端(svc):
    r = svc.add_memory("默认桶记忆", client="CLI")  # project 空 → 默认桶
    assert r.project == ""
    assert svc.search("默认桶记忆", client="CLI")[0]["id"] == r.id
    # 同客户端带项目检索不到默认桶记录
    hits = svc.search("默认桶记忆", client="CLI", project="kb")
    assert not any(x["id"] == r.id for x in hits)
    # 不同客户端默认桶检索不到
    hits2 = svc.search("默认桶记忆", client="Cursor")
    assert not any(x["id"] == r.id for x in hits2)


def test_主键服务端生成(svc):
    r = svc.add_memory("主键服务端生成")
    assert r.id and r.id != "default"  # uuid 主键，不依赖调用方
    assert len(r.id) == 32  # uuid4().hex


# ---- 2 读 / 改 / 删 归属校验 ----

def test_读_memory_他人不可读(svc):
    a = svc.add_memory("私有记忆", client="TraeWork", project="proj-a")
    assert svc.get_memory(a.id, client="TraeWork", project="proj-a") is not None
    assert svc.get_memory(a.id, client="TraeWork", project="proj-b") is None
    assert svc.get_memory(a.id, client="Cursor", project="proj-a") is None


def test_改_他人不可改(svc):
    a = svc.add_memory("原始内容", client="TraeWork", project="proj-a")
    assert svc.update_memory(a.id, "被乙改", client="TraeWork",
                             project="proj-b") is None
    assert svc.store.get(a.id).content == "原始内容"  # 未变更


def test_删_他人不可删(svc):
    a = svc.add_memory("待删记忆", client="TraeWork", project="proj-a")
    assert svc.delete_memory(a.id, client="Cursor", project="proj-a") is False
    assert svc.store.get(a.id) is not None


# ---- 3 共享知识（doc/web）全部可见 ----

def test_共享文档所有客户端可见(svc, tmp_path):
    f = tmp_path / "共享.txt"
    f.write_text("共享知识：项目规范", encoding="utf-8")
    svc.add_document(f, client="TraeWork", project="proj-a")
    hits = svc.search("项目规范", client="Cursor", project="other")
    assert hits and hits[0]["type"] == "doc_chunk"


# ---- 4 MCP 工具层隔离（v2：无 agent_id 入参）----

def test_mcp工具_双键隔离(svc):
    from kb.mcp import write_memory, search_memory, read_memory
    from kb.mcp import update_memory, delete_memory
    r = write_memory("MCP 甲私有")  # 直调：client 兜底 default、project 默认桶
    assert "id" in r
    # 无身份参数即可读写（默认桶）
    assert read_memory(r["id"])["content"] == "MCP 甲私有"
    assert search_memory("MCP 甲私有")[0]["id"] == r["id"]
    # 显式不同项目 → FORBIDDEN
    assert read_memory(r["id"], project="other")["error"] == "FORBIDDEN"
    assert update_memory(r["id"], "x", project="other")["error"] == "FORBIDDEN"
    assert delete_memory(r["id"], project="other")["error"] == "FORBIDDEN"
    # 带 project 写入 → 归属该桶，仅该桶可检索
    rp = write_memory("MCP 项目私有", project="proj-a")
    assert search_memory("MCP 项目私有", project="proj-a")[0]["id"] == rp["id"]
    assert not any(x["id"] == rp["id"]
                   for x in search_memory("MCP 项目私有", project="proj-b"))


def test_default桶_审计文件名(svc):
    from kb.audit import reset_access_logger, _agent_file_name
    reset_access_logger()
    assert _agent_file_name("TraeWork", "") == "TraeWork__default.log"
    assert _agent_file_name("TraeWork", "kb") == "TraeWork__kb.log"
    # client 空格保留（Claude Code 合法）；client 的非法字符清理
    assert _agent_file_name("Claude Code", "p/q") == "Claude Code__p_q.log"
    assert _agent_file_name("htt:p", "") == "htt_p__default.log"
    reset_access_logger()


# ---- 5 存取审计闭环（v2：client__project 文件名）----

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


# ---- 6 REST 层隔离与 audit 查询（v2）----

def test_rest_隔离与audit查询(env_isolated, monkeypatch):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.audit import reset_access_logger
    reset_access_logger()
    with TestClient(create_app()) as c:
        r1 = c.post("/api/v1/memories", json={
            "content": "REST 甲私有", "client": "HTTP", "project": "proj-a"})
        assert r1.status_code == 200
        rid = r1.json()["id"]
        # 同 project 可读（不解体 agent_id）
        assert c.get(f"/api/v1/memories/{rid}",
                     params={"client": "HTTP",
                             "project": "proj-a"}).status_code == 200
        # 不同 project 读 → 404
        assert c.get(f"/api/v1/memories/{rid}",
                     params={"client": "HTTP",
                             "project": "proj-b"}).status_code == 404
        # 检索隔离
        ra = c.post("/api/v1/search", json={
            "query": "REST 甲私有", "client": "HTTP", "project": "proj-a"})
        rb = c.post("/api/v1/search", json={
            "query": "REST 甲私有", "client": "HTTP", "project": "proj-b"})
        assert any(x["id"] == rid for x in ra.json()["results"])
        assert not any(x["id"] == rid for x in rb.json()["results"])
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


# ---- 7 身份字段规约（v2：client/project，agent_id 仅兼容）----

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
        # 不传 identity 字段（默认 client=HTTP、project 空默认桶）仍可用
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


# ---- 8 时钟注入（遗忘机制验证：decay 可拨到第 N 天）----

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