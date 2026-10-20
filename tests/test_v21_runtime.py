"""v2.1 资源占用、服务发现与登录自启验收测试（spec §3.3/§4）。"""
import os
import threading
import time
from pathlib import Path

import pytest


def test_嵌入模型空闲卸载(env_isolated):
    from kb.embedder import Embedder
    e = Embedder("fake-model", idle_unload_seconds=10)
    e._model = object()
    e._state = "ok"
    e._last_used = time.monotonic() - 11
    assert e.status()["state"] == "ok"
    assert e.unload_if_idle() is True
    assert e.status()["state"] == "unloaded"
    assert e.unload_if_idle() is False  # 已卸载不重复处理


def test_嵌入模型空闲卸载可关闭(env_isolated):
    from kb.embedder import Embedder
    e = Embedder("fake-model", idle_unload_seconds=0)
    e._model = object()
    e._state = "ok"
    e._last_used = time.monotonic() - 999
    assert e.unload_if_idle() is False
    assert e.status()["state"] == "ok"


def test_空闲守护线程可启停(env_isolated):
    from kb.cli import start_idle_watchdog
    called = threading.Event()

    class FakeEmbedder:
        def unload_if_idle(self):
            called.set()
            return False

    stop = start_idle_watchdog(FakeEmbedder(), 60, check_seconds=0.02)
    try:
        assert called.wait(2.0), "守护线程应周期性调用 unload_if_idle"
    finally:
        stop.set()


def test_发现顺序_manifest优先(tmp_path, monkeypatch, env_isolated):
    from kb import discovery
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    fake_python = tmp_path / "venv" / "python"
    fake_python.parent.mkdir()
    fake_python.write_text("", encoding="utf-8")
    discovery.write_manifest(python=str(fake_python), version="9.9.9",
                             data_dir=str(tmp_path / "kbdata"),
                             extras="ingest", venv=str(tmp_path / "venv"))
    info = discovery.discover()
    assert info["source"] == "manifest"
    assert info["python"] == str(fake_python)
    assert info["version"] == "9.9.9"
    assert info["data_dir"] == str(tmp_path / "kbdata")
    assert info["command"][-1] == "--non-interactive"


def test_登录自启_写入与删除(tmp_path, monkeypatch, env_isolated):
    from kb import discovery
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    info = {
        "python": str(Path(env_isolated) / "python"),
        "data_dir": str(tmp_path / "data"),
    }
    result = discovery.autostart_enable(info, run=False)
    path = Path(result["file"])
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "--non-interactive" in text
    assert str(tmp_path / "data") in text
    assert discovery.autostart_status()["enabled"] is True
    removed = discovery.autostart_disable(run=False)
    assert removed["removed"] is True
    assert not path.exists()
    assert discovery.autostart_status()["enabled"] is False


def test_where命令输出(env_isolated):
    from typer.testing import CliRunner
    from kb.cli import app
    result = CliRunner().invoke(app, ["where"])
    assert result.exit_code == 0, result.output
    for key in ("解释器", "版本", "数据目录", "MCP 端点"):
        assert key in result.output


@pytest.mark.skipif(os.name == "nt", reason="Windows 启动项由 .cmd 实现")
def test_linux_systemd_unit内容(tmp_path, monkeypatch, env_isolated):
    from kb import discovery
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    result = discovery.autostart_enable(
        {"python": "/opt/kb/bin/python", "data_dir": "/srv/kbdata"}, run=False)
    text = Path(result["file"]).read_text(encoding="utf-8")
    assert "ExecStart=/opt/kb/bin/python -m kb serve --non-interactive" in text
    assert "Environment=KB_DATA_DIR=/srv/kbdata" in text
    assert "WantedBy=default.target" in text
