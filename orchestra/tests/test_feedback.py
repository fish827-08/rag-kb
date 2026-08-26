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

    # ---- TASK-0040：--task 目标卡过滤 ----
    def test_list_task过滤_只返回该卡反馈(self, mock_request, capsys):
        import feedback
        a = feedback.render_fbk("FBK-0001", proposer="designer-1",
                                task_id="TASK-0001", fb_type="objection",
                                stage="precheck", summary="A 反馈",
                                alt="补用例")
        b = feedback.render_fbk("FBK-0002", proposer="worker-1",
                                task_id="TASK-0002", fb_type="risk",
                                stage="milestone", summary="B 反馈",
                                impact="阻塞")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk(a), _fbk(b)], "total": 2}
        feedback.cmd_fbk_list(task_id="TASK-0001")
        out = capsys.readouterr().out
        assert "FBK-0001 open TASK-0001" in out
        assert "FBK-0002" not in out  # 目标卡 TASK-0002 的反馈被过滤

    def test_list_task过滤_不传返回全部(self, mock_request, capsys):
        import feedback
        a = feedback.render_fbk("FBK-0001", proposer="designer-1",
                                task_id="TASK-0001", fb_type="objection",
                                stage="precheck", summary="A 反馈",
                                alt="补用例")
        b = feedback.render_fbk("FBK-0002", proposer="worker-1",
                                task_id="TASK-0002", fb_type="clarify",
                                stage="review", summary="B 反馈",
                                question="q")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk(a), _fbk(b)], "total": 2}
        feedback.cmd_fbk_list()  # 缺省不传 → 列全部（现有行为不变）
        out = capsys.readouterr().out
        assert "FBK-0001" in out and "FBK-0002" in out


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


# ---- TASK-0033：配额门禁 + decide ----
def _make_fbk(fbk_id, task_id, fb_type, stage, result="open"):
    """构造一条反馈卡记录（content 含完整字段）。"""
    import feedback
    content = feedback.render_fbk(
        fbk_id, proposer="designer-1", task_id=task_id,
        fb_type=fb_type, stage=stage, summary="s",
        alt="a" if fb_type == "objection" else "",
        impact="i" if fb_type == "risk" else "",
        question="q" if fb_type == "clarify" else "",
        result=result)
    return {"id": fbk_id.lower(), "content": content, "tags": ["feedback"]}


class TestQuota:
    """分节点配额硬门禁（B2 §3：precheck 2 / milestone 2 / 总 5 / review 不计）。"""

    def test_count_按节点统计(self):
        import feedback
        cards = [
            _make_fbk("FBK-0001", "TASK-0026", "objection", "precheck"),
            _make_fbk("FBK-0002", "TASK-0026", "risk", "precheck"),
            _make_fbk("FBK-0003", "TASK-0026", "clarify", "milestone"),
            _make_fbk("FBK-0004", "TASK-0026", "objection", "review"),
            _make_fbk("FBK-0005", "TASK-0099", "objection", "precheck"),  # 其他任务不计
        ]
        c = feedback.count_task_feedback("TASK-0026", cards)
        assert c["precheck"] == 2
        assert c["milestone"] == 1
        assert c["review"] == 1
        assert c["total"] == 4

    def test_check_quota_precheck第3轮拒绝提示仲裁(self):
        import feedback
        cards = [_make_fbk("FBK-0001", "TASK-0026", "objection", "precheck"),
                 _make_fbk("FBK-0002", "TASK-0026", "risk", "precheck")]
        with pytest.raises(ValueError, match="仲裁"):
            feedback.check_quota("TASK-0026", "precheck", cards)

    def test_check_quota_milestone第3轮拒绝(self):
        import feedback
        cards = [_make_fbk("FBK-0001", "TASK-0026", "clarify", "milestone"),
                 _make_fbk("FBK-0002", "TASK-0026", "risk", "milestone")]
        with pytest.raises(ValueError, match="仲裁"):
            feedback.check_quota("TASK-0026", "milestone", cards)

    def test_check_quota总第6轮拒绝(self):
        import feedback
        # precheck 2 + milestone 2 + review 1 = 5，再加任意节点触发总上限
        cards = [
            _make_fbk("FBK-0001", "TASK-0026", "objection", "precheck"),
            _make_fbk("FBK-0002", "TASK-0026", "risk", "precheck"),
            _make_fbk("FBK-0003", "TASK-0026", "clarify", "milestone"),
            _make_fbk("FBK-0004", "TASK-0026", "objection", "milestone"),
            _make_fbk("FBK-0005", "TASK-0026", "clarify", "review"),
        ]
        with pytest.raises(ValueError, match="仲裁"):
            feedback.check_quota("TASK-0026", "precheck", cards)

    def test_check_quota_review不计配额(self):
        import feedback
        # review 节点即使已有多条也不拒绝（沉淀性）
        cards = [_make_fbk(f"FBK-000{i}", "TASK-0026", "clarify", "review") for i in range(1, 6)]
        feedback.check_quota("TASK-0026", "review", cards)  # 不抛异常

    def test_check_quota未超限通过(self):
        import feedback
        cards = [_make_fbk("FBK-0001", "TASK-0026", "objection", "precheck")]
        feedback.check_quota("TASK-0026", "precheck", cards)  # 不抛异常


