"""feedback.py 单测（TASK-0032：B2 反馈卡）。

覆盖：字段校验（三类型必附字段/长度/枚举）、状态机（open→accepted/rejected）、
编号递增、add/list/show 命令与 CLI 分发。
"""
import json

import pytest


def _fbk(content, updated_at="2026-08-26T20:00:00"):
    """构造 kb list 返回的单条反馈卡记录。"""
    return {"id": "f1", "content": content, "tags": ["feedback"],
            "updated_at": updated_at}


# ---- 纯函数：渲染/解析/校验 ----
def test_render_标准反馈卡():
    import feedback
    content = feedback.render_fbk(
        "FBK-0001", proposer="designer-1", task_id="TASK-0026",
        fb_type="objection", stage="precheck", summary="缺验收测试",
        alt="补充黑盒用例")
    lines = content.split("\n")
    assert lines[0] == "FBK-0001 feedback TASK-0026 | objection precheck"
    assert lines[1] == "提出者：designer-1"
    assert lines[2] == "目标卡：TASK-0026"
    assert lines[3] == "类型：objection"
    assert lines[4] == "节点：precheck"
    assert lines[5] == "摘要：缺验收测试"
    assert lines[6] == "替代方案：补充黑盒用例"
    assert lines[-1] == "结果：open"  # 默认 open


def test_parse_header_往返与非法():
    import feedback
    h = feedback.parse_fbk_header(
        "FBK-0003 feedback TASK-0026 | risk milestone")
    assert h == {"fbk_id": "FBK-0003", "task_id": "TASK-0026",
                 "fb_type": "risk", "stage": "milestone"}
    with pytest.raises(ValueError):
        feedback.parse_fbk_header("这不是反馈卡")


def test_必附字段_三类型各校验():
    """B2 §4：objection 必附替代方案 / risk 必附阻塞点影响面 / clarify 必附澄清问题。"""
    import feedback
    # objection 缺替代方案 → 拒绝（无替代方案的异议直接驳回）
    with pytest.raises(ValueError, match="替代方案"):
        feedback.check_fbk_required("objection", alt="")
    feedback.check_fbk_required("objection", alt="改用方案B")
    # risk 缺阻塞点/影响面 → 拒绝
    with pytest.raises(ValueError, match="阻塞点/影响面"):
        feedback.check_fbk_required("risk", impact="")
    feedback.check_fbk_required("risk", impact="阻塞鉴权，影响合入")
    # clarify 缺澄清问题 → 拒绝
    with pytest.raises(ValueError, match="澄清问题"):
        feedback.check_fbk_required("clarify", question="")
    feedback.check_fbk_required("clarify", question="是否含本地模式？")
    # 非法类型
    with pytest.raises(ValueError, match="类型非法"):
        feedback.check_fbk_required("nonsense")


def test_状态机_open到accepted_rejected():
    """B2 §1：结果 open → accepted / rejected；非法转移/目标拒绝。"""
    import feedback
    feedback.check_result_transition("open", "accepted")
    feedback.check_result_transition("open", "rejected")
    with pytest.raises(ValueError, match="仅 open"):
        feedback.check_result_transition("accepted", "rejected")
    with pytest.raises(ValueError, match="结果非法"):
        feedback.check_result_transition("open", "pending")


def test_长度校验():
    import feedback
    with pytest.raises(ValueError, match="summary"):
        feedback.check_fbk_limits(summary="x" * 101)
    feedback.check_fbk_limits(summary="x" * 100)


def test_编号递增():
    import feedback
    cards = [_fbk("FBK-0007 feedback TASK-0001 | objection precheck"),
             _fbk("FBK-0003 feedback TASK-0002 | clarify review")]
    assert feedback._next_fbk_id(cards) == "FBK-0008"


