"""board.py 单测：HTTP 客户端、卡片纯函数、五个子命令。"""
import json

import pytest


class TestRequest:
    """_request 走 urllib 并正确解码 JSON。"""

    def test_request_解析JSON响应(self):
        import board
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
        monkey.setattr(board.urllib.request, "urlopen", fake_urlopen)
        try:
            result = board._request("GET", "/healthz")
        finally:
            monkey.undo()
        assert result == {"status": "ok"}
        assert captured["method"] == "GET"
        assert captured["url"] == "http://127.0.0.1:8000/api/v1/healthz"

    def test_request_连接失败抛BoardUnavailable(self, monkeypatch):
        import board
        import urllib.error

        def raise_urlerror(req, timeout):
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(board.urllib.request, "urlopen", raise_urlerror)
        with pytest.raises(board.BoardUnavailable):
            board._request("GET", "/healthz")


class TestCardFunctions:
    """卡片渲染与解析纯函数。"""

    def test_render_标准卡片(self):
        import board
        content = board.render_card(
            "TASK-0001", "pending", "worker-1", "重构异常处理",
            goal="统一异常为 StorageError", input_="kb/storage.py",
            constraints="不改接口签名", acceptance="测试全绿")
        lines = content.split("\n")
        assert lines[0] == "TASK-0001 pending worker-1 | 重构异常处理"
        assert lines[1] == "目标：统一异常为 StorageError"
        assert lines[2] == "输入：kb/storage.py"
        assert lines[3] == "约束：不改接口签名"
        assert lines[4] == "验收：测试全绿"
        assert lines[5] == "结果："

    def test_parse_header_往返(self):
        import board
        header = board.parse_header("TASK-0003 claimed worker-1 | 修复空指针")
        assert header == {"task_id": "TASK-0003", "status": "claimed",
                          "assignee": "worker-1", "title": "修复空指针"}

    def test_parse_header_非法格式报错(self):
        import board
        with pytest.raises(ValueError):
            board.parse_header("这不是一张任务卡")

    def test_check_limits_超限报错(self):
        import board
        with pytest.raises(ValueError) as ei:
            board.check_limits(title="x" * 31)
        assert "title" in str(ei.value)
        # 恰好 30 字符不报错
        board.check_limits(title="x" * 30)
