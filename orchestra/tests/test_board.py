"""board.py 单测：各子命令（cmd_*）行为；纯函数与 HTTP 客户端已拆至
test_cards.py / test_client.py（TASK-0028/0029 包化）；
registry/comm 已拆至 test_registry.py / test_comm.py（TASK-0030 包化）；
watch/worktree 已拆至 test_watch.py / test_worktree.py（TASK-0031 包化）。"""
import sys

import pytest


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
        import cards
        monkeypatch.setattr(sys, "argv", ["board.py", "status"])
        # 包化后 board 不再持有 _request；status 分发至 cards.cmd_status，
        # 其调用 cards._request——patch 它抛 BoardUnavailable（类源自 client）。
        monkeypatch.setattr(cards, "_request",
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


