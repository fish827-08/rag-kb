"""N30 MCP stdio 入口单测（A2.5 生态合规：uvx/客户端一键拉起路径）。

覆盖：
1. CLI 注册 `kb mcp` 命令（--help 可见）；
2. 命令路径装配：KBService → create_mcp_server → run_stdio_async（monkeypatch 全链路打桩）；
3. 非交互安全：stdio 协议流上不得触发设备交互询问；
4. server.json / README mcp-name 注释一致性（Registry 归属验证前置）。
"""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------- CLI 命令注册 ----------

def test_cli_注册mcp命令_help可见():
    """`kb mcp --help` 正常退出且描述 stdio 用途。"""
    from typer.testing import CliRunner
    from kb.cli import app
    r = CliRunner().invoke(app, ["mcp", "--help"])
    assert r.exit_code == 0
    assert "stdio" in r.output


# ---------- 装配链路（全链路打桩，不加载真实模型） ----------

def test_mcp命令_装配并运行stdio服务器(env_isolated, monkeypatch):
    """kb mcp：创建 KBService → 注册 8 工具的 MCPServer → run_stdio_async 被执行。"""
    import kb.cli as cli
    import kb.mcp as mcp_mod
    import kb.service as svc_mod
    from kb.cli import mcp_stdio

    run_calls = []          # anyio.run 收到的协程记录
    servers = []            # create_mcp_server 返回的服务器记录

    class FakeServer:
        def __init__(self):
            self.ran = False

        async def run_stdio_async(self):
            self.ran = True

    def fake_create(service):
        s = FakeServer()
        servers.append((s, service))
        return s

    def fake_run(coro):
        run_calls.append(coro)
        # anyio.run 收到的是协程函数/绑定方法（真 anyio.run 会自行调用），此处手动驱动
        if callable(coro):
            coro = coro()
        try:
            coro.send(None)  # FakeServer.run_stdio_async 无 await，一次 send 完成
        except StopIteration:
            pass

    monkeypatch.setattr(svc_mod, "KBService", lambda settings: object())
    monkeypatch.setattr(mcp_mod, "create_mcp_server", fake_create)
    monkeypatch.setattr("anyio.run", fake_run)

    mcp_stdio()

    assert len(servers) == 1, "应恰好创建一个 MCP 服务器"
    assert servers[0][0].ran, "run_stdio_async 应被真实执行"
    assert run_calls, "anyio.run 应被调用"


def test_mcp命令_不触发设备交互询问(env_isolated, monkeypatch):
    """stdio 模式设备检测必须 non-interactive（协议流上不能有询问）。"""
    import kb.mcp as mcp_mod
    import kb.service as svc_mod
    from kb.cli import mcp_stdio

    def forbidden_input(prompt=""):
        raise AssertionError("stdio 模式不得触发交互询问")

    monkeypatch.setattr("builtins.input", forbidden_input)
    monkeypatch.setattr(svc_mod, "KBService", lambda settings: object())

    async def idle():
        return None

    class FakeServer:
        async def run_stdio_async(self):
            return None

    monkeypatch.setattr(mcp_mod, "create_mcp_server", lambda s: FakeServer())

    def fake_run(coro):
        if callable(coro):
            coro = coro()
        try:
            coro.send(None)
        except StopIteration:
            pass

    monkeypatch.setattr("anyio.run", fake_run)

    mcp_stdio()  # 不应抛 AssertionError


# ---------- Registry 提交材料一致性 ----------

def test_serverjson_结构合法():
    """仓库根 server.json：必需字段齐备，远程流式 HTTP 端点自洽。

    历史说明：旧清单声明 PyPI 包 `kb-memory` + uvx stdio，但该包从未发布
    （PyPI 返回 404），任何按清单接入的用户都会第一步失败。现改为声明本项目
    真实提供的本地 streamable-http 端点；待将来真正发布 PyPI 包后再补 packages。
    """
    f = REPO_ROOT / "server.json"
    assert f.exists(), "缺少 server.json（MCP Registry 提交材料）"
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["name"].startswith("io.github.")
    assert data["version"], "顶层 version 必填"
    assert "packages" not in data or data["packages"], (
        "packages 若存在则不得为空；未发布 PyPI 包时请改用 remotes")
    remotes = data.get("remotes") or []
    assert remotes, "至少一个 remotes 条目（本项目以本地 HTTP 端点提供）"
    remote = remotes[0]
    assert remote["type"] == "streamable-http"
    assert remote["url"].startswith("http://127.0.0.1:")
    assert remote["url"].endswith("/mcp/")


def test_serverjson版本与包版本一致():
    """清单版本必须与 kb.__version__ 一致，避免文档/发布漂移。"""
    import kb
    data = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    assert data["version"] == kb.__version__


def test_readme_mcpname注释_与serverjson一致():
    """README 含 mcp-name 注释且与 server.json.name 完全一致（PyPI 归属验证规则）。"""
    data = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {data['name']}" in readme, (
        "README 缺少 'mcp-name: <server.json.name>' 注释（Registry 校验 PyPI README）")
