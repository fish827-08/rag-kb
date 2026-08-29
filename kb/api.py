"""REST API：FastAPI 应用工厂 + memories CRUD + healthz + ask + 统一错误格式。
English: REST API: FastAPI app factory with memories CRUD, healthz, ask, and a unified error format."""
import hmac
import json
import logging
import tempfile
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.requests import Request
from pydantic import BaseModel, Field, field_validator

from kb import __version__
from kb.config import Settings, get_settings
from kb.logging_setup import setup_logging
from kb.monitor import MonitorAgent, run_once_summary
from kb.mcp import create_mcp_server
from kb.service import (KBService, LLMDisabledError, UnsupportedFormatError,
                        WebFetchError)
from kb.watcher import KBWatcher


# ---- 日志查看端点常量（N18）----
SCAN_MAX = 20000          # /logs 尾部向后扫描行数上限
LOG_LIMIT_MAX = 1000      # /logs limit 上限
EVENT_WINDOW_MAX = 10000  # /logs/events window 上限


def exposure_warning(host: str, api_key: str) -> str | None:
    """N29 启动安全警告：非回环监听且未启用鉴权时返回警告文本，否则 None。

    判定口径（与 N19 鉴权中间件一致）：
    - key 为空或全空白 → 视为未鉴权；
    - host 为空串（默认 127.0.0.1）、localhost、::1 或 127.0.0.0/8 段 → 视为回环，本地零摩擦；
    - 其余地址（0.0.0.0 / 内网 IP / 公网 IP）且无 key → 返回醒目警告。
    English: N29 startup security warning: return a warning text when listening on a non-loopback and auth is
    not enabled, otherwise None. Judgment rules (consistent with the N19 auth middleware): an empty or all-blank
    key is treated as unauthenticated; an empty host (default 127.0.0.1), localhost, ::1, or the 127.0.0.0/8 range
    is treated as loopback (zero local friction); any other address (0.0.0.0 / LAN IP / public IP) with no key
    returns a prominent warning.
    """
    if (api_key or "").strip():
        return None
    h = (host or "").strip().lower()
    if h in ("", "localhost", "::1") or h.startswith("127."):
        return None
    return ("安全警告：服务监听 %s（非回环地址）且未设置 KB_API_KEY，"
            "REST/MCP 端点将对网络完全开放（记忆读写/文档删除均可匿名调用）。"
            "如确需对外监听，请设置 KB_API_KEY 启用鉴权，或改回 127.0.0.1 仅本机访问。"
            % host)


_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
# logging 缩写别名（大小写不敏感归一）：warn/err/fatal → 标准级别名
_LOG_LEVEL_ALIASES = {"WARN": "WARNING", "ERR": "ERROR", "FATAL": "CRITICAL"}


def _parse_log_line(line: str) -> dict | None:
    """解析一行日志（asctime | level | name | message）为 dict。

    message 可能含 "|" 需保留余下全部（只切前 3 个 |）；字段不足或内容
    非法返回 None，调用方跳过不中断读取。返回 dict 不含 line 字段，
    行号由调用方按文件位置补充。
    English: Parse one log line (asctime | level | name | message) into a dict.
    The message may contain "|" and must keep all the remainder (only the first 3 "|" are split); a dict with
    too few fields or invalid content returns None and the caller skips it without interrupting the read.
    The returned dict has no line field; the line number is filled in by the caller from the file position."""
    if not line or not line.strip():
        return None
    parts = line.split("|", 3)
    if len(parts) < 4:
        return None
    time_s, level_s, name_s, message = (p.strip() for p in parts)
    if not time_s or not level_s or not name_s or not message:
        return None
    return {"time": time_s, "level": level_s, "logger": name_s,
            "message": message}


def _read_log_tail(path: Path, scan_max: int = SCAN_MAX) -> tuple[list[str], bool, int]:
    """读日志文件尾部至多 scan_max 行，返回 (lines, truncated, start_line)。

    lines 按文件顺序（时间升序）；truncated 表示文件行数超过 scan_max 提前
    截断（可能漏掉更早行）；start_line 为 lines[0] 的 1 基文件行号（定位用）。
    文件不存在/为空 → ([], False, 1)，不视为错误；文件可存在但不可读时抛
    OSError（由端点转 500 LOG_READ_ERROR）。
    English: Read the tail of a log file, at most scan_max lines, returning (lines, truncated, start_line).
    lines follow file order (ascending time); truncated indicates the file has more than scan_max lines and
    earlier lines may be missed; start_line is the 1-based file line of lines[0] (for locating).
    A nonexistent/empty file → ([], False, 1), not an error; an existing-but-unreadable file raises OSError
    (mapped to 500 LOG_READ_ERROR by the endpoint)."""
    if not path.exists():
        return [], False, 1
    total = 0
    tail = deque(maxlen=scan_max)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            total += 1
            tail.append(line.rstrip("\n"))
    truncated = total > scan_max
    start_line = max(total - len(tail) + 1, 1)
    return list(tail), truncated, start_line


