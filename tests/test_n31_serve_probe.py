"""serve 启动健壮性修复验收测试（任务 #2：服务实际已启动但 agent 报告未启动）。

覆盖：
1. /health 别名路由与 /api/v1/healthz 返回相同内容（LLM agent 习惯猜 /health）；
2. 鉴权开启时 /health 与 /api/v1/healthz 同为白名单豁免；
3. cli serve 端口被占时非零退出并给出中文提示（socket 占住端口模拟）；
4. 端口空闲时正常启动路径由服务自身写入/清理工作区根 kb.pid（纯函数可测部分）。
不启动真实服务进程；uvicorn/create_app/设备检测全链路打桩（对齐 test_n30 模式）。
"""
import os
import socket

import pytest


def _free_port() -> int:
    """向系统申请一个空闲端口（绑 0 端口后立刻释放）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- 1. /health 别名 ----------

def test_health别名_与healthz内容一致(env_isolated):
    """GET /health 与 GET /api/v1/healthz 返回完全相同的 JSON。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r_health = c.get("/health")
        r_healthz = c.get("/api/v1/healthz")
    assert r_health.status_code == 200
    assert r_health.json() == r_healthz.json()
    assert r_health.json()["status"] == "ok"


def test_health别名_鉴权白名单豁免(env_isolated):
    """启用 API Key 鉴权时，/health 与 /api/v1/healthz 同享白名单豁免（无凭证 200）。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.config import Settings
    with TestClient(create_app(settings=Settings(api_key="secret123"))) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/api/v1/healthz").status_code == 200


# ---------- 2. 端口占用预检 ----------

class TestServe端口预检:
    """serve 启动前端口预检：被占用 → 中文报错 + 非零退出，不落空壳进程。"""

    def _patch_heavy_deps(self, monkeypatch, tmp_path):
        """打桩设备检测/应用构建/uvicorn；chdir 到临时目录隔离 kb.pid。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("kb.cli.resolve_device",
                            lambda s, interactive=True: "cpu")
        monkeypatch.setattr("kb.api.create_app", lambda *a, **kw: object())
        return monkeypatch

    def test_端口被占_非零退出并中文提示(self, env_isolated, monkeypatch, tmp_path):
        port = _free_port()
        monkeypatch.setenv("KB_API_PORT", str(port))
        from kb import config
        config.get_settings.cache_clear()

        self._patch_heavy_deps(monkeypatch, tmp_path)
        uvicorn_calls = []
        monkeypatch.setattr("uvicorn.run", lambda *a, **kw: uvicorn_calls.append(1))

        # socket 占住端口，模拟"已有实例在监听"
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", port))
        blocker.listen(1)
        try:
            from typer.testing import CliRunner
            from kb.cli import app
            r = CliRunner().invoke(app, ["serve"])
        finally:
            blocker.close()

        assert r.exit_code != 0, "端口被占必须非零退出"
        # rich 控制台可能软换行，断言前先去除空白还原完整文案（任务 #5：平台中性文案）
        out = "".join(r.output.split())
        assert "已被占用" in out
        assert "stop_kb.bat" in out          # Windows 指引仍在
        assert "systemctl" in out or "kill" in out  # Linux 指引不缺席
        assert uvicorn_calls == [], "端口被占时不得进入 uvicorn.run"

    def test_端口空闲_写入并清理pid文件(self, env_isolated, monkeypatch, tmp_path):
        port = _free_port()
        monkeypatch.setenv("KB_API_PORT", str(port))
        from kb import config
        config.get_settings.cache_clear()

        self._patch_heavy_deps(monkeypatch, tmp_path)
        pid_file = tmp_path / "kb.pid"
        pid_during_run = {}

        def fake_run(*args, **kwargs):
            # 模拟 uvicorn 运行期间：kb.pid 已写入且内容为当前进程 PID
            assert pid_file.exists(), "uvicorn 运行前应先写入 kb.pid"
            pid_during_run["pid"] = pid_file.read_text(encoding="utf-8").strip()

        monkeypatch.setattr("uvicorn.run", fake_run)

        from typer.testing import CliRunner
        from kb.cli import app
        r = CliRunner().invoke(app, ["serve"])

        assert r.exit_code == 0, f"正常启动路径应退出码 0，输出：{r.output}"
        assert pid_during_run["pid"] == str(os.getpid())
        assert not pid_file.exists(), "uvicorn 退出后应清理 kb.pid"


# ---------- 3. pid 纯函数 ----------

class TestPid纯函数:
    """write_pid_file / remove_pid_file / is_port_in_use 纯函数。"""

    def test_写入与清理_自己的pid(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from kb.cli import remove_pid_file, write_pid_file
        write_pid_file()
        pid_file = tmp_path / "kb.pid"
        assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())
        remove_pid_file()
        assert not pid_file.exists()

    def test_清理时不误删他人pid文件(self, tmp_path, monkeypatch):
        """kb.pid 内容非本进程 PID（另一实例所写）时不得删除。"""
        monkeypatch.chdir(tmp_path)
        from kb.cli import remove_pid_file
        pid_file = tmp_path / "kb.pid"
        other_pid = str(os.getpid() + 12345)
        pid_file.write_text(other_pid + "\n", encoding="utf-8")
        remove_pid_file()
        assert pid_file.exists() and pid_file.read_text().strip() == other_pid

    def test_is_port_in_use(self):
        from kb.cli import is_port_in_use
        port = _free_port()
        assert is_port_in_use("127.0.0.1", port) is False
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", port))
        blocker.listen(1)
        try:
            assert is_port_in_use("127.0.0.1", port) is True
        finally:
            blocker.close()

    def test_is_port_in_use_ipv6(self):
        """IPv6 host（::1）不得被 AF_INET 硬编码误判为占用（任务 #5）。
        本机无 IPv6 支持时跳过。"""
        from kb.cli import is_port_in_use
        try:
            probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        except OSError:
            pytest.skip("本机无 IPv6 支持")
        with probe:
            try:
                probe.bind(("::1", 0))
            except OSError:
                pytest.skip("::1 不可用")
            port = probe.getsockname()[1]
        # 释放后端口空闲：不得误判为占用而拒绝启动
        assert is_port_in_use("::1", port) is False
        blocker = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        blocker.bind(("::1", port))
        blocker.listen(1)
        try:
            assert is_port_in_use("::1", port) is True
        finally:
            blocker.close()