class TestAddQuota:
    """cmd_fbk_add 集成配额门禁：超限拒绝新卡且不发请求。"""

    def test_add_precheck超限拒绝不发请求(self, mock_request):
        import feedback
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_make_fbk("FBK-0001", "TASK-0026", "objection", "precheck"),
                      _make_fbk("FBK-0002", "TASK-0026", "risk", "precheck")]}
        with pytest.raises(ValueError, match="仲裁"):
            feedback.cmd_fbk_add(proposer="designer-1", task_id="TASK-0026",
                                 fb_type="clarify", stage="precheck",
                                 summary="s", question="q")
        # 仅发了 GET 统计请求，未发 POST 创建
        methods = [c[0] for c in mock_request.calls]
        assert "POST" not in methods


class TestDecide:
    """cmd_fbk_decide：open→accepted/rejected + comm:feedback 归档。"""

    def test_decide_open到accepted(self, mock_request, capsys):
        import feedback
        fbk = _make_fbk("FBK-0001", "TASK-0026", "objection", "precheck", result="open")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {"items": [fbk]}
        mock_request.responses["PATCH /memories/fbk-0001"] = fbk
        mock_request.responses["POST /memories"] = {"id": "comm1"}
        feedback.cmd_fbk_decide("FBK-0001", "accepted", note="方案合理", decider="coordinator")
        # PATCH 内容结果字段已改 accepted
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        assert "结果：accepted" in patch[2]["content"]
        # comm:feedback 已写
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        assert post[2]["tags"] == ["comm:feedback"]
        assert "FBK-0001" in post[2]["content"]
        assert "accepted" in post[2]["content"]

    def test_decide_open到rejected(self, mock_request):
        import feedback
        fbk = _make_fbk("FBK-0001", "TASK-0026", "objection", "precheck", result="open")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {"items": [fbk]}
        mock_request.responses["PATCH /memories/fbk-0001"] = fbk
        mock_request.responses["POST /memories"] = {"id": "comm1"}
        feedback.cmd_fbk_decide("FBK-0001", "rejected", note="无替代方案")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        assert "结果：rejected" in patch[2]["content"]

    def test_decide非open拒绝不改卡(self, mock_request):
        import feedback
        fbk = _make_fbk("FBK-0001", "TASK-0026", "objection", "precheck", result="accepted")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {"items": [fbk]}
        with pytest.raises(ValueError):
            feedback.cmd_fbk_decide("FBK-0001", "rejected")
        # 未发 PATCH / POST
        methods = [c[0] for c in mock_request.calls]
        assert "PATCH" not in methods
        assert "POST" not in methods

    def test_decide_comm_feedback不超300字符(self, mock_request):
        import feedback
        fbk = _make_fbk("FBK-0001", "TASK-0026", "objection", "precheck", result="open")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {"items": [fbk]}
        mock_request.responses["PATCH /memories/fbk-0001"] = fbk
        mock_request.responses["POST /memories"] = {"id": "comm1"}
        feedback.cmd_fbk_decide("FBK-0001", "accepted",
                                 note="x" * 500, decider="coordinator")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        assert len(post[2]["content"]) <= 300

    def test_main_feedback_decide分发(self, mock_request, monkeypatch, capsys):
        import sys
        import board
        fbk = _make_fbk("FBK-0001", "TASK-0026", "objection", "precheck", result="open")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {"items": [fbk]}
        mock_request.responses["PATCH /memories/fbk-0001"] = fbk
        mock_request.responses["POST /memories"] = {"id": "comm1"}
        monkeypatch.setattr(
            sys, "argv",
            ["board.py", "feedback", "decide", "FBK-0001", "--accepted", "--note", "ok"])
        board.main()
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        assert "结果：accepted" in patch[2]["content"]
