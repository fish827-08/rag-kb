"""comm.py 单测：report/list-comm 子命令（TASK-0030 包化④，
自 test_board.py 迁移；import 改新模块）。"""
import pytest

from comm import COMM_CHANNELS


def test_COMM_CHANNELS含dispatch():
    """TASK-0057：频道枚举含 dispatch（comm:dispatch 异常调度播报）。"""
    assert "dispatch" in COMM_CHANNELS


class TestReport:
    """report：写交流窗记录（tag=comm:<channel>，source=report者，text≤300）。"""

    def test_report_写入带正确tag和source(self, mock_request):
        import comm
        mock_request.responses["POST /memories"] = {"id": "c1"}
        comm.cmd_report(channel="done", from_="worker-1",
                        text="TASK-0005 已回写")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        body = post[2]
        assert body["tags"] == ["comm:done"]
        assert body["source"] == "worker-1"
        assert body["content"] == "TASK-0005 已回写"

    def test_report_非法频道拒绝(self, mock_request):
        import comm
        with pytest.raises(ValueError):
            comm.cmd_report(channel="info", from_="worker-1", text="hi")
        assert not mock_request.calls  # 未发出任何请求

    def test_report_超长text拒绝(self, mock_request):
        import comm
        with pytest.raises(ValueError) as ei:
            comm.cmd_report(channel="done", from_="worker-1", text="x" * 301)
        assert "300" in str(ei.value)
        assert not mock_request.calls

    def test_report_空from拒绝(self, mock_request):
        import comm
        with pytest.raises(ValueError):
            comm.cmd_report(channel="done", from_="", text="hi")
        assert not mock_request.calls

    def test_report_dispatch频道写入(self, mock_request):
        """TASK-0057：dispatch 频道可写入，tag=comm:dispatch。"""
        import comm
        mock_request.responses["POST /memories"] = {"id": "c1"}
        comm.cmd_report(channel="dispatch", from_="kb-dispatch",
                        text="待办告急：卡池 pending 数为 0")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        body = post[2]
        assert body["tags"] == ["comm:dispatch"]
        assert body["source"] == "kb-dispatch"
        assert "待办告急" in body["content"]


class TestListComm:
    """list-comm：按频道列最新 N 条交流窗记录（updated_at 降序）。"""

    @staticmethod
    def _comm(cid, tag, source, text, updated_at):
        return {"id": cid, "content": text, "tags": [tag], "source": source,
                "updated_at": updated_at}

    def test_list_comm_按频道过滤(self, mock_request, capsys):
        import comm
        mock_request.responses["GET /memories?tag=comm:done&limit=1000"] = {
            "items": [
                self._comm("c1", "comm:done", "worker-1", "A 完成",
                           "2026-08-25T10:00:00"),
                self._comm("c2", "comm:issue", "worker-2", "B 风险",
                           "2026-08-25T10:05:00"),
            ], "total": 2}
        comm.cmd_list_comm(channel="done", limit=10)
        out = capsys.readouterr().out
        assert "comm:done" in out and "A 完成" in out
        assert "comm:issue" not in out

    def test_list_comm_缺省列全频道(self, mock_request, capsys):
        import comm
        mock_request.responses["GET /memories?limit=1000"] = {
            "items": [
                self._comm("c1", "comm:done", "worker-1", "A",
                           "2026-08-25T10:00:00"),
                self._comm("c2", "comm:issue", "worker-2", "B",
                           "2026-08-25T10:05:00"),
                self._comm("c3", "普通tag", "worker-1", "C",
                           "2026-08-25T10:10:00"),
            ], "total": 3}
        comm.cmd_list_comm(channel=None, limit=10)
        out = capsys.readouterr().out
        assert "comm:done" in out and "comm:issue" in out
        assert "普通tag" not in out  # 非 comm:* 标签被过滤

    def test_list_comm_排序与limit(self, mock_request, capsys):
        import comm
        mock_request.responses["GET /memories?limit=1000"] = {
            "items": [
                self._comm("c1", "comm:done", "worker-1", "早",
                           "2026-08-25T10:00:00"),
                self._comm("c2", "comm:done", "worker-2", "中",
                           "2026-08-25T11:00:00"),
                self._comm("c3", "comm:done", "worker-1", "晚",
                           "2026-08-25T12:00:00"),
            ], "total": 3}
        comm.cmd_list_comm(channel=None, limit=2)
        out = capsys.readouterr().out
        assert out.index("晚") < out.index("中")  # 降序：晚、中 在前
        assert "早" not in out                    # limit=2 截断

    def test_list_comm_无结果提示(self, mock_request, capsys):
        import comm
        mock_request.responses["GET /memories?limit=1000"] = {
            "items": [], "total": 0}
        comm.cmd_list_comm(channel=None, limit=10)
        assert "无交流窗记录" in capsys.readouterr().out

    def test_list_comm_dispatch频道过滤(self, mock_request, capsys):
        """TASK-0057：--channel dispatch 按 comm:dispatch 过滤，其他频道不混入。"""
        import comm
        mock_request.responses["GET /memories?tag=comm:dispatch&limit=1000"] = {
            "items": [
                self._comm("c1", "comm:dispatch", "kb-dispatch", "待办告急",
                           "2026-08-25T10:00:00"),
                self._comm("c2", "comm:done", "worker-1", "A 完成",
                           "2026-08-25T10:05:00"),
            ], "total": 2}
        comm.cmd_list_comm(channel="dispatch", limit=10)
        out = capsys.readouterr().out
        assert "comm:dispatch" in out and "待办告急" in out
        assert "comm:done" not in out  # 其他频道被过滤

    def test_list_comm_dispatch空频道正常返回(self, mock_request, capsys):
        """TASK-0057：--channel dispatch 空频道正常返回'无交流窗记录'，不报错。"""
        import comm
        mock_request.responses["GET /memories?tag=comm:dispatch&limit=1000"] = {
            "items": [], "total": 0}
        comm.cmd_list_comm(channel="dispatch", limit=10)
        assert "无交流窗记录" in capsys.readouterr().out
