"""v2.1 skill 引导安装验收测试（spec §3.2）：只出计划、确认后安装、发现与启动。"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BOOTSTRAP = (Path(__file__).resolve().parent.parent / "skills" / "kb-memory"
             / "bootstrap.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("kb_bootstrap", BOOTSTRAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def boot(tmp_path, monkeypatch):
    module = _load_module()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return module


class FakeRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd, check=False, capture_output=False, text=False):
        self.calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="2.1.0\n")


def test_默认只展示计划(boot, tmp_path, capsys):
    runner = FakeRunner()
    rc = boot.main(["--dir", str(tmp_path / "inst"), "--port", "1"],
                   runner=runner)
    assert rc == 0
    out = capsys.readouterr().out
    assert "安装计划" in out
    assert "未执行安装" in out
    assert "kb-memory[ingest]" in out
    assert runner.calls == []  # 未执行任何命令
    assert not boot.manifest_path().exists()


def test_确认后安装并写清单(boot, tmp_path, capsys):
    inst = tmp_path / "inst"
    venv_python = boot.venv_python(inst / "venv")
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    runner = FakeRunner()
    rc = boot.main(["--yes", "--no-start", "--dir", str(inst),
                    "--port", "1"], runner=runner)
    assert rc == 0
    manifest = boot.read_manifest()
    assert manifest is not None
    assert manifest["python"] == str(venv_python)
    assert manifest["version"] == "2.1.0"
    assert manifest["data_dir"] == str(inst / "data")
    # venv 已存在 → 跳过创建；pip 安装仍执行
    assert all("venv" not in cmd for cmd in runner.calls)
    joined = [" ".join(c) for c in runner.calls]
    assert any("pip install kb-memory[ingest]" in j for j in joined)
    if sys.platform.startswith("linux"):
        assert any("download.pytorch.org/whl/cpu" in j for j in joined)


def test_清单发现与仅启动(boot, tmp_path, capsys):
    inst = tmp_path / "inst"
    venv_python = boot.venv_python(inst / "venv")
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    boot.main(["--yes", "--no-start", "--dir", str(inst), "--port", "1"],
              runner=FakeRunner())
    rc = boot.main(["--status", "--port", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(venv_python) in out and "2.1.0" in out
    launched: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        launched.append(list(cmd))
        return object()

    rc = boot.main(["--start-only", "--port", "1"], popen=fake_popen)
    assert rc == 0
    assert launched == [[str(venv_python), "-m", "kb", "serve",
                         "--non-interactive"]]


def test_无清单时状态提示(boot, capsys):
    assert boot.main(["--status", "--port", "1"]) == 1
    assert "未找到安装清单" in capsys.readouterr().out
