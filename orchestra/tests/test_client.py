"""client.py 单测：kb REST HTTP 客户端（TASK-0028 包化①，自 test_board.py 迁移）。"""
import json

import pytest


class TestRequest:
    """_request 走 urllib 并正确解码 JSON。"""

    def test_request_解析JSON响应(self):
        import client
        payload = json.dumps({"status": "ok"}).encode("utf-8")

        class FakeResp:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        captured = {}

        def fake_urlopen(req, timeout):
            captured["method"] = req.method
            captured["url"] = req.full_url
            return FakeResp(payload)

        monkey = pytest.MonkeyPatch()
        monkey.setattr(client.urllib.request, "urlopen", fake_urlopen)
        try:
            result = client._request("GET", "/healthz")
        finally:
            monkey.undo()
        assert result == {"status": "ok"}
        assert captured["method"] == "GET"
        assert captured["url"] == "http://127.0.0.1:8000/api/v1/healthz"

    def test_request_连接失败抛BoardUnavailable(self, monkeypatch):
        import client
        import urllib.error

        def raise_urlerror(req, timeout):
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(client.urllib.request, "urlopen", raise_urlerror)
        with pytest.raises(client.BoardUnavailable):
            client._request("GET", "/healthz")
