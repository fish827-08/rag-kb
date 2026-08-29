"""N18 验收：日志查看端点（GET /api/v1/logs 与 /api/v1/logs/events）。

对应设计书 orchestra/docs/superpowers/specs/2026-08-25-kb-n18-log-api-design.md：
- 第 1 节端点契约（limit/level/event 参数、响应 schema、错误码）
- 第 2 节 _parse_log_line 纯函数与读尾部语义（SCAN_MAX=20000）
- 第 3 节测试清单
"""
import pytest

pytestmark = pytest.mark.integration

# 样例日志行（与 logging_setup 输出格式一致：asctime | level | name | message）
L1 = "2026-08-25 21:50:22,978 | INFO | kb.serve | 服务启动 version=1.0.2"
L2 = "2026-08-25 21:50:23,100 | WARNING | kb.watcher | 目录缺失，跳过监听"
L3 = ("2026-08-25 21:50:24,200 | ERROR | kb.api | "
      "request.end method=GET path=/api/v1/healthz status=500 耗时=1.0ms")


def _write_log(env_isolated, lines, name="kb.log"):
    """向隔离环境的日志文件写入样例行（追加换行）。"""
    p = env_isolated / "logs" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _make_client(env_isolated, monkeypatch, level="WARNING"):
    """装配 app 并压掉应用自身 INFO 行（默认 WARNING），使日志文件仅含测试样例。

    同时禁用监控线程（KB_MONITOR_ENABLED=false）：TASK-0017 的 MonitorAgent
    在 serve 启动时写 WARNING 行，会混入本端点读取的日志文件，影响精确断言；
    测试对后台常驻线程一律隔离。注意：RotatingFileHandler 以追加模式打开文件
    （文件会被创建），但 WARNING 级别下生命周期/请求中间件均为 INFO 不写出，
    保证文件内容 = 测试写入的样例行。
    """
    monkeypatch.setenv("KB_LOG_LEVEL", level)
    monkeypatch.setenv("KB_MONITOR_ENABLED", "false")
    from kb import config
    config.get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from kb.api import create_app
    return TestClient(create_app())


# ---- _parse_log_line 纯函数 ----

def test_parse_log_line_正常行():
    from kb.api import _parse_log_line
    r = _parse_log_line("2026-08-25 21:50:22,978 | INFO | kb.serve | 服务启动 version=1.0.2")
    assert r == {"time": "2026-08-25 21:50:22,978", "level": "INFO",
                 "logger": "kb.serve", "message": "服务启动 version=1.0.2"}


def test_parse_log_line_message含竖线():
    from kb.api import _parse_log_line
    line = "2026-08-25 21:50:22,978 | INFO | kb.api | request.end method=GET status=200 | 耗时=1ms"
    r = _parse_log_line(line)
    assert r is not None
    assert r["message"] == "request.end method=GET status=200 | 耗时=1ms"
    assert r["logger"] == "kb.api"


def test_parse_log_line_非法行返回None():
    from kb.api import _parse_log_line
    assert _parse_log_line("") is None
    assert _parse_log_line("   ") is None
    assert _parse_log_line("只有一段") is None
    assert _parse_log_line("a | b | c") is None       # 字段不足


# ---- GET /api/v1/logs ----

def test_logs_返回尾部N行(env_isolated, monkeypatch):
    """临时日志 3 行，?limit=2 → 返回末尾 2 行、按文件序、line 为文件行号。"""
    _write_log(env_isolated, [L1, L2, L3])
    with _make_client(env_isolated, monkeypatch) as c:
        r = c.get("/api/v1/logs?limit=2")
    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 2
    assert data["total"] == 2
    assert data["truncated"] is False
    items = data["items"]
    assert items[0]["message"] == "目录缺失，跳过监听"      # L2
    assert items[1]["message"].startswith("request.end")    # L3
    assert items[0]["line"] == 2 and items[1]["line"] == 3  # 文件行号递增
    assert items[0]["time"] == "2026-08-25 21:50:23,100"
    assert items[0]["level"] == "WARNING"
    assert items[0]["logger"] == "kb.watcher"


