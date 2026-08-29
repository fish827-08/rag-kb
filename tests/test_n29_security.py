"""N29 启动安全警告单测（评估报告综合建议：非回环监听 + 未鉴权 → 醒目警告）。

覆盖两层：
1. 纯函数 exposure_warning(host, api_key)：回环判定 + 空 key 判定，返回警告文本或 None；
2. lifespan 集成：TestClient 启动时 0.0.0.0/无 key 场景记录 warning 日志，默认回环无警告。
"""
import logging

import pytest

pytestmark = pytest.mark.integration


# ---------- 纯函数 exposure_warning ----------

def test_非回环_无key_返回警告():
    """0.0.0.0 + 空 key → 警告文本包含 KB_API_KEY 指引。"""
    from kb.api import exposure_warning
    warn = exposure_warning("0.0.0.0", "")
    assert warn is not None
    assert "KB_API_KEY" in warn


def test_内网地址_无key_返回警告():
    """192.168.1.5 + 空 key → 同样警告（内网暴露也属非回环）。"""
    from kb.api import exposure_warning
    assert exposure_warning("192.168.1.5", "") is not None


def test_非回环_空白key_视为无key():
    """key 全空白（"  "）等同未设置 → 仍警告。"""
    from kb.api import exposure_warning
    assert exposure_warning("0.0.0.0", "   ") is not None


def test_非回环_有key_不警告():
    """0.0.0.0 + 有效 key → None（已启用鉴权，无暴露风险）。"""
    from kb.api import exposure_warning
    assert exposure_warning("0.0.0.0", "secret123") is None


def test_回环地址_无key_不警告():
    """127.0.0.1 / localhost / ::1 / 127.x 段 / 空串 → None（本地回环零摩擦设计）。"""
    from kb.api import exposure_warning
    for host in ("127.0.0.1", "localhost", "::1", "127.0.0.5", ""):
        assert exposure_warning(host, "") is None, f"回环地址 {host!r} 不应警告"


# ---------- lifespan 集成（warning 日志） ----------

def test_启动时_非回环无key_记录warning日志(env_isolated, caplog):
    """TestClient 启动（触发 lifespan）时 0.0.0.0 + 空 key → kb.serve 记 warning。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.config import Settings
    with caplog.at_level(logging.WARNING, logger="kb.serve"):
        with TestClient(create_app(settings=Settings(api_host="0.0.0.0", api_key=""))):
            assert any("KB_API_KEY" in r.message and r.levelno == logging.WARNING
                       for r in caplog.records)


def test_启动时_默认回环_无warning(env_isolated, caplog):
    """默认 127.0.0.1 + 空 key → kb.serve 不产生 warning（本地模式安静启动）。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.config import Settings
    with caplog.at_level(logging.WARNING, logger="kb.serve"):
        with TestClient(create_app(settings=Settings(api_host="127.0.0.1", api_key=""))):
            assert not any(r.levelno == logging.WARNING for r in caplog.records)