# ---- add/list/show 命令 ----
class TestAdd:
    def test_add_创建open反馈卡(self, mock_request, capsys):
        import feedback
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "n1"}
        feedback.cmd_fbk_add(proposer="designer-1", task_id="TASK-0026",
                             fb_type="objection", stage="precheck",
                             summary="缺验收", alt="补用例")
        out = capsys.readouterr().out
        assert "FBK-0001" in out
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        assert post[2]["tags"] == ["feedback"]
        assert post[2]["content"].startswith(
            "FBK-0001 feedback TASK-0026 | objection precheck")

    def test_add_必附字段缺失拒绝不发请求(self, mock_request):
        import feedback
        with pytest.raises(ValueError):
            feedback.cmd_fbk_add(proposer="designer-1", task_id="TASK-0026",
                                 fb_type="objection", stage="precheck",
                                 summary="缺验收")  # 无 alt
        assert not mock_request.calls  # 未发出任何请求

    def test_add_非法节点拒绝(self, mock_request):
        import feedback
        with pytest.raises(ValueError):
            feedback.cmd_fbk_add(proposer="w", task_id="TASK-0026",
                                 fb_type="clarify", stage="xx",
                                 summary="s", question="q")
        assert not mock_request.calls


class TestList:
    def test_list_一行一反馈卡(self, mock_request, capsys):
        import feedback
        content = feedback.render_fbk(
            "FBK-0001", proposer="designer-1", task_id="TASK-0026",
            fb_type="objection", stage="precheck", summary="缺验收",
            alt="补用例")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk(content)], "total": 1}
        feedback.cmd_fbk_list()
        out = capsys.readouterr().out
        assert "FBK-0001 open TASK-0026 objection precheck" in out
        assert "缺验收" in out

    def test_list_空板提示(self, mock_request, capsys):
        import feedback
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [], "total": 0}
        feedback.cmd_fbk_list()
        assert "无反馈卡" in capsys.readouterr().out

    def test_list_非法首行跳过(self, mock_request, capsys):
        import feedback
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk("坏内容"), _fbk("FBK-0002 feedback TASK-0001 | risk review")],
            "total": 2}
        feedback.cmd_fbk_list()
        out = capsys.readouterr().out
        assert "FBK-0002" in out and "非法" in out


class TestShow:
    def test_show_打印整卡(self, mock_request, capsys):
        import feedback
        content = feedback.render_fbk(
            "FBK-0001", proposer="designer-1", task_id="TASK-0026",
            fb_type="risk", stage="milestone", summary="OOM",
            impact="阻塞合入", result="accepted")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk(content)], "total": 1}
        feedback.cmd_fbk_show("FBK-0001")
        out = capsys.readouterr().out
        assert "风险" not in out and "OOM" in out
        assert "结果：accepted" in out

    def test_show_不存在报错(self, mock_request):
        import feedback
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(SystemExit):
            feedback.cmd_fbk_show("FBK-0099")


# ---- CLI 分发（board.py 接线） ----
class TestBoardDispatch:
    def test_main_feedback_add分发(self, mock_request, monkeypatch, capsys):
        import sys
        import board
        monkeypatch.setattr(
            sys, "argv",
            ["board.py", "feedback", "add", "--proposer", "designer-1",
             "--task", "TASK-0026", "--type", "objection",
             "--stage", "precheck", "--summary", "缺验收", "--alt", "补用例"])
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "n1"}
        board.main()
        assert "FBK-0001" in capsys.readouterr().out

    def test_main_feedback_list分发(self, mock_request, monkeypatch, capsys):
        import sys
        import board
        monkeypatch.setattr(sys, "argv", ["board.py", "feedback", "list"])
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [], "total": 0}
        board.main()
        assert "无反馈卡" in capsys.readouterr().out

    def test_main_feedback_show分发(self, mock_request, monkeypatch, capsys):
        import sys
        import board
        monkeypatch.setattr(sys, "argv",
                            ["board.py", "feedback", "show", "FBK-0001"])
        content = ("FBK-0001 feedback TASK-0026 | clarify precheck\n"
                   "提出者：designer-1\n目标卡：TASK-0026\n"
                   "类型：clarify\n节点：precheck\n摘要：问\n"
                   "澄清问题：是否含本地？\n结果：open")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk(content)], "total": 1}
        board.main()
        out = capsys.readouterr().out
        assert "FBK-0001" in out and "是否含本地？" in out

    def test_main_feedback_必附字段缺失退出码1(self, mock_request, monkeypatch):
        import sys
        import board
        monkeypatch.setattr(
            sys, "argv",
            ["board.py", "feedback", "add", "--proposer", "designer-1",
             "--task", "TASK-0026", "--type", "objection",
             "--stage", "precheck", "--summary", "缺验收"])
        # 不预置 responses：若发出请求会 AssertionError；应在校验阶段退出
        with pytest.raises(SystemExit) as ei:
            board.main()
        assert ei.value.code == 1
