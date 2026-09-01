import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def msvc(env_isolated):
    from kb.service import KBService
    return KBService()


def test_八个工具函数全部注册(msvc):
    from kb.mcp import create_mcp_server
    mcp = create_mcp_server(msvc)
    tools = {t.name for t in mcp._tool_manager.list_tools()}
    assert tools == {"write_memory", "search_memory", "read_memory", "update_memory",
                     "delete_memory", "add_document", "add_webpage", "ask_kb"}


def test_服务器注入全局instructions_且双语按语言切换(msvc, monkeypatch):
    """v3：MCPServer instructions 承载全局接入规约（跨客户端全局提示词）；
    中英双语按 detect_lang()（KB_LANG / 系统 locale）选一套注入。"""
    from kb import i18n
    from kb.i18n import mcp_instructions

    # 强制中文
    monkeypatch.setattr(i18n, "detect_lang", lambda: "zh")
    zh = mcp_instructions()
    assert "直接调用" in zh and "已记住你的偏好" in zh and "无需" in zh

    # 强制英文
    monkeypatch.setattr(i18n, "detect_lang", lambda: "en")
    en = mcp_instructions()
    assert "Noted your preference" in en and "no health check" in en

    # zh/en 是两套不同文案
    assert zh != en

    # 服务器注入：instructions 与语言检测一致（中文系统）
    monkeypatch.setattr(i18n, "detect_lang", lambda: "zh")
    from kb.mcp import create_mcp_server
    mcp = create_mcp_server(msvc)
    assert mcp.instructions and "已记住你的偏好" in mcp.instructions

    # 核心规则必须存在：不先做健康探测 / 失败才反馈原因
    assert "health check" in en or "健康检查" in zh
    assert "重复" in zh and "敏感" in zh


def test_记忆工具链(msvc):
    from kb.mcp import write_memory, read_memory, update_memory, delete_memory, search_memory
    r = write_memory("MCP写入的记忆")  # v2：无 agent_id 入参
    assert "id" in r
    assert read_memory(r["id"])["content"] == "MCP写入的记忆"
    update_memory(r["id"], "MCP更新的记忆")
    hits = search_memory("MCP更新")
    assert hits and hits[0]["id"] == r["id"]
    assert delete_memory(r["id"])["ok"] is True


def test_未就绪工具(msvc):
    from kb.mcp import add_document, add_webpage
    # add_document 已于 N13 接通（计划 L1100："N13 前返回 NOT_READY"）：
    # 不存在的文件返回 FILE_NOT_FOUND；add_webpage 已于 N14 接通
    # （计划 L1101："N14 前返回 NOT_READY"）：不可达 URL 返回 WEB_FETCH_FAILED
    assert add_document("x.pdf")["error"] == "FILE_NOT_FOUND"
    assert add_webpage("http://x")["error"] == "WEB_FETCH_FAILED"


def test_应用挂载冒烟(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        assert c.get("/api/v1/healthz").status_code == 200
