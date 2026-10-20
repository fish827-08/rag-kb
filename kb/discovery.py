"""服务发现与登录自启（v2.1 spec §3.3）。

安装清单 `~/.kb/install.json` 由 skill 引导脚本写入；`kb where` 按
manifest → PATH → importlib.metadata 的顺序定位服务安装，`kb autostart`
在 Windows 启动目录 / systemd user unit / macOS LaunchAgent 之间切换。

English: Service discovery and login autostart (v2.1 spec §3.3). The install
manifest ~/.kb/install.json is written by the skill bootstrap; `kb where`
locates the service via manifest → PATH → importlib.metadata, and
`kb autostart` manages the Windows Startup entry / systemd user unit /
macOS LaunchAgent.
"""
import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "kb"
UNIT_NAME = "kb.service"
LAUNCH_LABEL = "io.github.fish827.kb"


def manifest_path() -> Path:
    """安装清单路径：~/.kb/install.json。"""
    return Path.home() / ".kb" / "install.json"


def read_manifest() -> dict | None:
    """读取安装清单；缺失或损坏返回 None。"""
    path = manifest_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_manifest(*, python: str, version: str, data_dir: str,
                   extras: str = "", venv: str = "") -> Path:
    """写入安装清单（skill 引导安装成功后调用）。
    English: Write the install manifest after a successful bootstrap install."""
    from datetime import datetime
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "python": str(python),
        "version": version,
        "data_dir": str(data_dir),
        "extras": extras,
        "venv": str(venv),
        "installed_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def _package_version() -> str:
    try:
        from importlib.metadata import version
        return version("kb-memory")
    except Exception:
        from kb import __version__
        return __version__


def _running(host: str, port: int) -> bool:
    """探测本机服务健康端点（0.5s 超时，不依赖第三方库）。"""
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://{host}:{port}/api/v1/healthz", timeout=0.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def discover(settings=None) -> dict:
    """按 manifest → PATH → 当前解释器 的顺序发现服务安装。

    返回 {source, python, version, data_dir, running, endpoint, command}。
    English: Discover the service installation in manifest → PATH → current
    interpreter order. Returns a dict describing the installation.
    """
    if settings is None:
        from kb.config import get_settings
        settings = get_settings()
    host, port = settings.api_host, settings.api_port
    manifest = read_manifest()
    if manifest and manifest.get("python") and Path(manifest["python"]).exists():
        return {
            "source": "manifest",
            "python": manifest["python"],
            "version": manifest.get("version") or _package_version(),
            "data_dir": manifest.get("data_dir") or str(settings.data_dir),
            "venv": manifest.get("venv") or "",
            "running": _running(host, port),
            "endpoint": f"http://{host}:{port}/mcp/",
            "command": [manifest["python"], "-m", "kb", "serve",
                        "--non-interactive"],
        }
    kb_bin = shutil.which("kb")
    if kb_bin:
        python = sys.executable
        if os.name == "nt":
            candidate = Path(kb_bin).with_name("python.exe")
            if candidate.exists():
                python = str(candidate)
        return {
            "source": "path",
            "python": python,
            "version": _package_version(),
            "data_dir": str(settings.data_dir),
            "venv": "",
            "running": _running(host, port),
            "endpoint": f"http://{host}:{port}/mcp/",
            "command": [python, "-m", "kb", "serve", "--non-interactive"],
        }
    return {
        "source": "package",
        "python": sys.executable,
        "version": _package_version(),
        "data_dir": str(settings.data_dir),
        "venv": "",
        "running": _running(host, port),
        "endpoint": f"http://{host}:{port}/mcp/",
        "command": [sys.executable, "-m", "kb", "serve",
                    "--non-interactive"],
    }


# ---- 登录自启 ----

