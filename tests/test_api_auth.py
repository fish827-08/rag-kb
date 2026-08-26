"""N19 API Key 鉴权中间件单测（TASK-0062，spec §7 全部 9 用例）。

用 FastAPI TestClient；构造带/不带 api_key 的 Settings 验证鉴权行为。
覆盖：空 key 直通 / 无凭证 401 / 错误 key 401 / Bearer 正确放行 /
X-API-Key 正确放行 / healthz 白名单 / MCP 同栈覆盖 / 401 响应格式 /
compare_digest 防时序攻击。
"""
import inspect

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client_no_key(env_isolated):
    """空 key（默认）：不鉴权，等同 v1.x 行为。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.config import Settings
    with TestClient(create_app(settings=Settings(api_key=""))) as c:
        yield c


@pytest.fixture
def client_with_key(env_isolated):
    """有 key：启用鉴权，要求 Bearer/X-API-Key。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.config import Settings
    with TestClient(create_app(settings=Settings(api_key="secret123"))) as c:
        yield c


def test_空key_不鉴权_所有端点放行(client_no_key):
    """空 key 时不带 key 请求 /api/v1/memories 返回 200（而非 401）。"""
    r = client_no_key.get("/api/v1/memories")
    assert r.status_code == 200
    assert "items" in r.json()


def test_有key_无凭证_返回401(client_with_key):
    """有 key 时无 Authorization/X-API-Key → 401 JSON。"""
    r = client_with_key.get("/api/v1/memories")
    assert r.status_code == 401
    assert r.json()["error"] == "UNAUTHORIZED"


def test_有key_错误key_返回401(client_with_key):
    """有 key 时 X-API-Key 错误 → 401（不区分缺失与错误，防探测）。"""
    r = client_with_key.get("/api/v1/memories", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_有key_Bearer正确_放行(client_with_key):
    """有 key 时 Authorization: Bearer <正确key> → 200。"""
    r = client_with_key.get("/api/v1/memories",
                            headers={"Authorization": "Bearer secret123"})
    assert r.status_code == 200


def test_有key_XAPIKey正确_放行(client_with_key):
    """有 key 时 X-API-Key: <正确key> → 200（Bearer 优先，X-API-Key 次之）。"""
    r = client_with_key.get("/api/v1/memories",
                            headers={"X-API-Key": "secret123"})
    assert r.status_code == 200


def test_healthz_白名单_有key也放行(client_with_key):
    """有 key 时 GET /api/v1/healthz 无 header → 200（存活探针白名单）。"""
    r = client_with_key.get("/api/v1/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_mcp端点_有key无凭证_401(client_with_key):
    """/mcp/ 无 key → 401（验证 ASGI 中间件同栈覆盖 mount 的 MCP 子应用）。"""
    r = client_with_key.post("/mcp/", json={"jsonrpc": "2.0", "id": 1,
                                              "method": "tools/list"})
    assert r.status_code == 401


def test_401响应格式(client_with_key):
    """401 体为 {"error":"UNAUTHORIZED","message":...}，Content-Type 含 charset=utf-8。"""
    r = client_with_key.get("/api/v1/memories")
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == "UNAUTHORIZED"
    assert "missing or invalid api key" in body["message"]
    assert "charset=utf-8" in r.headers["content-type"].lower()


def test_key比较用compare_digest():
    """代码审查：_wrap_api_key 使用 hmac.compare_digest（不写 == 明文比较，防时序攻击）。"""
    from kb import api
    src = inspect.getsource(api._wrap_api_key)
    assert "hmac.compare_digest" in src
    # 中间件内 compare_digest 是唯一比较路径；无 provided_key == api_key 明文比较
    assert "provided_key == api_key" not in src
