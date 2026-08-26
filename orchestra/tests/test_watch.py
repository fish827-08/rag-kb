"""watch.py 终端看板单测（TASK-0031 包化⑥：自 test_board.py::TestWatch 迁移；
TASK-0035 增 open 反馈卡段用例）。

覆盖：_watch_frame 渲染（worker 行/卡行/open 反馈卡段/交流窗段）与
cmd_watch 行为（--once 单轮、interval 校验、Ctrl+C 干净退出）。
"""
import pytest

REG_JSON_1 = ('{"worker": "worker-1", "model": "GLM-5.3", "client": "TraeWork", '
              '"registered_at": "2026-08-24T23:00", "last_seen": "2026-08-25T10:00", '
              '"status": "idle"}')

_FBK_EMPTY = {"items": [], "total": 0}


def _card(content, updated_at="2026-08-24T12:30:00"):
    """构造 kb list 返回的单条记录。"""
    return {"id": "abc123", "content": content, "tags": ["taskboard"],
            "updated_at": updated_at}


def _fbk(content, updated_at="2026-08-26T20:00:00"):
    """构造反馈卡记录（tag=feedback）。"""
    return {"id": "f1", "content": content, "tags": ["feedback"],
            "updated_at": updated_at}


def _comm_record(cid, tag, source, text, updated_at):
    """构造交流窗记录（tags 带 comm:* 前缀）。"""
    return {"id": cid, "content": text, "tags": [tag], "source": source,
            "updated_at": updated_at}


