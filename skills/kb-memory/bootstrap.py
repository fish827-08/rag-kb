#!/usr/bin/env python3
"""kb-memory skill 引导脚本：发现、安装并启动 kb 服务（仅标准库）。

用法：
    python bootstrap.py                 # 只展示安装计划，不执行
    python bootstrap.py --yes           # 用户确认后执行安装并启动
    python bootstrap.py --yes --extras full --autostart
    python bootstrap.py --status        # 显示已安装服务的位置/解释器/数据目录
    python bootstrap.py --start-only    # 只启动已安装服务

安全约定：未加 --yes 时绝不下载或安装任何东西；安装路径隔离在 ~/.kb/venv，
数据目录 ~/.kb/data，清单 ~/.kb/install.json。

English: kb-memory skill bootstrap: discover, install, and start the kb service
(stdlib only). Without --yes it only prints the plan and changes nothing. The
install is isolated under ~/.kb/venv with data in ~/.kb/data and a manifest at
~/.kb/install.json.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

APP_NAME = "kb"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PIP_PACKAGE = "kb-memory"
PYPI_FALLBACK_INDEX = "https://pypi.org/simple/"
CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"


def _home() -> Path:
    return Path.home() / ".kb"


def manifest_path() -> Path:
    return _home() / "install.json"


def read_manifest() -> dict | None:
    try:
        data = json.loads(manifest_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def service_running(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    try:
        with urllib.request.urlopen(
                f"http://{host}:{port}/api/v1/healthz", timeout=0.8) as resp:
            return resp.status == 200
    except Exception:
        return False


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def build_plan(*, extras: str = "ingest", base_dir: Path | None = None,
               data_dir: Path | None = None, host: str = DEFAULT_HOST,
               port: int = DEFAULT_PORT, install: bool = True) -> dict:
    """构建安装/启动计划（纯函数，不执行任何命令）。"""
    base = Path(base_dir) if base_dir else _home()
    venv = base / "venv"
    py = venv_python(venv)
    data = Path(data_dir) if data_dir else base / "data"
    extra_suffix = f"[{extras}]" if extras and extras != "none" else ""
    commands: list[list[str]] = []
    if install:
        commands.append([sys.executable, "-m", "venv", str(venv)])
        commands.append([str(py), "-m", "pip", "install", "--upgrade", "pip"])
        if sys.platform.startswith("linux"):
            # Linux 的 PyPI torch 默认是 CUDA 轮子（数 GB）；先装 CPU 轮子省下载量
            commands.append([str(py), "-m", "pip", "install", "torch",
                             "--index-url", CPU_TORCH_INDEX])
        commands.append([str(py), "-m", "pip", "install",
                         f"{PIP_PACKAGE}{extra_suffix}"])
    start_command = [str(py), "-m", "kb", "serve", "--non-interactive"]
    return {
        "base_dir": str(base),
        "venv": str(venv),
        "python": str(py),
        "data_dir": str(data),
        "extras": extras,
        "package": f"{PIP_PACKAGE}{extra_suffix}",
        "manifest": str(manifest_path()),
        "commands": commands,
        "start_command": start_command,
        "endpoint": f"http://{host}:{port}/mcp/",
        "health": f"http://{host}:{port}/api/v1/healthz",
        "install": install,
    }


def _display(plan: dict) -> None:
    print("kb 安装计划 (plan)")
    print(f"  隔离环境 : {plan['venv']}")
    print(f"  解释器   : {plan['python']}")
    print(f"  数据目录 : {plan['data_dir']}")
    print(f"  PyPI 包  : {plan['package']}")
    print(f"  MCP 端点 : {plan['endpoint']}")
    if plan["commands"]:
        print("  将执行：")
        for cmd in plan["commands"]:
            print("    " + " ".join(cmd))
        print(f"  说明：若默认源尚未同步 {PIP_PACKAGE}，将自动追加官方 PyPI 兜底"
              f"（{PYPI_FALLBACK_INDEX}）重试。")
    print(f"  启动命令 : {' '.join(plan['start_command'])}")
    if not plan["install"]:
        print("  （当前仅启动已安装服务，不执行安装）")


def fallback_command(cmd: list[str]) -> list[str] | None:
    """给安装 kb-memory 的 pip 命令追加官方 PyPI 兜底源。

    国内镜像同步新包有延迟（实测阿里云曾对 kb-memory 返回 404），用户默认
    index 若指向镜像会安装失败。仅在安装 kb-memory 本身时兜底，其余依赖仍走
    用户配置的镜像；已带 --extra-index-url 的命令不重复追加。
    """
    joined = " ".join(cmd)
    if PIP_PACKAGE not in joined:
        return None
    if any(part.startswith("--extra-index-url") for part in cmd):
        return None
    return [*cmd, "--extra-index-url", PYPI_FALLBACK_INDEX]


def _run(cmd: list[str], runner, capture: bool = False):
    kwargs = {"capture_output": True, "text": True} if capture else {}
    return runner(cmd, check=False, **kwargs)


def install(plan: dict, *, runner=None, popen=None, start: bool = True,
            autostart: bool = False) -> dict:
    """执行安装计划：建 venv → pip 安装 → 写 manifest → 启动服务。

    runner/popen 可注入（测试用假 pip / 假进程）。
    English: Execute the plan: create the venv, pip install, write the manifest,
    and start the service. runner/popen are injectable for tests.
    """
    runner = runner or subprocess.run
    popen = popen or subprocess.Popen
    py = Path(plan["python"])
    if plan["install"]:
        for cmd in plan["commands"]:
            # venv 已存在时跳过创建，但 pip 安装仍执行（支持升级/补装 extras）
            if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv" \
                    and py.exists():
                continue
            proc = _run(cmd, runner)
            code = getattr(proc, "returncode", 0)
            if code != 0:
                retry = fallback_command(cmd)
                if retry is not None:
                    print(f"安装命令失败（exit={code}），改用官方 PyPI 兜底重试……")
                    proc = _run(retry, runner)
                    code = getattr(proc, "returncode", 0)
            if code != 0:
                raise RuntimeError(
                    f"命令失败（exit={code}）：{' '.join(cmd)}")
    version = "unknown"
    try:
        proc = _run([str(py), "-c", "import kb; print(kb.__version__)"],
                    runner, capture=True)
        version = (proc.stdout or "").strip() or "unknown"
    except Exception:
        pass
    manifest_file = Path(plan["manifest"])
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "python": str(py),
        "version": version,
        "data_dir": plan["data_dir"],
        "extras": plan["extras"],
        "venv": plan["venv"],
        "installed_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    started = False
    if start:
        env = dict(os.environ)
        env["KB_DATA_DIR"] = plan["data_dir"]
        kwargs = {"env": env, "cwd": str(Path(plan["base_dir"]))}
        if os.name == "nt":
            kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                       | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            kwargs["start_new_session"] = True
        popen(plan["start_command"], **kwargs)
        started = True
    if autostart:
        _run([str(py), "-m", "kb", "autostart", "enable"], runner)
    return {**payload, "started": started, "manifest": str(manifest_file)}


def _status(host: str, port: int) -> int:
    manifest = read_manifest()
    if not manifest:
        print("未找到安装清单 ~/.kb/install.json。")
        print("运行 `python bootstrap.py` 查看安装计划，或 `--yes` 执行安装。")
        print(f"服务状态：{'running' if service_running(host, port) else 'stopped'}")
        return 1
    print("kb 已安装 (installed)")
    print(f"  解释器   : {manifest.get('python')}")
    print(f"  版本     : {manifest.get('version')}")
    print(f"  数据目录 : {manifest.get('data_dir')}")
    print(f"  虚拟环境 : {manifest.get('venv')}")
    print(f"  服务状态 : {'running' if service_running(host, port) else 'stopped'}")
    print(f"  MCP 端点 : http://{host}:{port}/mcp/")
    return 0


def _start_existing(host: str, port: int, popen=None) -> int:
    manifest = read_manifest()
    if not manifest:
        print("未找到安装清单，请先运行 `python bootstrap.py --yes`。")
        return 1
    if service_running(host, port):
        print(f"服务已在运行：http://{host}:{port}/mcp/")
        return 0
    python = manifest.get("python") or ""
    if not python or not Path(python).exists():
        print(f"清单中的解释器不存在：{python!r}，请重新安装。")
        return 1
    popen = popen or subprocess.Popen
    env = dict(os.environ)
    if manifest.get("data_dir"):
        env["KB_DATA_DIR"] = manifest["data_dir"]
    kwargs = {"env": env, "cwd": str(Path.home())}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    popen([python, "-m", "kb", "serve", "--non-interactive"], **kwargs)
    print(f"已启动 kb 服务：http://{host}:{port}/mcp/")
    print("提示：首次检索会加载嵌入模型；空闲 10 分钟后自动卸载释放内存。")
    return 0


def main(argv: list[str] | None = None, *, runner=None, popen=None) -> int:
    parser = argparse.ArgumentParser(
        description="kb-memory skill bootstrap (discover / install / start)")
    parser.add_argument("--yes", action="store_true",
                        help="用户已确认，执行安装（默认只展示计划）")
    parser.add_argument("--extras", default="ingest",
                        choices=("ingest", "llm", "full", "none"),
                        help="安装的 PyPI extras（默认 ingest）")
    parser.add_argument("--dir", default=None, help="安装根目录（默认 ~/.kb）")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 ~/.kb/data）")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-start", action="store_true", help="安装后不启动服务")
    parser.add_argument("--autostart", action="store_true",
                        help="安装后启用登录自启")
    parser.add_argument("--status", action="store_true",
                        help="显示已安装服务的位置与运行状态")
    parser.add_argument("--start-only", action="store_true",
                        help="只启动已安装服务（不安装）")
    args = parser.parse_args(argv)

    if service_running(args.host, args.port):
        print(f"kb 服务已在运行：http://{args.host}:{args.port}/mcp/")
        if args.status:
            _status(args.host, args.port)
        return 0
    if args.status:
        return _status(args.host, args.port)
    if args.start_only:
        return _start_existing(args.host, args.port, popen=popen)

    plan = build_plan(extras=args.extras, base_dir=args.dir,
                      data_dir=args.data_dir, host=args.host, port=args.port)
    _display(plan)
    if not args.yes:
        print("\n未执行安装（等待用户确认）。")
        print("确认后运行：python bootstrap.py --yes"
              + (f" --extras {args.extras}" if args.extras != "ingest" else ""))
        return 0
    result = install(plan, runner=runner, popen=popen,
                     start=not args.no_start, autostart=args.autostart)
    print(f"\n安装完成：version={result['version']} "
          f"manifest={result['manifest']}")
    print(f"数据目录：{result['data_dir']}")
    if result["started"]:
        print(f"服务启动中：http://{args.host}:{args.port}/mcp/（首次检索会加载模型）")
    else:
        print(f"未启动服务；稍后运行：python bootstrap.py --start-only")
    if not args.autostart:
        print("如需开机自启：python -m kb autostart enable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
