"""N17 验收：日志配置项 + logging_setup 装配 + 生命周期日志 + 文件轮转。

对应日志设计文档 docs/superpowers/specs/2026-08-24-logging-design.md：
- 第 3 节：KB_LOG_LEVEL / KB_LOG_DIR / KB_LOG_MAX_BYTES / KB_LOG_BACKUP_COUNT
- 第 4.1 节：生命周期日志（logger kb.serve：服务启动/就绪/停止）
- 第 6 节：setup_logging 双 handler（控制台精简 + RotatingFileHandler 完整）
- 第 7 节验收测试 1 / 4 / 6（文件创建与基础写入 / 轮转 / 级别过滤）
"""
import logging
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _log_text(env_isolated) -> str:
    """读取当前隔离环境（KB_LOG_DIR=tmp/logs）的日志文件全文。"""
    return (env_isolated / "logs" / "kb.log").read_text(encoding="utf-8")


def test_配置默认值(env_isolated, monkeypatch):
    """第 3 节默认值：INFO / logs / 1MB / 5 备份。"""
    from kb import config
    monkeypatch.delenv("KB_LOG_DIR", raising=False)  # 摘掉 fixture 注入，验原生默认
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.log_level == "INFO"
    assert s.log_dir == Path("logs")
    assert s.log_max_bytes == 1048576
    assert s.log_backup_count == 5
    assert s.log_file == Path("logs") / "kb.log"


def test_日志文件创建与生命周期事件(env_isolated):
    """验收 1：应用启动后 kb.log 存在，含服务启动/就绪/停止与规定格式。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.get("/api/v1/healthz")
        assert r.status_code == 200
    text = _log_text(env_isolated)
    assert "服务启动" in text
    assert "服务就绪" in text
    assert "服务停止" in text
    # 完整格式（第 6 节）：时间 | 级别 | 模块 | 消息
    assert re.search(r"\| INFO \| kb\.serve \| 服务启动 version=", text), \
        f"生命周期日志格式不符：{text!r}"


def test_日志轮转产生备份文件(env_isolated, monkeypatch):
    """验收 4：KB_LOG_MAX_BYTES=1024，超限写入后产生 kb.log.1。"""
    monkeypatch.setenv("KB_LOG_MAX_BYTES", "1024")
    from kb import config
    config.get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()):
        log = logging.getLogger("kb.test")  # kb 子 logger，继承装配
        for i in range(30):                 # 每条 ~120B，30 条必超 1KB
            log.info("轮转压力记录 %02d %s", i, "x" * 60)
    assert (env_isolated / "logs" / "kb.log.1").exists(), "应产生 .1 备份文件"
    assert (env_isolated / "logs" / "kb.log").exists()


def test_级别过滤WARNING时INFO不落盘(env_isolated, monkeypatch):
    """验收 6：KB_LOG_LEVEL=WARNING 时 INFO 记录不落盘，WARNING 落盘。"""
    monkeypatch.setenv("KB_LOG_LEVEL", "WARNING")
    from kb import config
    config.get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()):
        log = logging.getLogger("kb.test")
        log.info("这条INFO不应落盘")
        log.warning("这条WARNING应当落盘")
    text = _log_text(env_isolated)
    assert "这条WARNING应当落盘" in text
    assert "这条INFO不应落盘" not in text