def test_logs_limit默认与上限(env_isolated, monkeypatch):
    """缺省 limit=100；limit=0 / limit=1001 → 422。"""
    lines = [f"2026-08-25 21:50:22,{i:03d} | INFO | kb.serve | 行{i}" for i in range(101)]
    _write_log(env_isolated, lines)
    with _make_client(env_isolated, monkeypatch) as c:
        r = c.get("/api/v1/logs")                 # 缺省 limit
        assert r.status_code == 200
        assert r.json()["total"] == 100           # 默认上限 100
        assert c.get("/api/v1/logs?limit=0").status_code == 422
        assert c.get("/api/v1/logs?limit=1001").status_code == 422


def test_logs_level过滤(env_isolated, monkeypatch):
    """?level=WARNING 只含 WARNING；?level=warn 大小写不敏感同效。"""
    _write_log(env_isolated, [L1, L2, L3])
    with _make_client(env_isolated, monkeypatch) as c:
        r1 = c.get("/api/v1/logs?level=WARNING")
        r2 = c.get("/api/v1/logs?level=warn")
    assert r1.status_code == 200
    for r in (r1, r2):
        data = r.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["level"] == "WARNING"
        assert data["items"][0]["message"] == "目录缺失，跳过监听"


def test_logs_level非法422(env_isolated, monkeypatch):
    """?level=FOO → 422。"""
    _write_log(env_isolated, [L1])
    with _make_client(env_isolated, monkeypatch) as c:
        assert c.get("/api/v1/logs?level=FOO").status_code == 422


def test_logs_event子串过滤(env_isolated, monkeypatch):
    """?event=命中 只含 message 含"命中"的行；大小写不敏感。"""
    lines = [
        "2026-08-25 21:50:22,978 | INFO | kb.retriever | 检索 query=test 命中=5",
        "2026-08-25 21:50:23,100 | INFO | kb.serve | 服务启动",
        "2026-08-25 21:50:24,200 | INFO | kb.retriever | HIT count=3",
    ]
    _write_log(env_isolated, lines)
    with _make_client(env_isolated, monkeypatch) as c:
        r1 = c.get("/api/v1/logs?event=命中")
        r2 = c.get("/api/v1/logs?event=hit")
    assert r1.status_code == 200
    assert [i["message"] for i in r1.json()["items"]] == ["检索 query=test 命中=5"]
    assert [i["message"] for i in r2.json()["items"]] == ["HIT count=3"]


def test_logs_文件不存在返回空(env_isolated, monkeypatch):
    """日志文件不存在或为空时返回空列表（不视为错误，200）。"""
    from kb.api import _read_log_tail
    # 文件不存在 → 空（直接测读取函数，避免 Windows 打开句柄删除限制）
    assert _read_log_tail(env_isolated / "logs" / "no-such.log") == ([], False, 1)
    # 文件存在但为空（CRITICAL 无任何写出）→ 端点返回空列表
    with _make_client(env_isolated, monkeypatch, level="CRITICAL") as c:
        r = c.get("/api/v1/logs")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "truncated": False}


# ---- GET /api/v1/logs/events ----

def test_logs_events_统计(env_isolated, monkeypatch):
    """events 按 level 与 logger 统计最近 window 行的行数。"""
    _write_log(env_isolated, [L1, L2, L3])
    with _make_client(env_isolated, monkeypatch) as c:
        r = c.get("/api/v1/logs/events?window=100")
    assert r.status_code == 200
    data = r.json()
    assert data["window"] == 100
    assert data["total_lines"] == 3
    assert data["by_level"] == {"INFO": 1, "WARNING": 1, "ERROR": 1}
    assert data["by_logger"] == {"kb.serve": 1, "kb.watcher": 1, "kb.api": 1}


def test_logs_events_level过滤与window边界(env_isolated, monkeypatch):
    """events 支持 level 过滤；window=0 / window=10001 → 422。"""
    _write_log(env_isolated, [L1, L2, L3])
    with _make_client(env_isolated, monkeypatch) as c:
        r = c.get("/api/v1/logs/events?window=100&level=WARNING")
        assert r.status_code == 200
        assert r.json()["by_level"] == {"WARNING": 1}
        assert c.get("/api/v1/logs/events?window=0").status_code == 422
        assert c.get("/api/v1/logs/events?window=10001").status_code == 422
        assert c.get("/api/v1/logs/events?level=FOO").status_code == 422
