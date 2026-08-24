"""board.py 单测：HTTP 客户端、卡片纯函数、五个子命令。"""
import json
import sys

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


def _card(content, updated_at="2026-08-24T12:30:00"):
    """构造 kb list 返回的单条记录。"""
    return {"id": "abc123", "content": content, "tags": ["taskboard"],
            "updated_at": updated_at}


class TestStatus:
    """status：列出全部任务卡，一行一卡。"""

    def test_status_一行一卡含时间与标题(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0001 pending worker-1 | 重构异常\n目标：x"),
                _card("TASK-0002 done worker-2 | 修复空指针\n目标：y",
                      updated_at="2026-08-24T09:15:00"),
            ], "total": 2}
        board.cmd_status()
        out = capsys.readouterr().out
        assert "TASK-0001 pending worker-1 12:30 重构异常" in out
        assert "TASK-0002 done worker-2 09:15 修复空指针" in out

    def test_status_空板提示(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        board.cmd_status()
        assert "无任务卡" in capsys.readouterr().out

    def test_status_非法卡片跳过并警告(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("坏卡片内容"), _card("TASK-0009 pending w1 | 正常|卡")],
            "total": 2}
        board.cmd_status()
        out = capsys.readouterr().out
        assert "TASK-0009" in out
        assert "非法" in out


class TestAdd:
    """add：字段校验、编号递增、创建调用。"""

    def test_add_创建首张卡编号0001(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "new-id"}
        board.cmd_add(assignee="worker-1", title="重构异常", goal="统一异常",
                      input_="kb/storage.py", constraints="不改接口",
                      acceptance="测试全绿")
        out = capsys.readouterr().out
        assert "TASK-0001" in out
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        body = post[2]
        assert body["tags"] == ["taskboard"]
        assert body["content"].startswith(
            "TASK-0001 pending worker-1 | 重构异常")

    def test_add_编号取最大值加一(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0007 done worker-1 | 旧卡"),
                _card("TASK-0003 pending worker-2 | 旧卡"),
            ], "total": 2}
        mock_request.responses["POST /memories"] = {"id": "x"}
        board.cmd_add(assignee="worker-1", title="t", goal="g", input_="i",
                      constraints="c", acceptance="a")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        assert post[2]["content"].startswith("TASK-0008 pending worker-1 | t")

    def test_add_字段超长拒绝(self, mock_request):
        import board
        with pytest.raises(ValueError):
            board.cmd_add(assignee="w1", title="x" * 31, goal="g",
                          input_="i", constraints="c", acceptance="a")
        assert not any(c[0] == "POST" for c in mock_request.calls)


CARD_FULL = ("TASK-0005 done worker-1 | 修复空指针\n"
             "目标：修复检索空指针\n输入：kb/retriever.py\n"
             "约束：不改接口\n验收：测试全绿\n"
             "结果：已修复第 42 行，测试通过")


class TestShow:
    def test_show_打印整卡(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_FULL)], "total": 1}
        board.cmd_show("TASK-0005")
        out = capsys.readouterr().out
        assert "修复空指针" in out and "结果：已修复" in out

    def test_show_卡不存在报错(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(SystemExit):
            board.cmd_show("TASK-0099")
        # readouterr() 只能读一次：统一读取后拼接 err+out 再断言
        captured = capsys.readouterr()
        assert "不存在" in captured.err + captured.out


class TestVerify:
    def test_verify_pass_done转verified(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_FULL)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        board.cmd_verify("TASK-0005", action="pass", note="")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        assert patch[2]["content"].startswith("TASK-0005 verified worker-1")

    def test_verify_reject_回pending带备注(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_FULL)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        board.cmd_verify("TASK-0005", action="reject", note="结果超长")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        content = patch[2]["content"]
        assert content.startswith("TASK-0005 pending worker-1")
        assert "备注：结果超长" in content

    def test_verify_仅done_failed可流转(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0006 pending worker-1 | 未完成\n目标：x")],
            "total": 1}
        with pytest.raises(SystemExit):
            board.cmd_verify("TASK-0006", action="pass", note="")


class TestNewWorker:
    def test_new_worker_输出引导语含名字与skill指令(self, capsys):
        import board
        board.cmd_new_worker("worker-1")
        out = capsys.readouterr().out
        assert "worker-1" in out
        assert "orchestra-worker" in out


class TestMain:
    def test_main_status分发(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(sys, "argv", ["board.py", "status"])
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        board.main()
        assert "无任务卡" in capsys.readouterr().out

    def test_main_服务不可达退出码2(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(sys, "argv", ["board.py", "status"])
        mock_request.responses["GET /boom*"] = None  # 触发 AssertionError 前，
        # 直接让 fake 抛 BoardUnavailable：
        monkeypatch.setattr(board, "_request",
                            lambda *a, **k: (_ for _ in ()).throw(
                                board.BoardUnavailable("down")))
        with pytest.raises(SystemExit) as ei:
            board.main()
        assert ei.value.code == 2
