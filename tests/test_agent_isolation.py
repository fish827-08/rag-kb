"""Agent 身份隔离与存取审计验收测试（A 节点 spec：2026-08-29）。

覆盖：写入归属、检索隔离、读/改/删归属校验、共享知识开放、
旧数据兼容、审计 JSON 行、REST audit 查询、CLI audit 命令。
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


# ---- 1 写入归属与检索隔离 ----

def test_写入归属_检索隔离(svc):
    a = svc.add_memory("甲的记忆：预算三百万", agent_id="agent-a")
    b = svc.add_memory("乙的记忆：成员五人", agent_id="agent-b")
    # 各自检索只看到自己的 memory
    ha = svc.search("预算", agent_id="agent-a")
    hb = svc.search("预算", agent_id="agent-b")
    assert any(r["id"] == a.id for r in ha)
    assert not any(r["id"] == b.id for r in ha)
    assert any(r["id"] == b.id for r in hb)
    assert not any(r["id"] == a.id for r in hb)


def test_默认agent归属default(svc):
    r = svc.add_memory("默认归属")
    assert r.agent_id == "default"
    assert svc.search("默认归属", agent_id="default")[0]["id"] == r.id


def test_旧数据无agent_id视为default(svc):
    # 模拟旧记录：metadata 缺 agent_id → from_chroma 回落 "default"
    from kb.models import Record, RecordType
    old = Record(content="旧记录", type=RecordType.MEMORY)
    md = old.to_metadata()
    md.pop("agent_id")  # 旧格式无该键
    md["id"] = old.id
    svc.store.add([Record(id=old.id, content=old.content,
                          type=RecordType.MEMORY, agent_id="default")],
                  [svc.embedder.embed_texts([old.content])[0]])
    # 直接构造旧格式记录并入库（agent_id 缺省 default）
    rec = Record(content="旧记录", type=RecordType.MEMORY)
    vec = svc.embedder.embed_texts([rec.content])[0]
    svc.store.add([rec], [vec])
    hits = svc.search("旧记录", agent_id="default")
    assert any(r["id"] == rec.id for r in hits)


# ---- 2 读 / 改 / 删 归属校验 ----

def test_读_memory_他人不可读(svc):
    a = svc.add_memory("私有记忆", agent_id="agent-a")
    assert svc.get_memory(a.id, agent_id="agent-a") is not None
    assert svc.get_memory(a.id, agent_id="agent-b") is None  # 非归属 → None


def test_改_他人不可改(svc):
    a = svc.add_memory("原始内容", agent_id="agent-a")
    assert svc.update_memory(a.id, "被乙改", agent_id="agent-b") is None
    assert svc.store.get(a.id).content == "原始内容"  # 未变更


def test_删_他人不可删(svc):
    a = svc.add_memory("待删记忆", agent_id="agent-a")
    assert svc.delete_memory(a.id, agent_id="agent-b") is False
    assert svc.store.get(a.id) is not None


# ---- 3 共享知识（doc/web）全部可见 ----

def test_共享文档所有agent可见(svc, tmp_path):
    f = tmp_path / "共享.txt"
    f.write_text("共享知识：项目规范", encoding="utf-8")
    svc.add_document(f, agent_id="agent-a")
    hits = svc.search("项目规范", agent_id="agent-b")
    assert hits and hits[0]["type"] == "doc_chunk"


# ---- 4 MCP 工具层隔离 ----

def test_mcp工具_agent隔离(svc):
    from kb.mcp import write_memory, search_memory, read_memory
    from kb.mcp import update_memory, delete_memory
    r = write_memory("MCP 甲私有", agent_id="agent-a")
    assert "id" in r
    # 甲可读，乙 FORBIDDEN
    assert read_memory(r["id"], agent_id="agent-a")["content"] == "MCP 甲私有"
    assert read_memory(r["id"], agent_id="agent-b")["error"] == "FORBIDDEN"
    # 乙改/删 FORBIDDEN
    assert update_memory(r["id"], "x", agent_id="agent-b")["error"] == "FORBIDDEN"
    assert delete_memory(r["id"], agent_id="agent-b")["error"] == "FORBIDDEN"
    # 甲可检索到，乙检索不到
    ha = search_memory("MCP 甲私有", agent_id="agent-a")
    assert any(x["id"] == r["id"] for x in ha)
    hb = search_memory("MCP 甲私有", agent_id="agent-b")
    assert not any(x["id"] == r["id"] for x in hb)


# ---- 5 存取审计闭环 ----

# ---- 7 身份字段规约（MCP/REST 校验 agent_id/client/project）----

def test_身份字段校验_有效与非法():
    from kb.service import validate_agent_id, validate_client, validate_project
    # 有效
    assert validate_agent_id("TASK-0076") is None
    assert validate_agent_id("worker_1", required=True) is None
    assert validate_client("Claude Code") is None
    assert validate_project("kb-mem") is None
    # 非法
    assert validate_agent_id("has space") is not None
    assert validate_agent_id("x" * 65) is not None
    assert validate_agent_id("a/b") is not None
    assert validate_client("bad/chars!") is not None
    assert validate_project("sql;drop") is not None
    # 占位/空：required=False 放行（REST 兼容），required=True 拒绝（MCP 强制）
    assert validate_agent_id("default", required=False) is None
    assert validate_agent_id("default", required=True) is not None
    assert validate_agent_id("", required=False) is None
    assert validate_agent_id("", required=True) is not None


def test_mcp_身份规约_直调非法client拒绝(svc):
    from kb.mcp import write_memory, search_memory
    # 直调（ctx=None）：agent_id 允许 default，但显式非法 client/project 拦截
    r = write_memory("合法记忆", agent_id="worker-1", client="TraeWork")
    assert "id" in r
    bad = write_memory("非法客户端", agent_id="worker-1",
                       client="bad charset!")
    assert bad["error"] == "INVALID_ARGUMENT"
    bad2 = write_memory("非法项目", agent_id="worker-1", project="p/q")
    assert bad2["error"] == "INVALID_ARGUMENT"
    bad3 = search_memory("x", agent_id="has space")
    assert bad3["error"] == "INVALID_ARGUMENT"


def test_rest_身份规约_非法字段422(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        # 合法写入
        ok = c.post("/api/v1/memories", json={
            "content": "规约测试", "agent_id": "worker-9",
            "client": "pytest", "project": "reg"})
        assert ok.status_code == 200
        # 非法 agent_id / client / project → 422
        assert c.post("/api/v1/memories", json={
            "content": "x", "agent_id": "has space"}).status_code == 422
        assert c.post("/api/v1/memories", json={
            "content": "x", "agent_id": "worker-9",
            "client": "bad!client"}).status_code == 422
        assert c.post("/api/v1/memories", json={
            "content": "x", "agent_id": "worker-9",
            "project": "bad/project"}).status_code == 422
        # 不传 identity 字段（默认 default/client）仍可用（向后兼容）
        legacy = c.post("/api/v1/memories", json={"content": "旧调用"})
        assert legacy.status_code == 200


def test_审计文件名解析_两段与三段():
    from kb.audit import parse_agent_file_name
    # 无项目：client__agent
    r1 = parse_agent_file_name("CLI__worker-1.log")
    assert r1 == {"client": "CLI", "project": "", "agent": "worker-1"}
    # 有项目：client__project__agent
    r2 = parse_agent_file_name("TraeWork__kb-mem__TASK-0076.log")
    assert r2 == {"client": "TraeWork", "project": "kb-mem", "agent": "TASK-0076"}
    # 轮转后缀剥离
    r3 = parse_agent_file_name("Claude Code__myproj__task_a.log.2026-08-29")
    assert r3 == {"client": "Claude Code", "project": "myproj", "agent": "task_a"}
    # 生成与解析往返一致（含非法字符清理：/ : 等替换为 _）
    from kb.audit import _agent_file_name
    fname = _agent_file_name("cli", "a/b", "c:d")
    status = parse_agent_file_name(fname)
    assert status["project"].startswith("a") and status["agent"].startswith("c")

def test_存取审计_json行(svc):
    from kb.audit import reset_access_logger
    reset_access_logger()
    a = svc.add_memory("审计内容" * 30, agent_id="agent-a",
                       client="TraeWork", project="kb-mem")  # 超 50 字符
    svc.get_memory(a.id, agent_id="agent-a", client="TraeWork",
                   project="kb-mem")
    svc.search("审计", agent_id="agent-a", client="TraeWork",
               project="kb-mem")
    # 按 Agent 分文件：log_dir/agent-audit/<客户端>__<项目>__<任务名>.log
    log_file = svc.settings.log_dir / "agent-audit" / "TraeWork__kb-mem__agent-a.log"
    assert log_file.is_file(), f"应生成按 Agent 分类的审计文件：{log_file}"
    lines = [json.loads(l) for l in
             log_file.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    actions = [e["action"] for e in lines]
    assert "write" in actions and "read" in actions and "search" in actions
    # 敏感红线：content 只记前 50 字符摘要，无全文
    write_evt = next(e for e in lines if e["action"] == "write")
    assert len(write_evt["content"]) == 50
    assert "审计内容" * 30 != write_evt["content"]
    # 身份由文件名承载，行内不重复记录 agent/client
    assert all("agent_id" not in e and "client" not in e for e in lines)
    # 查询侧补回身份
    items = svc.query_access_audit("agent-a")
    assert items and all(e["agent_id"] == "agent-a" for e in items)
    assert all(e["client"] == "TraeWork" for e in items)
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
    svc.add_memory("关了审计", agent_id="agent-a")
    agent_dir = svc.settings.log_dir / "agent-audit"
    if agent_dir.is_dir():
        files = list(agent_dir.glob("*.log"))
        assert not files, f"开关关闭时不应产生按 Agent 分类的审计文件：{files}"
    reset_access_logger()


# ---- 6 REST 层隔离与 audit 查询 ----

def test_rest_隔离与audit查询(env_isolated, monkeypatch):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.audit import reset_access_logger
    reset_access_logger()
    with TestClient(create_app()) as c:
        r1 = c.post("/api/v1/memories", json={
            "content": "REST 甲私有", "agent_id": "agent-a"})
        assert r1.status_code == 200
        rid = r1.json()["id"]
        # 乙读 → 404
        assert c.get(f"/api/v1/memories/{rid}",
                     params={"agent_id": "agent-b"}).status_code == 404
        # 甲读 → 200
        assert c.get(f"/api/v1/memories/{rid}",
                     params={"agent_id": "agent-a"}).status_code == 200
        # 检索隔离
        ra = c.post("/api/v1/search", json={
            "query": "REST 甲私有", "agent_id": "agent-a"})
        rb = c.post("/api/v1/search", json={
            "query": "REST 甲私有", "agent_id": "agent-b"})
        assert any(x["id"] == rid for x in ra.json()["results"])
        assert not any(x["id"] == rid for x in rb.json()["results"])
        # audit 查询端点
        aud = c.get("/api/v1/audit", params={"agent": "agent-a"})
        assert aud.status_code == 200
        items = aud.json()["items"]
        assert items, "agent-a 应有存取审计记录"
        assert all(e["agent_id"] == "agent-a" for e in items)
    reset_access_logger()


def test_audit_参数校验(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        assert c.get("/api/v1/audit", params={"agent": " "}).status_code == 422