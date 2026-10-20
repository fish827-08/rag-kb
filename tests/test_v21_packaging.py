"""v2.1 打包验收：版本一致、核心/可选依赖拆分、缺依赖提示、Linux CPU 轮子。"""
import importlib
import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import pytest

import kb

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_版本号三处一致():
    """pyproject / kb.__version__ / server.json 不允许漂移。"""
    data = _pyproject()
    manifest = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    assert data["project"]["name"] == "kb-memory"
    assert data["project"]["version"] == kb.__version__
    assert manifest["version"] == kb.__version__
    assert data["project"]["scripts"]["kb"] == "kb.cli:app"


def test_核心依赖不含可选重依赖():
    """默认安装只保留运行/检索必需项；摄取与 LLM 走 extras。"""
    project = _pyproject()["project"]
    core = " ".join(project["dependencies"]).lower()
    for name in ("pypdf", "python-docx", "markitdown", "trafilatura",
                 "lxml_html_clean", "watchdog", "openai"):
        assert name not in core, f"可选依赖 {name} 不应出现在核心依赖中"

    extras = project["optional-dependencies"]
    assert {"ingest", "llm", "full"} <= set(extras)
    ingest = " ".join(extras["ingest"]).lower()
    llm = " ".join(extras["llm"]).lower()
    full = " ".join(extras["full"]).lower()
    for name in ("pypdf", "python-docx", "markitdown", "trafilatura",
                 "lxml_html_clean", "watchdog"):
        assert name in ingest and name in full
    assert "openai" in llm and "openai" in full


def test_摄取缺依赖时提示对应extra(monkeypatch):
    from kb.ingest import UnsupportedFormatError, _ingest_import

    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(UnsupportedFormatError) as exc:
        _ingest_import("pypdf", "解析 PDF ")
    assert 'kb-memory[ingest]' in str(exc.value)


def test_bootstrap在Linux先装CPU_torch():
    """Linux PyPI torch 默认 CUDA 轮子体积大，引导脚本必须显式装 CPU 轮子。"""
    text = (REPO_ROOT / "skills" / "kb-memory" / "bootstrap.py").read_text(
        encoding="utf-8")
    assert "download.pytorch.org/whl/cpu" in text
    assert "sys.platform.startswith(\"linux\")" in text
    assert "DETACHED_PROCESS" in text  # Windows 分离启动，关终端不退出