class TestWatch:
    """watch：终端看板（worker 行 + 卡行 + 可选交流窗），支持 --once 单轮。"""

    def test_watch_输出worker行与卡行(self, mock_request, capsys):
        import watch
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card(REG_JSON_1)], "total": 1}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0001 pending worker-1 | 待办\n目标：x")],
            "total": 1}
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = _FBK_EMPTY
        watch.cmd_watch(interval=5, comm=False, once=True)
        out = capsys.readouterr().out
        assert "worker-1 GLM-5.3 idle 2026-08-25T10:00" in out  # worker 行
        assert "TASK-0001 pending worker-1 12:30 待办" in out    # 卡行

    def test_watch_空数据提示不崩溃(self, mock_request, capsys):
        import watch
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = _FBK_EMPTY
        watch.cmd_watch(interval=5, comm=False, once=True)
        out = capsys.readouterr().out
        assert "无已注册 worker" in out

    def test_watch_interval非法报错(self, mock_request):
        import watch
        with pytest.raises(ValueError):
            watch.cmd_watch(interval=0, comm=False, once=True)
        assert not mock_request.calls  # 未发出任何请求

    def test_watch_KeyboardInterrupt干净退出(self, mock_request, monkeypatch,
                                             capsys):
        import watch
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card(REG_JSON_1)], "total": 1}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0001 pending worker-1 | 待办\n目标：x")],
            "total": 1}

        def raise_kbi(*a, **k):
            raise KeyboardInterrupt

        monkeypatch.setattr(watch.time, "sleep", raise_kbi)
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = _FBK_EMPTY
        watch.cmd_watch(interval=5, comm=False, once=False)
        out = capsys.readouterr().out
        assert "watch 已退出" in out  # 干净退出，未向外抛异常

    def test_watch_comm附交流窗最近5条(self, mock_request, capsys):
        import watch
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = _FBK_EMPTY
        mock_request.responses["GET /memories?limit=1000"] = {
            "items": [
                _comm_record("c1", "comm:done", "worker-1", "完成",
                             "2026-08-25T10:00:00"),
                _comm_record("c2", "comm:issue", "worker-2", "风险",
                             "2026-08-25T10:05:00"),
            ], "total": 2}
        watch.cmd_watch(interval=5, comm=True, once=True)
        out = capsys.readouterr().out
        assert "交流窗" in out
        assert "comm:done" in out and "comm:issue" in out

    # ---- TASK-0035：open 反馈卡段 ----
    def test_watch_反馈段_有openFBK时渲染该段(self, mock_request, capsys):
        """有 open FBK 时渲染反馈段；accepted/rejected 不显示。"""
        import watch
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        fbk_open = ("FBK-0001 feedback TASK-0026 | objection precheck\n"
                    "提出者：designer-1\n目标卡：TASK-0026\n"
                    "类型：objection\n节点：precheck\n摘要：缺验收测试\n"
                    "替代方案：补黑盒用例\n结果：open")
        fbk_closed = ("FBK-0002 feedback TASK-0027 | clarify review\n"
                      "提出者：worker-1\n目标卡：TASK-0027\n"
                      "类型：clarify\n节点：review\n摘要：已答复\n"
                      "澄清问题：是否含本地？\n结果：accepted")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk(fbk_open), _fbk(fbk_closed)], "total": 2}
        watch.cmd_watch(interval=5, comm=False, once=True)
        out = capsys.readouterr().out
        assert "-- 反馈卡(open) --" in out
        assert "FBK-0001 open TASK-0026 objection precheck" in out  # 含 FBK-ID 与目标卡
        assert "缺验收测试" in out
        assert "FBK-0002" not in out  # accepted 不显示

    def test_watch_反馈段_无open时不显示该段(self, mock_request, capsys):
        """无 open 反馈卡时整个反馈段不渲染。"""
        import watch
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        fbk_closed = ("FBK-0001 feedback TASK-0026 | risk milestone\n"
                      "提出者：worker-1\n目标卡：TASK-0026\n"
                      "类型：risk\n节点：milestone\n摘要：OOM\n"
                      "阻塞点/影响面：阻塞合入\n结果：rejected")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk(fbk_closed)], "total": 1}
        watch.cmd_watch(interval=5, comm=False, once=True)
        out = capsys.readouterr().out
        assert "反馈卡" not in out

    # ---- TASK-0039：任务卡行附 open FBK 数 ----
    def test_watch_卡行附open反馈数(self, mock_request, capsys):
        """有 open FBK 的任务卡行末尾显示 [FBK:N]（按目标卡聚合）。"""
        import watch
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0026 pending worker-2 | 有反馈的卡\n目标：x"),
                _card("TASK-0027 pending worker-3 | 无反馈的卡\n目标：y"),
            ], "total": 2}
        fbk1 = ("FBK-0001 feedback TASK-0026 | objection precheck\n"
                "提出者：designer-1\n目标卡：TASK-0026\n"
                "类型：objection\n节点：precheck\n摘要：缺验收测试\n"
                "替代方案：补黑盒用例\n结果：open")
        fbk2 = ("FBK-0002 feedback TASK-0026 | risk milestone\n"
                "提出者：worker-1\n目标卡：TASK-0026\n"
                "类型：risk\n节点：milestone\n摘要：OOM\n"
                "阻塞点/影响面：阻塞合入\n结果：open")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk(fbk1), _fbk(fbk2)], "total": 2}
        watch.cmd_watch(interval=5, comm=False, once=True)
        out = capsys.readouterr().out
        assert "TASK-0026 pending worker-2 12:30 有反馈的卡 [FBK:2]" in out

    def test_watch_卡行无open反馈时不显示标注(self, mock_request, capsys):
        """无 open FBK（或 FBK 均已 closed）的任务卡行不带 [FBK: 标注。"""
        import watch
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0027 pending worker-3 | 全closed的卡\n目标：y"),
                _card("TASK-0028 pending worker-4 | 零反馈的卡\n目标：z"),
            ], "total": 2}
        fbk_closed = ("FBK-0003 feedback TASK-0027 | risk milestone\n"
                      "提出者：worker-1\n目标卡：TASK-0027\n"
                      "类型：risk\n节点：milestone\n摘要：OOM\n"
                      "阻塞点/影响面：阻塞合入\n结果：accepted")
        mock_request.responses["GET /memories?tag=feedback&limit=1000"] = {
            "items": [_fbk(fbk_closed)], "total": 1}
        watch.cmd_watch(interval=5, comm=False, once=True)
        out = capsys.readouterr().out
        assert "TASK-0027 pending worker-3 12:30 全closed的卡" in out
        assert "TASK-0028 pending worker-4 12:30 零反馈的卡" in out
        assert "[FBK:" not in out  # 两行均不应出现标注
