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
