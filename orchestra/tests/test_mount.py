"""mount.py 单测：挂载生命周期六命令（B5-1）。"""
import json

import pytest


def _card(content):
    return {"id": "mid", "content": content, "tags": ["mount_state"],
            "updated_at": "2026-08-28T20:00:00"}


def _mounted_json(name="worker-1", role="worker", ttl=900,
                  status="mounted", chain=None, streak=0, idle="2026-08-28T20:00:00"):
    return json.dumps({
        "agent": name, "role": role, "session_id": "M-20260828T200000",
        "mount_status": status, "mounted_at": "2026-08-28T20:00:00",
        "last_heartbeat": "2026-08-28T20:00:00", "idle_since": idle,
        "ttl": ttl, "topic_chain": chain or [], "topic_streak": streak,
        "reset_reason": "",
    }, ensure_ascii=False)


class TestMount:
    """mount：开始/刷新挂载会话。"""

    def test_mount_新挂载写入mount_state(self, mock_request, monkeypatch, capsys):
        import mount
        monkeypatch.setattr(mount, "_now_iso", lambda: "2026-08-28T20:00:00")
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "new-id"}
        mount.cmd_mount("worker-1", role="worker", ttl=900)
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        body = post[2]
        assert body["tags"] == ["mount_state"]
        data = json.loads(body["content"])
        assert data["agent"] == "worker-1"
        assert data["role"] == "worker"
        assert data["mount_status"] == "mounted"
        assert data["ttl"] == 900
        assert data["topic_streak"] == 0
        assert data["topic_chain"] == []
        assert "已挂载 worker-1" in capsys.readouterr().out

    def test_mount_重挂载PATCH不重复建且链清零(self, mock_request, monkeypatch):
        import mount
        monkeypatch.setattr(mount, "_now_iso", lambda: "2026-08-28T20:30:00")
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json(chain=["kb-A3"] * 3, streak=3))],
            "total": 1}
        mock_request.responses["PATCH /memories/mid"] = {}
        mount.cmd_mount("worker-1", role="worker", ttl=900)
        assert not any(c[0] == "POST" for c in mock_request.calls)
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        data = json.loads(patch[2]["content"])
        assert data["topic_chain"] == []           # 重挂载链清零
        assert data["topic_streak"] == 0
        assert data["mount_status"] == "mounted"

    def test_mount_非法role报错(self, mock_request):
        import mount
        with pytest.raises(ValueError):
            mount.cmd_mount("worker-1", role="boss", ttl=900)
        assert not mock_request.calls

    def test_mount_非法ttl报错(self, mock_request):
        import mount
        with pytest.raises(ValueError):
            mount.cmd_mount("worker-1", role="worker", ttl=-1)
        assert not mock_request.calls

    def test_mount_空name报错(self, mock_request):
        import mount
        with pytest.raises(ValueError):
            mount.cmd_mount("", role="worker", ttl=900)
        assert not mock_request.calls


class TestHeartbeat:
    """heartbeat：刷新心跳。"""

    def test_heartbeat刷新last_heartbeat(self, mock_request, monkeypatch):
        import mount
        monkeypatch.setattr(mount, "_now_iso", lambda: "2026-08-28T20:05:00")
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json())], "total": 1}
        mock_request.responses["PATCH /memories/mid"] = {}
        mount.cmd_heartbeat("worker-1")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        data = json.loads(patch[2]["content"])
        assert data["last_heartbeat"] == "2026-08-28T20:05:00"

    def test_heartbeat未挂载报错(self, mock_request):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(ValueError):
            mount.cmd_heartbeat("worker-1")

    def test_heartbeat已退出报错(self, mock_request):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json(status="exited"))], "total": 1}
        with pytest.raises(ValueError):
            mount.cmd_heartbeat("worker-1")


class TestUnmount:
    """unmount：退出挂载。"""

    def test_unmount置exited带原因(self, mock_request, capsys):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json())], "total": 1}
        mock_request.responses["PATCH /memories/mid"] = {}
        mount.cmd_unmount("worker-1", reason="空闲超时")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        data = json.loads(patch[2]["content"])
        assert data["mount_status"] == "exited"
        assert data["reset_reason"] == "空闲超时"
        assert "已退出挂载（空闲超时）" in capsys.readouterr().out

    def test_unmount未挂载报错(self, mock_request):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(ValueError):
            mount.cmd_unmount("worker-1")


class TestMountStatus:
    """mount-status：挂载看板。"""

    def test_mount_status一行一agent(self, mock_request, capsys):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json()), _card(_mounted_json("designer-1", "designer"))],
            "total": 2}
        mount.cmd_mount_status()
        out = capsys.readouterr().out
        assert "worker-1 worker mounted" in out
        assert "designer-1 designer mounted" in out

    def test_mount_status空表提示(self, mock_request, capsys):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [], "total": 0}
        mount.cmd_mount_status()
        assert "无挂载中的 agent" in capsys.readouterr().out

    def test_mount_status按role过滤(self, mock_request, capsys):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json()), _card(_mounted_json("designer-1", "designer"))],
            "total": 2}
        mount.cmd_mount_status(role="designer")
        out = capsys.readouterr().out
        assert "designer-1" in out
        assert "worker-1" not in out

    def test_mount_status非JSON记录跳过(self, mock_request, capsys):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card("不是JSON"), _card(_mounted_json())], "total": 2}
        mount.cmd_mount_status()
        out = capsys.readouterr().out
        assert "worker-1" in out
        assert "跳过" in out


