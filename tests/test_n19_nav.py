"""根路径极简导航页（N19，TASK-0019）。

GET / 返回极简 HTML 导航（纯字符串，无模板依赖）：三行链接
（/api/v1/healthz、/dashboard/、/docs）+ 顶部标题 'kb 记忆服务'；
样式内联、与看板风格一致（深色渐变头部 + 白卡片）。
"""
import pytest

pytestmark = pytest.mark.integration


def _get_root(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        return c.get("/")


def test_根路径返回200(env_isolated):
    r = _get_root(env_isolated)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_根路径含三个链接(env_isolated):
    html = _get_root(env_isolated).text
    assert "/api/v1/healthz" in html
    assert "/dashboard/" in html
    assert "/docs" in html


def test_根路径含标题(env_isolated):
    html = _get_root(env_isolated).text
    assert "kb 记忆服务" in html
