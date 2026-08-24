"""orchestra 测试配置：把 orchestra/ 加入 sys.path，并提供 _request mock。"""
import sys
from pathlib import Path

import pytest

ORCHESTRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCHESTRA_DIR))


@pytest.fixture
def mock_request(monkeypatch):
    """拦截 board._request：记录调用并返回预置响应。

    用法：先设 mock_request.responses = {"GET /memories": {...}}，
    断言 mock_request.calls == [("GET", "/memories", body), ...]。
    """
    import board

    calls = []
    responses: dict = {}

    def fake(method, path, body=None):
        calls.append((method, path, body))
        key = f"{method} {path}"
        if key in responses:
            return responses[key]
        for pattern, resp in responses.items():
            if pattern.endswith("*") and key.startswith(pattern[:-1]):
                return resp
        raise AssertionError(f"未预置的请求：{key}")

    monkeypatch.setattr(board, "_request", fake)
    holder = type("Mock", (), {})()
    holder.calls = calls
    holder.responses = responses
    return holder