def autostart_file() -> Path:
    """当前平台的登录自启文件路径。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
        return (Path(base) / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / "Startup" / "kb.cmd")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _service_target(info: dict) -> tuple[str, str]:
    """确定自启解释器与数据目录：manifest 优先，其次当前解释器/配置。"""
    python = info.get("python") or sys.executable
    data_dir = info.get("data_dir") or ""
    return python, data_dir


def _windows_content(info: dict) -> str:
    python, data_dir = _service_target(info)
    pythonw = str(Path(python).with_name("pythonw.exe"))
    exe = pythonw if Path(pythonw).exists() else python
    lines = ["@echo off"]
    if data_dir:
        lines.append(f'set "KB_DATA_DIR={data_dir}"')
    lines.append(f'start "" "{exe}" -m kb serve --non-interactive')
    return "\r\n".join(lines) + "\r\n"


def _systemd_content(info: dict) -> str:
    python, data_dir = _service_target(info)
    env = f"Environment=KB_DATA_DIR={data_dir}\n" if data_dir else ""
    return (
        "[Unit]\n"
        "Description=kb local memory & knowledge service\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={python} -m kb serve --non-interactive\n"
        f"{env}"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _launchd_content(info: dict) -> str:
    python, data_dir = _service_target(info)
    env = ""
    if data_dir:
        env = ("    <key>EnvironmentVariables</key>\n    <dict>\n"
               "        <key>KB_DATA_DIR</key>\n"
               f"        <string>{data_dir}</string>\n    </dict>\n")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0">\n<dict>\n'
        f'    <key>Label</key>\n    <string>{LAUNCH_LABEL}</string>\n'
        '    <key>ProgramArguments</key>\n    <array>\n'
        f'        <string>{python}</string>\n'
        '        <string>-m</string>\n        <string>kb</string>\n'
        '        <string>serve</string>\n'
        '        <string>--non-interactive</string>\n    </array>\n'
        '    <key>RunAtLoad</key>\n    <true/>\n'
        f'{env}'
        '</dict>\n</plist>\n'
    )


def autostart_enable(info: dict | None = None, run: bool = True) -> dict:
    """写入并启用登录自启；返回 {file, platform, activated}。
    English: Write and enable login autostart; returns {file, platform, activated}."""
    import subprocess
    info = info or discover()
    path = autostart_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    activated = False
    if os.name == "nt":
        path.write_text(_windows_content(info), encoding="utf-8",
                        newline="")
    elif sys.platform == "darwin":
        path.write_text(_launchd_content(info), encoding="utf-8")
        if run:
            activated = _run(["launchctl", "load", str(path)]) is not None
    else:
        path.write_text(_systemd_content(info), encoding="utf-8")
        if run:
            _run(["systemctl", "--user", "daemon-reload"])
            activated = _run(["systemctl", "--user", "enable", "--now",
                              UNIT_NAME]) is not None
    return {"file": str(path), "platform": sys.platform,
            "activated": activated}


def autostart_disable(run: bool = True) -> dict:
    """删除自启文件并停用；返回 {file, removed, deactivated}。"""
    path = autostart_file()
    deactivated = False
    if run and sys.platform == "darwin" and path.exists():
        deactivated = _run(["launchctl", "unload", str(path)]) is not None
    elif run and os.name != "nt" and path.exists():
        _run(["systemctl", "--user", "disable", "--now", UNIT_NAME])
        deactivated = _run(["systemctl", "--user", "daemon-reload"]) is not None
    removed = path.exists()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        removed = False
    return {"file": str(path), "removed": removed,
            "deactivated": deactivated}


def autostart_status() -> dict:
    """自启状态：是否已配置 + 文件路径 + 平台。"""
    path = autostart_file()
    return {"enabled": path.exists(), "file": str(path),
            "platform": sys.platform}


def _run(cmd: list[str]) -> int | None:
    """执行系统命令并吞掉输出；失败返回 None（不抛异常，自启失败可降级提示）。"""
    import subprocess
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=15)
        return proc.returncode if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None
