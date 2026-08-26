"""watch.py 终端看板单测（TASK-0031 包化⑥：自 test_board.py::TestWatch 迁移）。

覆盖：_watch_frame 渲染（worker 行/卡行/交流窗段）与 cmd_watch 行为
（--once 单轮、interval 校验、Ctrl+C 干净退出）。
"""
import pytest

REG_JSON_1 = ('{"worker": "worker-1", "model": "GLM-5.3", "client": "TraeWork", '
              '"registered_at": "2026-08-24T23:00", "last_seen": "2026-08-25T10:00", '
              '"status": "idle"}')


def _card(content, updated_at="2026-08-24T12:30:00"):
    """构造 kb list 返回的单条记录。"""
    return {"id": "abc123", "content": content, "tags": ["taskboard"],
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
        watch.cmd_watch(interval=5, comm=False, once=False)
        out = capsys.readouterr().out
        assert "watch 已退出" in out  # 干净退出，未向外抛异常

    def test_watch_comm附交流窗最近5条(self, mock_request, capsys):
        import watch
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
        watch.cmd_watch(interval=5, comm=True, once=True)
        out = capsys.readouterr().out
        assert "交流窗" in out
        assert "comm:done" in out and "comm:issue" in out
