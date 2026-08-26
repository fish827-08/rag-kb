"""cards.py 卡片纯函数单测（TASK-0029 包化②：从 board.py 拆出）。

覆盖：render_card / parse_header / check_limits / _next_task_id / _fmt_time / LIMITS 常量。
原 test_board.py::TestCardFunctions 的 4 个用例机械搬移至此，import 由 board 改为 cards。
"""
import pytest


class TestCardFunctions:
    """卡片渲染与解析纯函数。"""

    def test_render_标准卡片(self):
        import cards
        content = cards.render_card(
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
        import cards
        header = cards.parse_header("TASK-0003 claimed worker-1 | 修复空指针")
        assert header == {"task_id": "TASK-0003", "status": "claimed",
                          "assignee": "worker-1", "title": "修复空指针"}

    def test_parse_header_非法格式报错(self):
        import cards
        with pytest.raises(ValueError):
            cards.parse_header("这不是一张任务卡")

    def test_check_limits_超限报错(self):
        import cards
        with pytest.raises(ValueError) as ei:
            cards.check_limits(title="x" * 31)
        assert "title" in str(ei.value)
        # 恰好 30 字符不报错
        cards.check_limits(title="x" * 30)


class TestNextTaskId:
    """_next_task_id：现有卡最大编号 +1，四位数零填充。"""

    def test_空列表返回0001(self):
        import cards
        assert cards._next_task_id([]) == "TASK-0001"

    def test_取最大值加一(self):
        import cards
        cards_list = [
            {"content": "TASK-0003 pending w1 | a"},
            {"content": "TASK-0007 done w2 | b"},
            {"content": "TASK-0001 claimed w3 | c"},
        ]
        assert cards._next_task_id(cards_list) == "TASK-0008"

    def test_非法卡跳过不参与编号(self):
        import cards
        cards_list = [
            {"content": "TASK-0005 done w1 | a"},
            {"content": "这不是卡"},
        ]
        assert cards._next_task_id(cards_list) == "TASK-0006"


class TestFmtTime:
    """_fmt_time：ISO 时间 → HH:MM；解析失败返回 ???。"""

    def test_正常ISO返回时分(self):
        import cards
        assert cards._fmt_time("2026-08-24T12:30:00") == "12:30"

    def test_非法字符串返回问号(self):
        import cards
        assert cards._fmt_time("not-a-time") == "???"

    def test_None返回问号(self):
        import cards
        assert cards._fmt_time(None) == "???"


class TestLimits:
    """LIMITS 常量：字段上限与设计文档第 4 节一致；docs 为包化设计 §4 增补。"""

    def test_字段上限完整(self):
        import cards
        assert cards.LIMITS == {"title": 30, "goal": 300, "input": 300,
                                 "constraints": 200, "acceptance": 200,
                                 "result": 1000, "docs": 300}


def _card(content, updated_at="2026-08-24T12:30:00"):
    """构造 kb list 返回的单条记录。"""
    return {"id": "abc123", "content": content, "tags": ["taskboard"],
            "updated_at": updated_at}


CARD_FULL = ("TASK-0005 done worker-1 | 修复空指针\n"
             "目标：修复检索空指针\n输入：kb/retriever.py\n"
             "约束：不改接口\n验收：测试全绿\n"
             "结果：已修复第 42 行，测试通过")

CARD_WITH_DOCS = ("TASK-0007 done worker-2 | 端点文档化\n"
                  "目标：补文档\n输入：api.py\n"
                  "约束：零代码改动\n验收：文档已更新\n"
                  "结果：已完成\n"
                  "文档同步：USER_GUIDE.md(端点速查节)")


class TestStatus:
    """status：列出全部任务卡，一行一卡。"""

    def test_status_一行一卡含时间与标题(self, mock_request, capsys):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0001 pending worker-1 | 重构异常\n目标：x"),
                _card("TASK-0002 done worker-2 | 修复空指针\n目标：y",
                      updated_at="2026-08-24T09:15:00"),
            ], "total": 2}
        cards.cmd_status()
        out = capsys.readouterr().out
        assert "TASK-0001 pending worker-1 12:30 重构异常" in out
        assert "TASK-0002 done worker-2 09:15 修复空指针" in out

    def test_status_空板提示(self, mock_request, capsys):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        cards.cmd_status()
        assert "无任务卡" in capsys.readouterr().out

    def test_status_非法卡片跳过并警告(self, mock_request, capsys):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("坏卡片内容"), _card("TASK-0009 pending w1 | 正常|卡")],
            "total": 2}
        cards.cmd_status()
        out = capsys.readouterr().out
        assert "TASK-0009" in out
        assert "非法" in out


class TestListPending:
    """list-pending：只显示 pending 状态的任务卡。"""

    def test_list_pending_只列pending过滤其他状态(self, mock_request, capsys):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0001 pending worker-1 | 待办甲\n目标：x"),
                _card("TASK-0002 claimed worker-2 | 进行中\n目标：y",
                      updated_at="2026-08-24T09:15:00"),
                _card("TASK-0003 done worker-1 | 已完成\n目标：z"),
                _card("TASK-0004 verified worker-2 | 已核验\n目标：w"),
                _card("TASK-0005 failed worker-1 | 已失败\n目标：v"),
            ], "total": 5}
        cards.cmd_list_pending()
        out = capsys.readouterr().out
        assert "TASK-0001 pending worker-1 12:30 待办甲" in out
        assert "TASK-0002" not in out
        assert "TASK-0003" not in out
        assert "TASK-0004" not in out
        assert "TASK-0005" not in out

    def test_list_pending_空板提示(self, mock_request, capsys):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        cards.cmd_list_pending()
        assert "无待办任务卡" in capsys.readouterr().out


