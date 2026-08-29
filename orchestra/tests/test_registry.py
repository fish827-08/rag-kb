"""registry.py 单测：register/workers 子命令（TASK-0030 包化③，
自 test_board.py 迁移；import 改新模块）。"""
import json

import pytest


def _card(content, updated_at="2026-08-24T12:30:00"):
    """构造 kb list 返回的单条记录。"""
    return {"id": "abc123", "content": content, "tags": ["taskboard"],
            "updated_at": updated_at}


REG_JSON_1 = ('{"worker": "worker-1", "model": "GLM-5.3", "client": "TraeWork", '
              '"registered_at": "2026-08-24T23:00", "last_seen": "2026-08-25T10:00", '
              '"status": "idle"}')


class TestRegister:
    """register：注册/刷新 worker 身份（tag=registry）。"""

    def test_register_新worker写入registry(self, mock_request, monkeypatch):
        import registry
        monkeypatch.setattr(registry, "_now_iso", lambda: "2026-08-25T10:30")
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "new-id"}
        registry.cmd_register("worker-2", model="豆包", client="Doubao")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        body = post[2]
        assert body["tags"] == ["registry"]
        data = json.loads(body["content"])
        assert data["worker"] == "worker-2"
        assert data["model"] == "豆包"
        assert data["client"] == "Doubao"
        assert data["status"] == "idle"
        assert data["registered_at"] == "2026-08-25T10:30"
        assert data["last_seen"] == "2026-08-25T10:30"

    def test_register_重复注册刷新last_seen不重复建卡(self, mock_request, monkeypatch):
        import registry
        monkeypatch.setattr(registry, "_now_iso", lambda: "2026-08-25T11:00")
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card(REG_JSON_1)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        registry.cmd_register("worker-1", model="GLM-6.0", client="TraeWork")
        assert not any(c[0] == "POST" for c in mock_request.calls)  # 不重复建卡
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        data = json.loads(patch[2]["content"])
        assert data["worker"] == "worker-1"
        assert data["model"] == "GLM-6.0"                      # 身份刷新
        assert data["registered_at"] == "2026-08-24T23:00"     # 保留首登时间
        assert data["last_seen"] == "2026-08-25T11:00"         # last_seen 刷新

    def test_register_非法参数报错(self, mock_request):
        import registry
        with pytest.raises(ValueError):
            registry.cmd_register("", model="X", client="Y")
        assert not mock_request.calls  # 未发出任何请求


class TestWorkers:
    """workers：一行一 worker 列表（名字/模型/状态/最后活跃）。"""

    def test_workers_一行一worker(self, mock_request, capsys):
        import registry
        reg2 = ('{"worker": "worker-2", "model": "豆包", "client": "Doubao", '
                '"registered_at": "2026-08-25T10:30", "last_seen": "2026-08-25T11:00", '
                '"status": "idle"}')
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card(REG_JSON_1), _card(reg2)], "total": 2}
        registry.cmd_workers()
        out = capsys.readouterr().out
        assert "worker-1 GLM-5.3 idle 2026-08-25T10:00" in out
        assert "worker-2 豆包 idle 2026-08-25T11:00" in out

    def test_workers_空表提示(self, mock_request, capsys):
        import registry
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [], "total": 0}
        registry.cmd_workers()
        assert "无已注册 worker" in capsys.readouterr().out

    def test_workers_非JSON记录跳过(self, mock_request, capsys):
        import registry
        mock_request.responses["GET /memories?tag=registry&limit=1000"] = {
            "items": [_card("不是JSON"), _card(REG_JSON_1)], "total": 2}
        registry.cmd_workers()
        out = capsys.readouterr().out
        assert "worker-1" in out
        assert "跳过" in out
