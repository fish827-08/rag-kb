"""coordinator_loop.py 单测（TASK-0061：open FBK 自动广播）。

覆盖：
- _list_open_fbks：解析 board feedback list 输出（仅 open，排除 closed 行）
- _broadcast_new_fbks：新 open FBK 广播一次 / 下轮不重复 / 无新不写 /
  快照随当前 open 集合更新（关闭的 FBK 移出快照，再 open 会重新广播）
"""
import json

FBK_LIST_OUT = (
    "FBK-0001 open TASK-0026 objection precheck 22:00 缺验收测试\n"
    "FBK-0002 accepted TASK-0027 risk milestone 22:01 OOM\n"
    "FBK-0003 open TASK-0028 clarify review 22:02 澄清本地\n"
)


class TestListOpenFbks:
    """_list_open_fbks：从 feedback list 输出解析 open 反馈卡。"""

    def test_解析open行排除closed行(self, monkeypatch):
        import coordinator_loop as cl
        monkeypatch.setattr(cl, "_run", lambda cmd, **kw: (0, FBK_LIST_OUT))
        fbks = cl._list_open_fbks()
        assert fbks == [("FBK-0001", "TASK-0026", "objection"),
                        ("FBK-0003", "TASK-0028", "clarify")]

    def test_无反馈卡输出返回空(self, monkeypatch):
        import coordinator_loop as cl
        monkeypatch.setattr(cl, "_run", lambda cmd, **kw: (0, "无反馈卡"))
        assert cl._list_open_fbks() == []

    def test_命令失败返回空不崩溃(self, monkeypatch):
        import coordinator_loop as cl
        monkeypatch.setattr(cl, "_run", lambda cmd, **kw: (1, "连接失败"))
        assert cl._list_open_fbks() == []


class TestBroadcastNewFbks:
    """_broadcast_new_fbks：对比快照只广播新出现的 open FBK。"""

    def test_新open反馈广播一次并落快照(self, monkeypatch, tmp_path):
        import coordinator_loop as cl
        calls = []
        monkeypatch.setattr(cl, "_list_open_fbks", lambda: [
            ("FBK-0001", "TASK-0026", "objection"),
            ("FBK-0003", "TASK-0028", "clarify"),
        ])
        monkeypatch.setattr(cl, "_comm_system",
                            lambda text: calls.append(text))
        snap = tmp_path / "snap.json"
        new = cl._broadcast_new_fbks(snapshot_path=snap)
        assert len(new) == 2                      # 两个均视为新（无快照）
        assert len(calls) == 2                    # 每卡广播一次
        assert any("FBK-0001" in c and "TASK-0026" in c
                   and "objection" in c for c in calls)
        assert json.load(open(snap, encoding="utf-8")) == \
            ["FBK-0001", "FBK-0003"]             # 快照落盘

    def test_下轮不重复广播(self, monkeypatch, tmp_path):
        import coordinator_loop as cl
        calls = []
        monkeypatch.setattr(cl, "_list_open_fbks", lambda: [
            ("FBK-0001", "TASK-0026", "objection"),
            ("FBK-0003", "TASK-0028", "clarify"),
        ])
        monkeypatch.setattr(cl, "_comm_system",
                            lambda text: calls.append(text))
        snap = tmp_path / "snap.json"
        cl._broadcast_new_fbks(snapshot_path=snap)   # 第一轮：广播 2 条
        first = len(calls)
        calls.clear()
        new = cl._broadcast_new_fbks(snapshot_path=snap)  # 第二轮：同一批
        assert new == []
        assert calls == []                        # 不重复广播
        assert first == 2

    def test_无新FBK不写交流窗(self, monkeypatch, tmp_path):
        import coordinator_loop as cl
        calls = []
        monkeypatch.setattr(cl, "_list_open_fbks", lambda: [])
        monkeypatch.setattr(cl, "_comm_system",
                            lambda text: calls.append(text))
        snap = tmp_path / "snap.json"
        snap.write_text('["FBK-0009"]', encoding="utf-8")
        new = cl._broadcast_new_fbks(snapshot_path=snap)
        assert new == []
        assert calls == []                        # 无新 FBK 不写
        assert json.load(open(snap, encoding="utf-8")) == []  # 快照同步收缩

    def test_部分新增只广播新增(self, monkeypatch, tmp_path):
        import coordinator_loop as cl
        calls = []
        monkeypatch.setattr(cl, "_list_open_fbks", lambda: [
            ("FBK-0001", "TASK-0026", "objection"),
            ("FBK-0007", "TASK-0030", "risk"),
        ])
        monkeypatch.setattr(cl, "_comm_system",
                            lambda text: calls.append(text))
        snap = tmp_path / "snap.json"
        snap.write_text('["FBK-0001"]', encoding="utf-8")
        new = cl._broadcast_new_fbks(snapshot_path=snap)
        assert [n[0] for n in new] == ["FBK-0007"]
        assert len(calls) == 1 and "FBK-0007" in calls[0]

    def test_关闭后再open会重新广播(self, monkeypatch, tmp_path):
        import coordinator_loop as cl
        calls = []
        monkeypatch.setattr(cl, "_list_open_fbks", lambda: [
            ("FBK-0001", "TASK-0026", "objection"),
        ])
        monkeypatch.setattr(cl, "_comm_system",
                            lambda text: calls.append(text))
        snap = tmp_path / "snap.json"
        cl._broadcast_new_fbks(snapshot_path=snap)   # 第一轮：广播
        monkeypatch.setattr(cl, "_list_open_fbks", lambda: [])  # 关闭
        cl._broadcast_new_fbks(snapshot_path=snap)   # 快照收缩为空
        monkeypatch.setattr(cl, "_list_open_fbks", lambda: [
            ("FBK-0001", "TASK-0026", "objection"),
        ])
        calls.clear()
        new = cl._broadcast_new_fbks(snapshot_path=snap)   # 重新 open
        assert [n[0] for n in new] == ["FBK-0001"]
        assert len(calls) == 1


class TestCheckMountStale:
    """_check_mount_stale：失联 agent 写 comm:dispatch 告警（B5-4）。"""

    MOUNT_OUT = (
        "worker-1 worker 心跳2026-08-28T20:00:00 失联360s\n"
        "designer-1 designer 心跳2026-08-28T20:01:00 失联300s\n"
    )

    def test_失联agent逐条广播dispatch(self, monkeypatch):
        import coordinator_loop as cl
        calls = []
        monkeypatch.setattr(cl, "_run", lambda cmd, **kw: (0, self.MOUNT_OUT))
        monkeypatch.setattr(cl, "_comm_dispatch",
                            lambda text: calls.append(text))
        alerts = cl._check_mount_stale()
        assert len(alerts) == 2
        assert len(calls) == 2
        assert "worker-1" in calls[0] and "360s" in calls[0]
        assert "designer-1" in calls[1] and "300s" in calls[1]

    def test_无失联不广播(self, monkeypatch):
        import coordinator_loop as cl
        calls = []
        monkeypatch.setattr(cl, "_run", lambda cmd, **kw: (0, "无失联 agent"))
        monkeypatch.setattr(cl, "_comm_dispatch",
                            lambda text: calls.append(text))
        assert cl._check_mount_stale() == []
        assert calls == []

    def test_命令失败返回空不崩溃(self, monkeypatch):
        import coordinator_loop as cl
        monkeypatch.setattr(cl, "_run", lambda cmd, **kw: (1, "连接失败"))
        assert cl._check_mount_stale() == []