class TestMountClaim:
    """mount-claim：领卡登记主题，连续相关链/streak 更新。"""

    def test_claim首主题streak1(self, mock_request):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json())], "total": 1}
        mock_request.responses["PATCH /memories/mid"] = {}
        mount.cmd_mount_claim("worker-1", topic="kb-A3")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        data = json.loads(patch[2]["content"])
        assert data["topic_chain"] == ["kb-A3"]
        assert data["topic_streak"] == 1
        assert data["mount_status"] == "working"
        assert data["idle_since"] == ""

    def test_claim同主题连续递增(self, mock_request):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json(chain=["kb-A3", "kb-A3"], streak=2))],
            "total": 1}
        mock_request.responses["PATCH /memories/mid"] = {}
        mount.cmd_mount_claim("worker-1", topic="kb-A3")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        data = json.loads(patch[2]["content"])
        assert data["topic_streak"] == 3
        assert data["topic_chain"] == ["kb-A3", "kb-A3", "kb-A3"]

    def test_claim不同主题重置为1(self, mock_request):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json(chain=["kb-A3", "kb-A3"], streak=2))],
            "total": 1}
        mock_request.responses["PATCH /memories/mid"] = {}
        mount.cmd_mount_claim("worker-1", topic="kb-A4")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        data = json.loads(patch[2]["content"])
        assert data["topic_streak"] == 1
        assert data["topic_chain"] == ["kb-A4"]

    def test_claim达上限打印重置标记(self, mock_request, capsys):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json(chain=["kb-A3"] * 4, streak=4))],
            "total": 1}
        mock_request.responses["PATCH /memories/mid"] = {}
        mount.cmd_mount_claim("worker-1", topic="kb-A3")
        out = capsys.readouterr().out
        assert "连续相关=5" in out
        assert "上下文重置" in out

    def test_claim空topic报错(self, mock_request):
        import mount
        with pytest.raises(ValueError):
            mount.cmd_mount_claim("worker-1", topic="")
        assert not mock_request.calls

    def test_claim未挂载报错(self, mock_request):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(ValueError):
            mount.cmd_mount_claim("worker-1", topic="kb-A3")


class TestMountIdle:
    """mount-idle：完成任务转回空闲监听。"""

    def test_idle转mounted刷新idle_since(self, mock_request, monkeypatch):
        import mount
        monkeypatch.setattr(mount, "_now_iso", lambda: "2026-08-28T20:10:00")
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json(status="working", idle=""))],
            "total": 1}
        mock_request.responses["PATCH /memories/mid"] = {}
        mount.cmd_mount_idle("worker-1")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        data = json.loads(patch[2]["content"])
        assert data["mount_status"] == "mounted"
        assert data["idle_since"] == "2026-08-28T20:10:00"

    def test_idle未挂载报错(self, mock_request):
        import mount
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(ValueError):
            mount.cmd_mount_idle("worker-1")


class TestStaleAgents:
    """stale_agents：失联判定纯函数（B5-4）。"""

    NOW = "2026-08-28T20:06:00"

    def _rec(self, agent="worker-1", role="worker", status="mounted",
             hb="2026-08-28T20:00:00"):
        return _card(json.dumps({
            "agent": agent, "role": role, "mount_status": status,
            "last_heartbeat": hb, "idle_since": "", "ttl": 900,
            "topic_chain": [], "topic_streak": 0, "reset_reason": "",
        }, ensure_ascii=False))

    def test_心跳超阈值判定失联(self):
        import mount
        stale = mount.stale_agents([self._rec()], self.NOW, 300)
        assert len(stale) == 1
        assert stale[0]["agent"] == "worker-1"
        assert stale[0]["idle_seconds"] == 360

    def test_心跳在阈值内不失联(self):
        import mount
        stale = mount.stale_agents(
            [self._rec(hb="2026-08-28T20:05:30")], self.NOW, 300)
        assert stale == []

    def test_exited排除(self):
        import mount
        stale = mount.stale_agents(
            [self._rec(status="exited")], self.NOW, 300)
        assert stale == []

    def test_心跳解析失败视为失联(self):
        import mount
        stale = mount.stale_agents(
            [self._rec(hb="坏时间")], self.NOW, 300)
        assert len(stale) == 1
        assert stale[0]["idle_seconds"] is None

    def test_非JSON记录跳过(self):
        import mount
        stale = mount.stale_agents([_card("不是JSON")], self.NOW, 300)
        assert stale == []


class TestMountCheck:
    """mount-check：列出失联 agent（B5-4）。"""

    def test_mount_check列出失联(self, mock_request, monkeypatch, capsys):
        import mount
        monkeypatch.setattr(mount, "_now_iso", lambda: "2026-08-28T20:06:00")
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json())], "total": 1}
        mount.cmd_mount_check(threshold=300)
        out = capsys.readouterr().out
        assert "worker-1 worker" in out
        assert "失联360s" in out

    def test_mount_check无失联提示(self, mock_request, monkeypatch, capsys):
        import mount
        monkeypatch.setattr(mount, "_now_iso", lambda: "2026-08-28T20:00:30")
        mock_request.responses["GET /memories?tag=mount_state&limit=1000"] = {
            "items": [_card(_mounted_json())], "total": 1}
        mount.cmd_mount_check(threshold=300)
        assert "无失联 agent" in capsys.readouterr().out

    def test_mount_check非法threshold报错(self, mock_request):
        import mount
        with pytest.raises(ValueError):
            mount.cmd_mount_check(threshold=0)
        assert not mock_request.calls
