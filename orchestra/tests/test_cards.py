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
    """LIMITS 常量：字段上限与设计文档第 4 节一致。"""

    def test_字段上限完整(self):
        import cards
        assert cards.LIMITS == {"title": 30, "goal": 300, "input": 300,
                                 "constraints": 200, "acceptance": 200, "result": 1000}
