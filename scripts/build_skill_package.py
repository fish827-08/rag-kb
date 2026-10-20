#!/usr/bin/env python3
"""构建 Agensi 可交付的 kb-memory skill 包（zip 内为单层 kb-memory/ 目录）。

Agensi 要求 zip 内含且仅含一个与 skill 同名的顶层目录，SKILL.md 位于该目录根；
只打包 kb 接入所需的最小集合，不包含测试、运行数据或任何密钥。
English: Build the standalone kb-memory skill package for Agensi. The zip contains
one top-level folder named kb-memory with SKILL.md at its root; it never includes
tests, runtime data or secrets.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "skills" / "kb-memory"

# Agensi 要求的单一顶层目录（与 SKILL.md 的 name 一致）
PACKAGE_DIR = "kb-memory"

# 包内文件名 → 打包后的 zip 路径（相对于 PACKAGE_DIR）
PACKAGE_FILES = {
    "SKILL.md": "SKILL.md",
    "README.md": "README.md",
    "bootstrap.py": "bootstrap.py",
    "mcp.json": "mcp.json",
    "env.example": "env.example",
}

# SKILL.md 必须自包含的关键内容（确认安装 → 发现 → 启动 → 配置 → 验证 → 排障）
REQUIRED_SKILL_MARKERS = (
    "bootstrap.py --status",
    "bootstrap.py --yes",
    'pip install "kb-memory[ingest]"',
    "python -m kb serve",
    "kb where",
    "kb autostart",
    "http://127.0.0.1:8000/mcp/",
    "/api/v1/healthz",
    "write_memory",
    "search_memory",
    "add_document",
    "add_webpage",
    "ask_kb",
    "KB_MEMORY_LANG",
    "rules.md",
)

FORBIDDEN_MARKERS = (
    "_archive",
    ".trae-html-share-packages",
    "e2e-user-test",
)

SECRET_PATTERN = re.compile(r"\bsk-[A-Za-z0-9]{8,}")


class PackageError(RuntimeError):
    """包内容校验失败。English: Package content validation failed."""


def _read_version() -> str:
    text = (REPO_ROOT / "kb" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise PackageError("无法从 kb/__init__.py 读取版本号")
    return match.group(1)


def _validate_front_matter(text: str) -> dict[str, str]:
    """校验标准 YAML front matter：首行 ---，前 20 行内闭合，含 name/description。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PackageError("SKILL.md 首行必须是 ---（标准 YAML front matter）")
    end = None
    for i in range(1, min(len(lines), 20)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise PackageError("SKILL.md front matter 未闭合（缺少第二个 ---）")
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line and not line.lstrip().startswith("#"):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    for key in ("name", "description"):
        if not fields.get(key):
            raise PackageError(f"SKILL.md front matter 缺少 {key}")
    if fields["name"] != "kb-memory":
        raise PackageError(f"SKILL.md name 必须为 kb-memory，实际 {fields['name']!r}")
    return fields


def validate_package() -> None:
    """不落盘地校验源目录；任何问题抛 PackageError。"""
    missing = [name for name in PACKAGE_FILES if not (SOURCE_DIR / name).is_file()]
    if missing:
        raise PackageError(f"skill 包缺少文件: {', '.join(missing)}")

    skill_text = (SOURCE_DIR / "SKILL.md").read_text(encoding="utf-8")
    _validate_front_matter(skill_text)
    for marker in REQUIRED_SKILL_MARKERS:
        if marker not in skill_text:
            raise PackageError(f"SKILL.md 缺少自包含安装/使用说明: {marker!r}")

    all_text = []
    for name in PACKAGE_FILES:
        path = SOURCE_DIR / name
        content = path.read_text(encoding="utf-8")
        all_text.append(content)
        for marker in FORBIDDEN_MARKERS:
            if marker in content:
                raise PackageError(f"{name} 含不应交付的子系统/路径: {marker}")
        if SECRET_PATTERN.search(content):
            raise PackageError(f"{name} 疑似包含真实 API Key，禁止打包")

    # JSON 模板必须可解析
    json.loads((SOURCE_DIR / "mcp.json").read_text(encoding="utf-8"))

    # env 模板只允许空值（防止把本机配置/密钥打进包）
    for line in (SOURCE_DIR / "env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if value.strip():
            raise PackageError(f"env.example 的 {key} 必须为空值（模板不得含真实配置）")


def build(out_dir: Path | None = None) -> Path:
    """校验并生成 zip，返回产物路径。"""
    validate_package()
    version = _read_version()
    out_dir = out_dir or (REPO_ROOT / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"kb-memory-skill-{version}.zip"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        # 统一 LF：Windows 检出为 CRLF 时，zip 内仍保持跨平台一致
        for source_name, archive_name in PACKAGE_FILES.items():
            data = (SOURCE_DIR / source_name).read_bytes().replace(b"\r\n", b"\n")
            zf.writestr(f"{PACKAGE_DIR}/{archive_name}", data)
        license_data = (REPO_ROOT / "LICENSE").read_bytes().replace(b"\r\n", b"\n")
        zf.writestr(f"{PACKAGE_DIR}/LICENSE", license_data)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the standalone kb-memory skill zip")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="输出目录（默认 dist/）")
    args = parser.parse_args(argv)
    try:
        target = build(args.out_dir)
    except PackageError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] {target} ({target.stat().st_size} bytes)")
    with zipfile.ZipFile(target) as zf:
        for name in zf.namelist():
            print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
