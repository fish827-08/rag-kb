"""Agensi 交付包验收：标准 SKILL.md、自包含说明、配置模板与 zip 结构。"""
import json
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "kb-memory"


def _front_matter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---"
    end = next(i for i in range(1, min(len(lines), 20))
               if lines[i].strip() == "---")
    fields = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def test_skill_front_matter标准且可解析():
    """第二轮 P0：首行 *** / 长横线不是合法 YAML front matter。"""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "***" not in text.splitlines()[0]
    fields = _front_matter(text)
    assert fields["name"] == "kb-memory"
    assert fields["description"]
    # 英文 description，便于英文 marketplace 检索
    assert any(word in fields["description"].lower()
               for word in ("memory", "local", "rag"))


def test_skill自包含安装启动配置验证排障():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for marker in (
        "git clone https://github.com/fish827-08/rag-kb.git",
        "pip install -r requirements.txt",
        "pip install -e .",
        "python -m kb serve",
        "127.0.0.1:8000/mcp/",
        "/api/v1/healthz",
        "HF_ENDPOINT",
        "KB_API_PORT",
    ):
        assert marker in text, f"SKILL.md 缺少 {marker!r}"


def test_skill包不依赖orchestra且无密钥():
    for path in SKILL_DIR.iterdir():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "orchestra" not in text
        assert "_archive" not in text
        assert "e2e-user-test" not in text


def test_mcp模板可直接挂载():
    data = json.loads((SKILL_DIR / "mcp.json").read_text(encoding="utf-8"))
    server = data["mcpServers"]["kb"]
    assert server["type"] == "http"
    assert server["url"] == "http://127.0.0.1:8000/mcp/"


def test_env模板不含真实配置():
    for line in (SKILL_DIR / "env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        _key, value = stripped.split("=", 1)
        assert not value.strip(), f"env.example 不应含真实值: {stripped}"


def test_build_script产物结构正确(tmp_path):
    import importlib.util
    script = REPO_ROOT / "scripts" / "build_skill_package.py"
    spec = importlib.util.spec_from_file_location("build_skill_package", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target = module.build(tmp_path)
    assert target.exists() and target.name.startswith("kb-memory-skill-")
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        assert "SKILL.md" in names, "SKILL.md 必须位于 zip 根目录"
        assert {"README.md", "mcp.json", "env.example", "LICENSE"} <= names
        assert all("/" not in n for n in names), "交付包应平铺在 zip 根目录"
        skill = zf.read("SKILL.md").decode("utf-8")
        assert skill.startswith("---\n")


def test_build_script拒绝真实密钥(tmp_path, monkeypatch):
    import importlib.util
    script = REPO_ROOT / "scripts" / "build_skill_package.py"
    spec = importlib.util.spec_from_file_location("build_skill_package", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_skill = tmp_path / "skill"
    fake_skill.mkdir()
    for name in module.PACKAGE_FILES:
        (fake_skill / name).write_text(
            (SKILL_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    (fake_skill / "README.md").write_text(
        (fake_skill / "README.md").read_text(encoding="utf-8")
        + "\nkey: sk-abcdefgh12345678\n", encoding="utf-8")
    monkeypatch.setattr(module, "SOURCE_DIR", fake_skill)
    with pytest.raises(module.PackageError):
        module.validate_package()
