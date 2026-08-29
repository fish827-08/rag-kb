"""V2-0 缺陷修复验收：watcher 监听目录缺失时 serve 容错。

背景：data/ 目录被归档删除后 KB_WATCH_DIR 指向不存在路径，
serve 启动时 watchdog 在 get_directory_handle 抛 FileNotFoundError
直接崩溃退出（worker-1 在 TASK-0003 实测踩中）。

期望：目录缺失时记 warning 日志并跳过目录监听，服务其余功能正常。
"""
import logging

import pytest

pytestmark = pytest.mark.integration


def _set_watch_dir(monkeypatch, path):
    """设置 KB_WATCH_DIR 并清配置缓存，确保新值被 create_app 读到。"""
    monkeypatch.setenv("KB_WATCH_DIR", str(path))
    from kb import config
    config.get_settings.cache_clear()


def test_监听目录缺失时服务正常启动(env_isolated, monkeypatch):
    """KB_WATCH_DIR 指向不存在路径：serve 应启动成功且 healthz 200。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    _set_watch_dir(monkeypatch, env_isolated / "no_such_dir")
    with TestClient(create_app(enable_watcher=True)) as c:
        r = c.get("/api/v1/healthz")
        assert r.status_code == 200, f"healthz 应 200，实际 {r.status_code}"


def test_监听目录缺失时记警告日志(env_isolated, caplog):
    """目录缺失时 watcher.start() 记 warning 且不抛异常；stop() 安全。"""
    from kb.service import KBService
    from kb.watcher import KBWatcher
    watch = env_isolated / "no_such_dir"
    s = KBService()
    w = KBWatcher(s, watch)
    with caplog.at_level(logging.WARNING, logger="kb.watcher"):
        w.start()  # 修复前此处抛 FileNotFoundError
    assert "监听目录不存在" in caplog.text, \
        f"应记警告日志，实际捕获：{caplog.text!r}"
    w.stop()  # 跳过监听后 stop 应安全无事（无线程/句柄待清理）


def test_监听路径是文件而非目录时同样跳过(env_isolated, caplog):
    """watch_dir 指向普通文件：视为不可监听，同样警告跳过不崩溃。"""
    from kb.service import KBService
    from kb.watcher import KBWatcher
    not_dir = env_isolated / "plain.txt"
    not_dir.write_text("不是目录", encoding="utf-8")
    s = KBService()
    w = KBWatcher(s, not_dir)
    with caplog.at_level(logging.WARNING, logger="kb.watcher"):
        w.start()
    assert "监听目录不存在" in caplog.text
    w.stop()