class TestAdd:
    """add：字段校验、编号递增、创建调用。"""

    def test_add_创建首张卡编号0001(self, mock_request, capsys):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "new-id"}
        cards.cmd_add(assignee="worker-1", title="重构异常", goal="统一异常",
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
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0007 done worker-1 | 旧卡"),
                _card("TASK-0003 pending worker-2 | 旧卡"),
            ], "total": 2}
        mock_request.responses["POST /memories"] = {"id": "x"}
        cards.cmd_add(assignee="worker-1", title="t", goal="g", input_="i",
                      constraints="c", acceptance="a")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        assert post[2]["content"].startswith("TASK-0008 pending worker-1 | t")

    def test_add_字段超长拒绝(self, mock_request):
        import cards
        with pytest.raises(ValueError):
            cards.cmd_add(assignee="w1", title="x" * 31, goal="g",
                          input_="i", constraints="c", acceptance="a")
        assert not any(c[0] == "POST" for c in mock_request.calls)


class TestShow:
    def test_show_打印整卡(self, mock_request, capsys):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_FULL)], "total": 1}
        cards.cmd_show("TASK-0005")
        out = capsys.readouterr().out
        assert "修复空指针" in out and "结果：已修复" in out

    def test_show_卡不存在报错(self, mock_request, capsys):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(SystemExit):
            cards.cmd_show("TASK-0099")
        # readouterr() 只能读一次：统一读取后拼接 err+out 再断言
        captured = capsys.readouterr()
        assert "不存在" in captured.err + captured.out


class TestVerify:
    def test_verify_pass_done转verified(self, mock_request):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_FULL)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        cards.cmd_verify("TASK-0005", action="pass", note="")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        assert patch[2]["content"].startswith("TASK-0005 verified worker-1")

    def test_verify_reject_回pending带备注(self, mock_request):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_FULL)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        cards.cmd_verify("TASK-0005", action="reject", note="结果超长")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        content = patch[2]["content"]
        assert content.startswith("TASK-0005 pending worker-1")
        assert "备注：结果超长" in content

    def test_verify_仅done_failed可流转(self, mock_request):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0006 pending worker-1 | 未完成\n目标：x")],
            "total": 1}
        with pytest.raises(SystemExit):
            cards.cmd_verify("TASK-0006", action="pass", note="")


class TestClaim:
    """claim：pending→claimed 并更新 assignee；非 pending 拒绝。"""

    def test_claim_pending卡成功转claimed且更新assignee(self, mock_request):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0010 pending worker-1 | 待认领\n目标：x")],
            "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        cards.cmd_claim("TASK-0010", assignee="worker-2")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        content = patch[2]["content"]
        assert content.startswith("TASK-0010 claimed worker-2 | 待认领")
        # 其余字段原样保留
        assert "目标：x" in content

    def test_claim_非pending卡报错不误改(self, mock_request):
        import cards
        for status in ("claimed", "done", "failed", "verified"):
            mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
                "items": [_card(f"TASK-0010 {status} worker-1 | 进行中\n目标：x")],
                "total": 1}
            with pytest.raises(SystemExit):
                cards.cmd_claim("TASK-0010", assignee="worker-2")
            # 没有发出 PATCH
            assert not any(c[0] == "PATCH" for c in mock_request.calls)

    def test_claim_卡不存在报错(self, mock_request):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(SystemExit):
            cards.cmd_claim("TASK-0099", assignee="worker-2")


class TestNewWorker:
    def test_new_worker_输出引导语含名字与skill指令(self, capsys):
        import cards
        cards.cmd_new_worker("worker-1")
        out = capsys.readouterr().out
        assert "worker-1" in out
        assert "orchestra-worker" in out


class TestDocs:
    """--docs 文档同步清单（包化设计 §4）：add 渲染进卡 + verify 硬门禁。"""

    def test_add_docs清单渲染进卡片(self, mock_request):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "new-id"}
        cards.cmd_add(assignee="worker-1", title="端点文档化", goal="g",
                      input_="i", constraints="c", acceptance="a",
                      docs="USER_GUIDE.md(端点速查节)")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        assert "文档同步：USER_GUIDE.md(端点速查节)" in post[2]["content"]

    def test_add_docs超长拒绝(self, mock_request):
        import cards
        with pytest.raises(ValueError) as ei:
            cards.cmd_add(assignee="w1", title="t", goal="g", input_="i",
                          constraints="c", acceptance="a", docs="x" * 301)
        assert "docs" in str(ei.value)
        assert not any(c[0] == "POST" for c in mock_request.calls)

    def test_verify_pass_有docs清单未确认拒绝(self, mock_request):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_WITH_DOCS)], "total": 1}
        with pytest.raises(SystemExit):
            cards.cmd_verify("TASK-0007", action="pass", note="")
        # 未发出 PATCH（状态未误改）
        assert not any(c[0] == "PATCH" for c in mock_request.calls)

    def test_verify_pass_docs_done放行(self, mock_request):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_WITH_DOCS)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        cards.cmd_verify("TASK-0007", action="pass", note="",
                         docs_done=True)
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        assert patch[2]["content"].startswith("TASK-0007 verified worker-2")
        # docs 清单行原样保留（核验后仍可追溯）
        assert "文档同步：USER_GUIDE.md(端点速查节)" in patch[2]["content"]

    def test_verify_reject_不受docs门禁影响(self, mock_request):
        import cards
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_WITH_DOCS)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        cards.cmd_verify("TASK-0007", action="reject", note="文档未同步")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        assert patch[2]["content"].startswith("TASK-0007 pending worker-2")
