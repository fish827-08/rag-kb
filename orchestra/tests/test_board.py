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


class TestListPending:
    """list-pending：只显示 pending 状态的任务卡。"""

    def test_list_pending_只列pending过滤其他状态(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0001 pending worker-1 | 待办甲\n目标：x"),
                _card("TASK-0002 claimed worker-2 | 进行中\n目标：y",
                      updated_at="2026-08-24T09:15:00"),
                _card("TASK-0003 done worker-1 | 已完成\n目标：z"),
                _card("TASK-0004 verified worker-2 | 已核验\n目标：w"),
                _card("TASK-0005 failed worker-1 | 已失败\n目标：v"),
            ], "total": 5}
        board.cmd_list_pending()
        out = capsys.readouterr().out
        assert "TASK-0001 pending worker-1 12:30 待办甲" in out
        assert "TASK-0002" not in out
        assert "TASK-0003" not in out
        assert "TASK-0004" not in out
        assert "TASK-0005" not in out

    def test_list_pending_全是非pending时明确提示(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0002 claimed worker-2 | 进行中\n目标：y"),
                _card("TASK-0003 done worker-1 | 已完成\n目标：z"),
            ], "total": 2}
        board.cmd_list_pending()
        out = capsys.readouterr().out
        assert "无待办任务卡" in out
        assert "TASK-0002" not in out and "TASK-0003" not in out

    def test_list_pending_空板提示(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        board.cmd_list_pending()
        assert "无待办任务卡" in capsys.readouterr().out


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


class TestClaim:
    """claim：pending→claimed 并更新 assignee；非 pending 拒绝。"""

    def test_claim_pending卡成功转claimed且更新assignee(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0010 pending worker-1 | 待认领\n目标：x")],
            "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        board.cmd_claim("TASK-0010", assignee="worker-2")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        content = patch[2]["content"]
        assert content.startswith("TASK-0010 claimed worker-2 | 待认领")
        # 其余字段原样保留
        assert "目标：x" in content

    def test_claim_非pending卡报错不误改(self, mock_request):
        import board
        for status in ("claimed", "done", "failed", "verified"):
            mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
                "items": [_card(f"TASK-0010 {status} worker-1 | 进行中\n目标：x")],
                "total": 1}
            with pytest.raises(SystemExit):
                board.cmd_claim("TASK-0010", assignee="worker-2")
            # 没有发出 PATCH
            assert not any(c[0] == "PATCH" for c in mock_request.calls)

    def test_claim_卡不存在报错(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(SystemExit):
            board.cmd_claim("TASK-0099", assignee="worker-2")


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

    def test_main_list_pending分发(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(sys, "argv", ["board.py", "list-pending"])
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0001 pending worker-1 | 待办\n目标：x")],
            "total": 1}
        board.main()
        assert "TASK-0001 pending worker-1 12:30 待办" in \
            capsys.readouterr().out

    def test_main_claim分发(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(sys, "argv",
                            ["board.py", "claim", "TASK-0001", "--assignee", "worker-2"])
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0001 pending worker-1 | 待办\n目标：x")],
            "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        board.main()
        out = capsys.readouterr().out
        assert "TASK-0001" in out and "claimed" in out

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

    def test_main_register分发(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(
            sys, "argv",
            ["board.py", "register", "worker-2", "--model", "豆包", "--client", "Doubao"])
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "r1"}
        board.main()
        assert "已注册" in capsys.readouterr().out

    def test_main_workers分发(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(sys, "argv", ["board.py", "workers"])
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        board.main()
        assert "无已注册 worker" in capsys.readouterr().out

    def test_main_report分发(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(
            sys, "argv",
            ["board.py", "report", "--channel", "done", "--from",
             "worker-1", "--text", "hi"])
        mock_request.responses["POST /memories"] = {"id": "c1"}
        board.main()
        assert "comm:done" in capsys.readouterr().out

    def test_main_list_comm分发(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(sys, "argv", ["board.py", "list-comm"])
        mock_request.responses["GET /memories?limit=1000"] = {
            "items": [], "total": 0}
        board.main()
        assert "无交流窗记录" in capsys.readouterr().out

    def test_main_watch分发(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(sys, "argv", ["board.py", "watch", "--once"])
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        board.main()
        assert "无已注册 worker" in capsys.readouterr().out


REG_JSON_1 = ('{"worker": "worker-1", "model": "GLM-5.3", "client": "TraeWork", '
              '"registered_at": "2026-08-24T23:00", "last_seen": "2026-08-25T10:00", '
              '"status": "idle"}')


class TestRegister:
    """register：注册/刷新 worker 身份（tag=registry）。"""

    def test_register_新worker写入registry(self, mock_request, monkeypatch):
        import board
        monkeypatch.setattr(board, "_now_iso", lambda: "2026-08-25T10:30")
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "new-id"}
        board.cmd_register("worker-2", model="豆包", client="Doubao")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        body = post[2]
        assert body["tags"] == ["registry"]
        data = json.loads(body["content"])
        assert data["worker"] == "worker-2"
        assert data["model"] == "豆包"
        assert data["client"] == "Doubao"
        assert data["status"] == "idle"
        assert data["registered_at"] == "2026-08-25T10:30"
        assert data["last_seen"] == "2026-08-25T10:30"

    def test_register_重复注册刷新last_seen不重复建卡(self, mock_request, monkeypatch):
        import board
        monkeypatch.setattr(board, "_now_iso", lambda: "2026-08-25T11:00")
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card(REG_JSON_1)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        board.cmd_register("worker-1", model="GLM-6.0", client="TraeWork")
        assert not any(c[0] == "POST" for c in mock_request.calls)  # 不重复建卡
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        data = json.loads(patch[2]["content"])
        assert data["worker"] == "worker-1"
        assert data["model"] == "GLM-6.0"                      # 身份刷新
        assert data["registered_at"] == "2026-08-24T23:00"     # 保留首登时间
        assert data["last_seen"] == "2026-08-25T11:00"         # last_seen 刷新

    def test_register_非法参数报错(self, mock_request):
        import board
        with pytest.raises(ValueError):
            board.cmd_register("", model="X", client="Y")
        assert not mock_request.calls  # 未发出任何请求


class TestWorkers:
    """workers：一行一 worker 列表（名字/模型/状态/最后活跃）。"""

    def test_workers_一行一worker(self, mock_request, capsys):
        import board
        reg2 = ('{"worker": "worker-2", "model": "豆包", "client": "Doubao", '
                '"registered_at": "2026-08-25T10:30", "last_seen": "2026-08-25T11:00", '
                '"status": "idle"}')
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card(REG_JSON_1), _card(reg2)], "total": 2}
        board.cmd_workers()
        out = capsys.readouterr().out
        assert "worker-1 GLM-5.3 idle 2026-08-25T10:00" in out
        assert "worker-2 豆包 idle 2026-08-25T11:00" in out

    def test_workers_空表提示(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        board.cmd_workers()
        assert "无已注册 worker" in capsys.readouterr().out

    def test_workers_非JSON记录跳过(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card("不是JSON"), _card(REG_JSON_1)], "total": 2}
        board.cmd_workers()
        out = capsys.readouterr().out
        assert "worker-1" in out
        assert "跳过" in out


class TestReport:
    """report：写交流窗记录（tag=comm:<channel>，source=report者，text≤300）。"""

    def test_report_写入带正确tag和source(self, mock_request):
        import board
        mock_request.responses["POST /memories"] = {"id": "c1"}
        board.cmd_report(channel="done", from_="worker-1",
                         text="TASK-0005 已回写")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        body = post[2]
        assert body["tags"] == ["comm:done"]
        assert body["source"] == "worker-1"
        assert body["content"] == "TASK-0005 已回写"

    def test_report_非法频道拒绝(self, mock_request):
        import board
        with pytest.raises(ValueError):
            board.cmd_report(channel="info", from_="worker-1", text="hi")
        assert not mock_request.calls  # 未发出任何请求

    def test_report_超长text拒绝(self, mock_request):
        import board
        with pytest.raises(ValueError) as ei:
            board.cmd_report(channel="done", from_="worker-1", text="x" * 301)
        assert "300" in str(ei.value)
        assert not mock_request.calls

    def test_report_空from拒绝(self, mock_request):
        import board
        with pytest.raises(ValueError):
            board.cmd_report(channel="done", from_="", text="hi")
        assert not mock_request.calls


class TestListComm:
    """list-comm：按频道列最新 N 条交流窗记录（updated_at 降序）。"""

    @staticmethod
    def _comm(cid, tag, source, text, updated_at):
        return {"id": cid, "content": text, "tags": [tag], "source": source,
                "updated_at": updated_at}

    def test_list_comm_按频道过滤(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=comm:done&limit=1000"] = {
            "items": [
                self._comm("c1", "comm:done", "worker-1", "A 完成",
                           "2026-08-25T10:00:00"),
                self._comm("c2", "comm:issue", "worker-2", "B 风险",
                           "2026-08-25T10:05:00"),
            ], "total": 2}
        board.cmd_list_comm(channel="done", limit=10)
        out = capsys.readouterr().out
        assert "comm:done" in out and "A 完成" in out
        assert "comm:issue" not in out

    def test_list_comm_缺省列全频道(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?limit=1000"] = {
            "items": [
                self._comm("c1", "comm:done", "worker-1", "A",
                           "2026-08-25T10:00:00"),
                self._comm("c2", "comm:issue", "worker-2", "B",
                           "2026-08-25T10:05:00"),
                self._comm("c3", "普通tag", "worker-1", "C",
                           "2026-08-25T10:10:00"),
            ], "total": 3}
        board.cmd_list_comm(channel=None, limit=10)
        out = capsys.readouterr().out
        assert "comm:done" in out and "comm:issue" in out
        assert "普通tag" not in out  # 非 comm:* 标签被过滤

    def test_list_comm_排序与limit(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?limit=1000"] = {
            "items": [
                self._comm("c1", "comm:done", "worker-1", "早",
                           "2026-08-25T10:00:00"),
                self._comm("c2", "comm:done", "worker-2", "中",
                           "2026-08-25T11:00:00"),
                self._comm("c3", "comm:done", "worker-1", "晚",
                           "2026-08-25T12:00:00"),
            ], "total": 3}
        board.cmd_list_comm(channel=None, limit=2)
        out = capsys.readouterr().out
        assert out.index("晚") < out.index("中")  # 降序：晚、中 在前
        assert "早" not in out                    # limit=2 截断

    def test_list_comm_无结果提示(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?limit=1000"] = {
            "items": [], "total": 0}
        board.cmd_list_comm(channel=None, limit=10)
        assert "无交流窗记录" in capsys.readouterr().out


def _comm_record(cid, tag, source, text, updated_at):
    """构造交流窗记录（tags 带 comm:* 前缀）。"""
    return {"id": cid, "content": text, "tags": [tag], "source": source,
            "updated_at": updated_at}


class TestWatch:
    """watch：终端看板（worker 行 + 卡行 + 可选交流窗），支持 --once 单轮。"""

    def test_watch_输出worker行与卡行(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card(REG_JSON_1)], "total": 1}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0001 pending worker-1 | 待办\n目标：x")],
            "total": 1}
        board.cmd_watch(interval=5, comm=False, once=True)
        out = capsys.readouterr().out
        assert "worker-1 GLM-5.3 idle 2026-08-25T10:00" in out  # worker 行
        assert "TASK-0001 pending worker-1 12:30 待办" in out    # 卡行

    def test_watch_空数据提示不崩溃(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        board.cmd_watch(interval=5, comm=False, once=True)
        out = capsys.readouterr().out
        assert "无已注册 worker" in out

    def test_watch_interval非法报错(self, mock_request):
        import board
        with pytest.raises(ValueError):
            board.cmd_watch(interval=0, comm=False, once=True)
        assert not mock_request.calls  # 未发出任何请求

    def test_watch_KeyboardInterrupt干净退出(self, mock_request, monkeypatch,
                                             capsys):
        import board
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card(REG_JSON_1)], "total": 1}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0001 pending worker-1 | 待办\n目标：x")],
            "total": 1}

        def raise_kbi(*a, **k):
            raise KeyboardInterrupt

        monkeypatch.setattr(board.time, "sleep", raise_kbi)
        board.cmd_watch(interval=5, comm=False, once=False)
        out = capsys.readouterr().out
        assert "watch 已退出" in out  # 干净退出，未向外抛异常

    def test_watch_comm附交流窗最近5条(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?limit=1000"] = {
            "items": [
                _comm_record("c1", "comm:done", "worker-1", "完成",
                             "2026-08-25T10:00:00"),
                _comm_record("c2", "comm:issue", "worker-2", "风险",
                             "2026-08-25T10:05:00"),
            ], "total": 2}
        board.cmd_watch(interval=5, comm=True, once=True)
        out = capsys.readouterr().out
        assert "交流窗" in out
        assert "comm:done" in out and "comm:issue" in out