def _normalize_level(level: str | None) -> str | None:
    """level 参数：大小写不敏感 + 缩写别名归一（warn→WARNING 等）；非法抛 422。
    English: level param: case-insensitive plus abbreviation-alias normalization (warn→WARNING etc.); illegal raises 422."""
    if level is None:
        return None
    lvl = _LOG_LEVEL_ALIASES.get(level.strip().upper(), level.strip().upper())
    if lvl not in _LOG_LEVELS:
        raise HTTPException(status_code=422, detail={
            "error": "INVALID_LEVEL",
            "message": f"level 非法：{level}，可选 {', '.join(sorted(_LOG_LEVELS))}"})
    return lvl


# ---- 根路径极简导航页（N19）：纯字符串 HTML，无模板依赖，配色与看板一致 ----
_NAV_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>kb 记忆服务</title>
<style>
  body { margin: 0; min-height: 100vh; background: #f4f6f8; color: #24292f;
         font-family: "Microsoft YaHei", system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center; }
  .card { width: 420px; max-width: 90vw; border-radius: 12px; overflow: hidden;
          background: #fff; border: 1px solid #e3e8ee;
          box-shadow: 0 4px 16px rgba(0,0,0,.08); }
  .head { padding: 18px 24px; color: #fff;
          background: linear-gradient(135deg, #1e293b 0%, #312e81 100%); }
  .head h1 { margin: 0; font-size: 20px; font-weight: 600; }
  .body { padding: 12px 8px; }
  .body a { display: block; padding: 12px 16px; margin: 4px 8px; border-radius: 8px;
            color: #1e293b; text-decoration: none; font-size: 15px;
            border: 1px solid #e3e8ee; transition: background .15s; }
  .body a:hover { background: #f0f2f5; }
</style>
</head>
<body>
  <div class="card">
    <div class="head"><h1>kb 记忆服务</h1></div>
    <div class="body">
      <a href="/api/v1/healthz">API 状态</a>
      <a href="/dashboard/">HTML 看板</a>
      <a href="/docs">MCP 端点文档</a>
    </div>
  </div>
</body>
</html>
"""


class MemoryCreate(BaseModel):
    """创建记忆请求。
    English: Create-memory request."""
    content: str
    tags: list[str] = []
    source: str | None = None
    namespace: str = "default"
    agent_id: str = "default"          # 写入方 Agent 身份（A 节点；推荐任务名）；默认 default
    client: str = "default"            # 来源客户端（可选，如 curl/Python/TraeWork）；默认 default
    project: str | None = None         # 项目名（可选，仅用于审计文件名归类）

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, v: str) -> str:
        """拒绝空串与纯空白内容（strip 后为空即非法，pydantic 校验失败转 422）。
        English: Reject empty and all-blank content (blank after strip is illegal; a pydantic validation
        failure maps to 422)."""
        if not v.strip():
            raise ValueError("content 不能为空或纯空白")
        return v

    @field_validator("agent_id", "client", "project")
    @classmethod
    def identity_fields_valid(cls, v: str | None, info) -> str | None:
        """规约校验身份字段（A 节点）：agent_id/client/project 白名单格式，
        非法直接 422，杜绝调用方随意传任意字符串。
        English: Validate identity fields against the whitelist; invalid values are rejected
        with 422 so callers cannot pass arbitrary strings."""
        from kb.service import validate_agent_id, validate_client, validate_project
        if info.field_name == "agent_id":
            err = validate_agent_id(v)
        elif info.field_name == "client":
            err = validate_client(v)
        else:
            err = validate_project(v)
        if err:
            raise ValueError(err)
        return v


class MemoryUpdate(BaseModel):
    """更新记忆请求；至少一项非空。
    English: Update-memory request; at least one field must be non-empty."""
    content: str | None = None
    tags: list[str] | None = None


class SearchRequest(BaseModel):
    """检索请求；query 必填，top_k/mode 带默认值，type/tag 可选过滤。
    agent_id：A 节点强制隔离——memory 只返回归属该 Agent 的，doc/web 共享。
    client：来源客户端（可选）。
    English: Search request; query is required, top_k/mode have defaults, and type/tag are optional filters.
    agent_id: A-node mandatory isolation — memory records only return those owned by the calling agent,
    doc/web chunks are shared. client: source client (optional)."""
    query: str
    top_k: int = Field(default=5, ge=1)
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid"
    type: str | None = None
    tag: str | None = None
    agent_id: str = "default"
    client: str = "default"
    project: str | None = None  # 项目名（可选，仅用于审计文件名归类）

    @field_validator("agent_id", "client", "project")
    @classmethod
    def identity_fields_valid(cls, v: str | None, info) -> str | None:
        """规约校验身份字段（A 节点），非法 422。"""
        from kb.service import validate_agent_id, validate_client, validate_project
        if info.field_name == "agent_id":
            err = validate_agent_id(v)
        elif info.field_name == "client":
            err = validate_client(v)
        else:
            err = validate_project(v)
        if err:
            raise ValueError(err)
        return v


class AskRequest(BaseModel):
    """问答请求；question 必填。
    English: Ask request; question is required."""
    question: str
    agent_id: str = "default"          # A 节点：问答检索按该 Agent 隔离 memory
    client: str = "default"            # 来源客户端（可选）
    project: str | None = None         # 项目名（可选，仅用于审计文件名归类）

    @field_validator("agent_id", "client", "project")
    @classmethod
    def identity_fields_valid(cls, v: str | None, info) -> str | None:
        """规约校验身份字段（A 节点），非法 422。"""
        from kb.service import validate_agent_id, validate_client, validate_project
        if info.field_name == "agent_id":
            err = validate_agent_id(v)
        elif info.field_name == "client":
            err = validate_client(v)
        else:
            err = validate_project(v)
        if err:
            raise ValueError(err)
        return v


class WebIngestRequest(BaseModel):
    """网页摄取请求；url 必填。
    English: Web-ingest request; url is required."""
    url: str


def _wrap_sse_charset(app):
    """轻量 ASGI 中间件：给 MCP 的 SSE 响应头补 charset=utf-8。

    纯函数式包装：拦截 http.response.start 消息，当 content-type 为
    text/event-stream 且未声明 charset 时追加 "; charset=utf-8"，
    兼容按默认编码解码 SSE 流的客户端；其余消息与作用域原样透传。
    English: Lightweight ASGI middleware: append charset=utf-8 to the MCP SSE response header.
    A pure functional wrapper: intercept http.response.start messages and append "; charset=utf-8" when the
    content-type is text/event-stream and no charset is declared, for clients that decode the SSE stream with
    the default encoding; all other messages and scopes pass through unchanged."""

    async def middleware(scope, receive, send):
        # 非 HTTP 作用域（如 lifespan）直接透传
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        async def send_wrapper(message):
            # 只在响应起始消息上补头；命中时构造新消息，不改动原 dict
            if message["type"] == "http.response.start":
                headers = message.get("headers") or []
                new_headers = []
                patched = False
                for k, v in headers:
                    if (k.lower() == b"content-type"
                            and v.lower().startswith(b"text/event-stream")
                            and b"charset" not in v.lower()):
                        new_headers.append((k, v + b"; charset=utf-8"))
                        patched = True
                    else:
                        new_headers.append((k, v))
                if patched:
                    message = {**message, "headers": new_headers}
            await send(message)

        await app(scope, receive, send_wrapper)

    return middleware


def _wrap_json_charset(app):
    """轻量 ASGI 中间件：给 JSON REST 响应头补 charset=utf-8。

    与 v1.0.1 的 SSE 中间件同构：拦截 http.response.start 消息，
    content-type 为 application/json 且未声明 charset 时追加
    "; charset=utf-8"，解决 PowerShell 等按默认编码解码的客户端
    对中文 JSON 的乱码问题；其余消息与作用域原样透传（含挂载的
    /mcp SSE 子应用，其 text/event-stream 头不在此匹配范围）。
    English: Lightweight ASGI middleware: append charset=utf-8 to JSON REST response headers.
    Isomorphic to the v1.0.1 SSE middleware: intercept http.response.start messages and append "; charset=utf-8"
    when the content-type is application/json with no declared charset, fixing mojibake of Chinese JSON for
    clients that decode with the default encoding (e.g. PowerShell); all other messages and scopes pass
    through unchanged (including the mounted /mcp SSE sub-app, whose text/event-stream header is not matched
    here)."""

    async def middleware(scope, receive, send):
        # 非 HTTP 作用域（如 lifespan）直接透传
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        async def send_wrapper(message):
            # 只在响应起始消息上补头；命中时构造新消息，不改动原 dict
            if message["type"] == "http.response.start":
                headers = message.get("headers") or []
                new_headers = []
                patched = False
                for k, v in headers:
                    if (k.lower() == b"content-type"
                            and v.lower().startswith(b"application/json")
                            and b"charset" not in v.lower()):
                        new_headers.append((k, v + b"; charset=utf-8"))
                        patched = True
                    else:
                        new_headers.append((k, v))
                if patched:
                    message = {**message, "headers": new_headers}
            await send(message)

        await app(scope, receive, send_wrapper)

    return middleware


def _wrap_request_log(app):
    """请求访问日志中间件（N17/TASK-0012）：ASGI 栈最外层，REST 与 MCP 统一覆盖。

    请求进入记录 event=request.start（method/path），请求结束记录
    event=request.end（method/path/status/耗时ms）。不记录请求 body
    （日志设计第 5 节敏感红线）；MCP 挂载子应用（/mcp）同样经过本层。
    English: Request access-log middleware (N17/TASK-0012): the outermost ASGI layer, covering REST and MCP alike.
    On entry it logs event=request.start (method/path); on exit it logs event=request.end (method/path/status/latency-ms).
    It never logs request bodies (sensitive red line in the logging-design section 5); the mounted MCP sub-app
    (/mcp) also passes through this layer."""

    async def middleware(scope, receive, send):
        # 非 HTTP 作用域（lifespan 等）原样透传
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        api_log = logging.getLogger("kb.api")
        method = scope.get("method", "")
        path = scope.get("path", "")
        api_log.info("request.start method=%s path=%s", method, path)
        start = time.perf_counter()
        status = 0

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message.get("status", 0)
            await send(message)

        try:
            await app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            api_log.info("request.end method=%s path=%s status=%s 耗时=%.1fms",
                         method, path, status, elapsed_ms)

    return middleware


def _wrap_api_key(app, settings):
    """API Key 鉴权中间件（N19/TASK-0062）：纯 ASGI，空 key 直通，非空校验 Bearer/X-API-Key。

    与 _wrap_sse_charset / _wrap_request_log 同模式（纯函数式包装，不依赖 FastAPI
    依赖注入，确保覆盖 mount 的 /mcp 子应用与 /dashboard 静态挂载）。

    流程：
      1) settings.api_key 为空 → 直通（降级模式，等同 v1.x 行为）
      2) 白名单 GET /api/v1/healthz → 放行（存活探针）
      3) 取 key：Authorization: Bearer <key> 优先，其次 X-API-Key: <key>
      4) 缺失/不匹配 → 401 JSON {"error":"UNAUTHORIZED","message":"missing or invalid api key"}
         （Content-Type: application/json; charset=utf-8；不区分缺失与错误，防探测）
      5) hmac.compare_digest 匹配 → 放行

    注册顺序（create_app 内，从内到外）：业务 → 本中间件 → JSON charset → 访问日志；
    即请求进入：访问日志 → JSON charset → 鉴权 → 业务；401 响应向外经 JSON charset
    补 charset（本中间件自身也显式声明 charset=utf-8，双保险）。
    English: API-Key auth middleware (N19/TASK-0062): pure ASGI; empty key passes through, non-empty validates
    Bearer/X-API-Key. Same pattern as _wrap_sse_charset / _wrap_request_log (pure functional wrapping, no FastAPI
    dependency injection, ensuring coverage of the mounted /mcp sub-app and the /dashboard static mount).
    Flow: 1) empty settings.api_key → pass through (legacy mode); 2) whitelist GET /api/v1/healthz → allow (liveness);
    3) extract key: Authorization: Bearer <key> first, then X-API-Key: <key>;
    4) missing/mismatch → 401 JSON {"error":"UNAUTHORIZED","message":"missing or invalid api key"}
    (Content-Type: application/json; charset=utf-8; no distinction between missing and wrong, to prevent probing);
    5) hmac.compare_digest match → allow.
    Registration order (in create_app, inner to outer): business → this middleware → JSON charset → access log;
    so requests enter: access log → JSON charset → auth → business; the 401 response gets charset added by the
    JSON charset wrapper as it flows out (this middleware also declares charset=utf-8 explicitly, double insurance)."""
    api_key = (settings.api_key or "").strip()

    async def middleware(scope, receive, send):
        # 非 HTTP 作用域（lifespan 等）原样透传
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        # 空 key 直通（降级模式）
        if not api_key:
            await app(scope, receive, send)
            return
        method = scope.get("method", "")
        path = scope.get("path", "")
        # 白名单：GET /api/v1/healthz（存活探针，无敏感数据）
        if method == "GET" and path == "/api/v1/healthz":
            await app(scope, receive, send)
            return
        # 取 key：Authorization: Bearer <key> 优先
        headers = scope.get("headers", [])
        provided_key = ""
        for k, v in headers:
            if k.lower() == b"authorization":
                val = v.decode("latin-1", errors="replace")
                if val.lower().startswith("bearer "):
                    provided_key = val[7:].strip()
                    break
        # 其次 X-API-Key: <key>
        if not provided_key:
            for k, v in headers:
                if k.lower() == b"x-api-key":
                    provided_key = v.decode("latin-1", errors="replace").strip()
                    break
        # compare_digest 比较（防时序攻击）；缺失或不匹配均 401（不区分，防探测）
        if not provided_key or not hmac.compare_digest(provided_key, api_key):
            body = json.dumps(
                {"error": "UNAUTHORIZED",
                 "message": "missing or invalid api key"}
            ).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        # 匹配放行
        await app(scope, receive, send)

    return middleware


def create_app(settings: Settings | None = None,
               enable_watcher: bool = False) -> FastAPI:
    """应用工厂；全局单例 KBService 挂 app.state.kb（REST 与 MCP 共享该实例）。
    统一错误 JSON：{"error": "<CODE>", "message": "<人话>"}。

    enable_watcher=True 时（仅 serve 模式）在 lifespan 中挂目录监听线程；
    TestClient 测试默认 False 不触发。watch_dir 为空（""/"."）时不启动。
    English: App factory; a global singleton KBService is attached to app.state.kb (shared by REST and MCP).
    Unified error JSON: {"error": "<CODE>", "message": "<human-readable>"}.
    With enable_watcher=True (serve mode only) a directory-watch thread is started in lifespan; TestClient
    defaults to False and does not trigger it. Nothing starts when watch_dir is empty (""/".")."""
    kb = KBService(settings)
    # 日志装配（N17）：serve 与 TestClient 均经此处，双 handler + 轮转
    setup_logging(kb.settings)
    # MCP 服务器与 REST 共享同一 KBService 单例；先建 streamable http 应用
    # （内部懒创建会话管理器，随后由主应用 lifespan 托管启停）
    mcp_server = create_mcp_server(kb)
    mcp_app = mcp_server.streamable_http_app(streamable_http_path="/")
    watcher: KBWatcher | None = None
    monitor: MonitorAgent | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """托管 MCP 会话管理器、目录监听线程与本地监控线程的生命周期（含启停日志）。
        English: Manage the lifecycle of the MCP session manager, directory-watch thread, and local monitor
        thread (including start/stop logs)."""
        nonlocal watcher, monitor
        serve_log = logging.getLogger("kb.serve")
        serve_log.info("服务启动 version=%s host=%s port=%s", __version__,
                       kb.settings.api_host, kb.settings.api_port)
        # 鉴权状态（N19/TASK-0062）：只记启用/未启用，不回显 key
        if (kb.settings.api_key or "").strip():
            serve_log.info("鉴权已启用（KB_API_KEY 已配置，要求 Bearer/X-API-Key）")
        else:
            serve_log.info("鉴权未启用（本地模式，KB_API_KEY 为空）")
        # N29 启动安全警告：非回环监听 + 未鉴权 → warning 日志（终端醒目横幅由 serve 命令负责）
        warn = exposure_warning(kb.settings.api_host, kb.settings.api_key)
        if warn:
            serve_log.warning(warn)
        wd = kb.settings.watch_dir
        if enable_watcher and str(wd) not in ("", "."):
            watcher = KBWatcher(kb, wd)
            watcher.start()
        # 本地监控 Agent（TASK-0017）：serve 模式默认启用，仅读配置决定间隔；
        # TestClient 测试默认不启动（monitor_enabled 默认 False，测试按需开）
        if kb.settings.monitor_enabled and str(wd) not in ("", "."):
            monitor = MonitorAgent(kb, kb.settings.monitor_interval,
                                   dispatch_enabled=kb.settings.dispatch_enabled)
            monitor.start()
            serve_log.info("监控线程启动 interval=%s分钟", kb.settings.monitor_interval)
        try:
            async with mcp_server.session_manager.run():
                serve_log.info("服务就绪 records=%s", kb.stats().get("records"))
                yield
        finally:
            if monitor is not None:
                monitor.stop()
            if watcher is not None:
                watcher.stop()
            serve_log.info("服务停止")

    app = FastAPI(title="kb memory service", lifespan=lifespan)
    app.state.kb = kb

    @app.exception_handler(HTTPException)
    def http_exc_handler(request: Request, exc: HTTPException):
        """统一错误 JSON：detail 为 dict 时直接扁平返回（不包 detail 层）。
        English: Unified error JSON: when detail is a dict, return it flattened directly (without wrapping in a detail layer)."""
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code,
                            content={"error": "HTTP_ERROR", "message": str(exc.detail)})

    @app.post("/api/v1/memories")
    def create_memory(body: MemoryCreate) -> dict:
        from kb.governance import DuplicateError
        try:
            r = kb.add_memory(body.content, tags=body.tags,
                              source=body.source, namespace=body.namespace,
                              agent_id=body.agent_id, client=body.client,
                              project=body.project)
        except DuplicateError as e:
            # N22a/TASK-0069：语义去重命中，返回 409 + 已有记录 id 与相似度
            return JSONResponse(
                status_code=409,
                content={"error": "DUPLICATE",
                         "message": "语义重复，已存在相似记录",
                         "duplicate_of": e.existing_id,
                         "similarity": round(e.similarity, 4)})
        return {"id": r.id, **r.model_dump()}

    @app.get("/api/v1/memories")
    def list_memories(type: str | None = None, tag: str | None = None,
                      source: str | None = None, q: str | None = None,
                      limit: int = 100, offset: int = 0) -> dict:
        records, total = kb.list_memories(type=type, tag=tag, source=source,
                                          q=q, limit=limit, offset=offset)
        return {"items": [r.model_dump() for r in records], "total": total}

    @app.get("/api/v1/memories/{record_id}")
    def get_memory(record_id: str, agent_id: str = "default",
                   client: str = "default",
                   project: str | None = None) -> dict:
        r = kb.get_memory(record_id, agent_id=agent_id, client=client,
                          project=project)
        if r is None:
            raise HTTPException(status_code=404,
                                detail={"error": "NOT_FOUND", "message": "记录不存在或无权访问"})
        return r.model_dump()

    @app.patch("/api/v1/memories/{record_id}")
    def update_memory(record_id: str, body: MemoryUpdate,
                      agent_id: str = "default",
                      client: str = "default",
                      project: str | None = None) -> dict:
        r = kb.update_memory(record_id, content=body.content, tags=body.tags,
                             agent_id=agent_id, client=client, project=project)
        if r is None:
            raise HTTPException(status_code=404,
                                detail={"error": "NOT_FOUND", "message": "记录不存在或无权访问"})
        return r.model_dump()

    @app.delete("/api/v1/memories/{record_id}")
    def delete_memory(record_id: str, agent_id: str = "default",
                      client: str = "default",
                      project: str | None = None) -> dict:
        if not kb.delete_memory(record_id, agent_id=agent_id, client=client,
                                project=project):
            raise HTTPException(status_code=404,
                                detail={"error": "NOT_FOUND", "message": "记录不存在或无权访问"})
        return {"ok": True}

    @app.post("/api/v1/search")
    def search(body: SearchRequest) -> dict:
        """混合检索；A 节点按 agent_id 强制隔离 memory（doc/web 共享）。
        English: Hybrid retrieval with A-node mandatory memory isolation by agent_id (doc/web shared)."""
        results = kb.search(body.query, top_k=body.top_k, mode=body.mode,
                            type=body.type, tag=body.tag,
                            agent_id=body.agent_id, client=body.client,
                            project=body.project)
        return {"results": results}

    @app.post("/api/v1/documents")
    async def add_document(request: Request) -> dict:
        """导入文档：multipart 文件上传（字段 file）或 JSON {"path": 本地路径}。

        返回 {"source": 文件名, "chunks": 块数}；
        缺参 / 文件不存在 / 格式不支持 → 400（统一错误 JSON）。
        上传文件保留扩展名落临时文件走统一解析管道，source 用上传文件名。
        English: Import a document: multipart file upload (field file) or JSON {"path": local path}.
        Returns {"source": filename, "chunks": count}; missing param / nonexistent file / unsupported format
        → 400 (unified error JSON). The uploaded file is saved to a temp file with its extension kept and goes
        through the unified parse pipeline, with source set to the uploaded file name."""
        content_type = request.headers.get("content-type", "")
        try:
            if content_type.startswith("multipart/form-data"):
                form = await request.form()
                upload = form.get("file")
                if upload is None or isinstance(upload, str):
                    raise HTTPException(status_code=400, detail={
                        "error": "BAD_REQUEST",
                        "message": "multipart 上传需提供文件字段 file"})
                name = Path(upload.filename or "upload").name
                with tempfile.NamedTemporaryFile(
                        suffix=Path(name).suffix, delete=False) as tmp:
                    tmp.write(await upload.read())
                    tmp_path = Path(tmp.name)
                try:
                    return kb.add_document(tmp_path, source=name)
                finally:
                    tmp_path.unlink(missing_ok=True)
            try:
                body = await request.json()
            except Exception:
                body = None
            path = (body or {}).get("path")
            if not path:
                raise HTTPException(status_code=400, detail={
                    "error": "BAD_REQUEST",
                    "message": "需提供 multipart file 字段或 JSON path 字段"})
            # JSON path 模式可带 agent_id/client/project（记录归属，仅审计）
            body_agent = (body or {}).get("agent_id", "default")
            body_client = (body or {}).get("client", "default")
            body_project = (body or {}).get("project")
            if not Path(path).is_file():
                raise HTTPException(status_code=400, detail={
                    "error": "FILE_NOT_FOUND",
                    "message": f"文件不存在：{path}"})
            return kb.add_document(path, agent_id=body_agent, client=body_client,
                                   project=body_project)
        except UnsupportedFormatError as exc:
            raise HTTPException(status_code=400, detail={
                "error": "UNSUPPORTED_FORMAT", "message": str(exc)})

    @app.get("/api/v1/documents")
    def list_documents() -> dict:
        """按 source 聚合的文档列表。
        English: Document list aggregated by source."""
        return {"items": kb.list_documents()}

    @app.delete("/api/v1/documents/{source}")
    def delete_document(source: str) -> dict:
        """按 source 删除文档全部记录。
        English: Delete all records of a document by source."""
        return {"deleted": kb.delete_document(source)}

    @app.post("/api/v1/ingest/web")
    def ingest_web(body: WebIngestRequest) -> dict:
        """抓取网页正文切分入库；抓取/正文提取失败返回 400 与原因。
        English: Fetch a webpage body, split and ingest it; returns 400 with the reason on fetch/body-extraction failure."""
        try:
            return kb.add_webpage(body.url, agent_id="default",
                                 client="default")
        except WebFetchError as exc:
            raise HTTPException(status_code=400, detail={
                "error": "WEB_FETCH_FAILED", "message": str(exc)})

    @app.post("/api/v1/ask")
    def ask(body: AskRequest) -> dict:
        """基础 RAG 问答；LLM 禁用时返回 503 与配置指引。
        English: Basic RAG Q&A; returns 503 with setup guidance when the LLM is disabled."""
        try:
            return kb.ask(body.question, agent_id=body.agent_id,
                          client=body.client, project=body.project)
        except LLMDisabledError:
            raise HTTPException(status_code=503, detail={
                "error": "LLM_DISABLED",
                "message": "未检测到可用的 LLM：请在 .env 配置 KB_LLM_MODE=local "
                           "（Ollama）或 KB_LLM_API_KEY 启用云端（任意 OpenAI 兼容服务商）"})

    @app.get("/api/v1/audit")
    def audit(agent: str, action: str | None = None,
              days: int | None = None, limit: int = 100) -> dict:
        """用户查询 Agent 存取审计（A 节点 spec 2.5）：读 access-audit.log，
        按 agent 过滤，可选 action/days/limit。返回 {"items": [...], "total": N}。
        English: Query the Agent access audit (A-node spec 2.5): reads access-audit.log, filters by
        agent, optionally by action/days/limit. Returns {"items": [...], "total": N}."""
        if not agent or not agent.strip():
            raise HTTPException(status_code=422, detail={
                "error": "INVALID_ARGUMENT", "message": "agent 参数必填"})
        items = kb.query_access_audit(
            agent=agent, action=action, days=days, limit=limit)
        return {"items": items, "total": len(items)}

    @app.get("/api/v1/healthz")
    def healthz() -> dict:
        return {"status": "ok", **kb.stats()}

    @app.post("/api/v1/monitor/summary")
    def post_monitor_summary() -> dict:
        """按需生成监控摘要（TASK-0021 去常驻 / TASK-0059 降级 / TASK-0065 纯文本模式）：单轮（快照→摘要→写comm:monitor）。

        monitor_llm="off"（默认）时全程纯文本不调 LLM（零成本/不抢GPU）；"auto" 时
        LLM 可用摘要化、不可用降级纯文本仍返回 200；仅 build_snapshot/add_memory
        失败返回 None → 502 MONITOR_UNAVAILABLE。
        不依赖常驻线程，前端按钮/定时器按需调用。
        English: On-demand monitor summary (TASK-0021 no-resident / TASK-0059 degrade / TASK-0065 plain-text
        mode): one round (snapshot → summary → write comm:monitor). With monitor_llm="off" (default) it is
        plain-text throughout without calling the LLM (zero cost, no GPU contention); with "auto", the LLM
        summarizes when available and degrades to plain text otherwise, still returning 200; only
        build_snapshot/add_memory failure returns None → 502 MONITOR_UNAVAILABLE. It does not depend on a
        resident thread; the frontend button/timer calls on demand."""
        record = run_once_summary(kb, max_tokens=kb.settings.monitor_max_tokens,
                                   dispatch_enabled=kb.settings.dispatch_enabled,
                                   monitor_llm=kb.settings.monitor_llm)
        if record is None:
            raise HTTPException(status_code=502, detail={
                "error": "MONITOR_UNAVAILABLE",
                "message": "摘要生成失败：本地 LLM 不可用或摘要为空"})
        return {"summary": record.content, "id": record.id}

    @app.get("/api/v1/config")
    def get_config() -> dict:
        """前端只读配置（TASK-0021）：仅暴露看板需要的最小字段（monitor_autotimer）。
        English: Frontend read-only config (TASK-0021): exposes only the minimal fields the dashboard needs (monitor_autotimer)."""
        return {"monitor_autotimer": kb.settings.monitor_autotimer}

    @app.get("/api/v1/governance/stats")
    def get_governance_stats() -> dict:
        """治理统计（TASK-0070，A3 spec §4.2）：只读无副作用。

        返回总记录数、平均 access_count、超 90 天未命中数；access_count/last_accessed
        用 getattr 兼容 TASK-0067 未合入（未合入时均为 0/""，统计值为 0）。
        English: Governance stats (TASK-0070, A3 spec §4.2): read-only, no side effects.
        Returns total record count, average access_count, and the count of records not hit for over 90 days;
        access_count/last_accessed use getattr to stay compatible with pre-merge TASK-0067 (all 0/"" and stats
        of 0 when not merged)."""
        from kb.governance import compute_stats
        return compute_stats(kb.store.iter_all())

    @app.get("/api/v1/governance/config")
    def get_governance_config() -> dict:
        """治理配置（TASK-0070）：GET 当前衰减+新鲜度配置，只读。
        English: Governance config (TASK-0070): GET the current decay + freshness config, read-only."""
        s = kb.settings
        return {
            "decay_enabled": getattr(s, "decay_enabled", False),
            "decay_lambda": getattr(s, "decay_lambda", 0.02),
            "decay_gamma": getattr(s, "decay_gamma", 0.3),
            "freshness_enabled": getattr(s, "freshness_enabled", False),
            "freshness_beta": getattr(s, "freshness_beta", 0.05),
            "freshness_alpha": getattr(s, "freshness_alpha", 0.3),
        }

    @app.get("/")
    def root() -> HTMLResponse:
        """根路径极简导航页（N19）：纯字符串 HTML，无模板依赖；样式与看板一致。
        English: Minimal root navigation page (N19): plain-string HTML, no template dependency; styling matches the dashboard."""
        return HTMLResponse(_NAV_HTML)

    @app.get("/api/v1/logs")
    def get_logs(limit: int = Query(100, ge=1, le=LOG_LIMIT_MAX),
                 level: str | None = Query(None),
                 event: str | None = Query(None)) -> dict:
        """日志查看（N18）：尾部向前扫描，按 level/event 过滤凑满 limit 条，按文件序返回。

        limit 越界 / level 非法 → 422；文件不可读 → 500 LOG_READ_ERROR；
        文件不存在或为空 → items=[]（不视为错误）。
        English: Log viewing (N18): scan backward from the tail, filter by level/event to fill limit items, return in
        file order. Out-of-range limit / illegal level → 422; unreadable file → 500 LOG_READ_ERROR; a nonexistent
        or empty file → items=[] (not an error)."""
        lvl = _normalize_level(level)
        try:
            lines, truncated, start_line = _read_log_tail(kb.settings.log_file)
        except OSError as exc:
            raise HTTPException(status_code=500, detail={
                "error": "LOG_READ_ERROR", "message": str(exc)})
        items = []
        for back_idx, line in enumerate(reversed(lines)):
            parsed = _parse_log_line(line)
            if parsed is None:
                continue
            if lvl and parsed["level"].upper() != lvl:
                continue
            if event and event.lower() not in parsed["message"].lower():
                continue
            parsed["line"] = start_line + (len(lines) - 1 - back_idx)
            items.append(parsed)
            if len(items) >= limit:
                break
        items.reverse()
        return {"items": items, "total": len(items), "truncated": truncated}

    @app.get("/api/v1/logs/events")
    def get_log_events(window: int = Query(1000, ge=1, le=EVENT_WINDOW_MAX),
                       level: str | None = Query(None)) -> dict:
        """日志事件统计（N18）：按 level 与 logger 两维度统计最近 window 行的行数。

        当前文本行格式无结构化 event 字段，以 logger/level 代理统计（见设计书）。
        English: Log event statistics (N18): count the lines of the most recent `window` lines by level and logger.
        The current text-line format has no structured event field, so logger/level proxy the stats (see design
        doc)."""
        lvl = _normalize_level(level)
        try:
            recent, _, _ = _read_log_tail(kb.settings.log_file, scan_max=window)
        except OSError as exc:
            raise HTTPException(status_code=500, detail={
                "error": "LOG_READ_ERROR", "message": str(exc)})
        by_level: dict[str, int] = {}
        by_logger: dict[str, int] = {}
        for line in recent:
            parsed = _parse_log_line(line)
            if parsed is None:
                continue
            lv = parsed["level"].upper()
            if lvl and lv != lvl:
                continue
            by_level[lv] = by_level.get(lv, 0) + 1
            by_logger[parsed["logger"]] = by_logger.get(parsed["logger"], 0) + 1
        return {"window": window, "total_lines": len(recent),
                "by_level": by_level, "by_logger": by_logger}

    # MCP streamable http 端点挂载在 /mcp（子路径 "/"，即完整路径 /mcp/）；
    # 外包 SSE charset 中间件，保证 text/event-stream 响应头带 charset=utf-8
    app.mount("/mcp", _wrap_sse_charset(mcp_app))
    # HTML 看板静态挂载（TASK-0014 集成项）：kb 同源提供 /dashboard，浏览器同源策略免 CORS；
    # 目录不存在时静默跳过（orchestra/dashboard 仅本仓库存在，不影响 kb 独立部署）
    dashboard_dir = Path(__file__).resolve().parent.parent / "orchestra" / "dashboard"
    if dashboard_dir.is_dir():
        from starlette.staticfiles import StaticFiles
        app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True),
                  name="dashboard")
    # 鉴权中间件（N19/TASK-0062）：纯 ASGI，空 key 直通，非空校验 Bearer/X-API-Key；
    # 注册在 JSON charset 内层（401 响应向外经 JSON charset 补 charset）、访问日志内层
    app = _wrap_api_key(app, kb.settings)
    # 兜底：整体再包 JSON charset 中间件，保证所有 application/json
    # 响应头带 charset=utf-8（含 422 校验错误与错误 JSON）
    app = _wrap_json_charset(app)
    # 请求访问日志中间件（N17/TASK-0012）：ASGI 栈最外层，REST 与 MCP 统一覆盖
    app = _wrap_request_log(app)

    return app