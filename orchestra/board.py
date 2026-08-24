#!/usr/bin/env python3
"""agent-orchestra 任务板 CLI：管理 kb 服务上的 taskboard 任务卡。

协调者专用；worker 走 MCP（orchestra-worker skill）。
仅标准库；kb REST 契约见 rag-kb kb/api.py。

用法：
    board.py add --assignee w1 --title T --goal G --input I --constraints C --acceptance A
    board.py status
    board.py show TASK-0003
    board.py verify TASK-0003 --pass | --reject [--note 原因]
    board.py new-worker NAME
"""
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

KB_BASE = "http://127.0.0.1:8000/api/v1"
TAG = "taskboard"
# 各字段字符上限（设计文档第 4 节）
LIMITS = {"title": 30, "goal": 300, "input": 300,
          "constraints": 200, "acceptance": 200, "result": 1000}
# 状态机合法值
STATUSES = ("pending", "claimed", "done", "failed", "verified")


class BoardUnavailable(Exception):
    """kb 服务不可达或服务端错误。"""


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """kb REST 请求封装。

    连接失败/5xx → BoardUnavailable（退出码 2）；
    4xx → RuntimeError（退出码 1，提示调用方参数或状态问题）。
    """
    url = f"{KB_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise BoardUnavailable(f"kb 服务错误 HTTP {e.code}") from e
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"kb 拒绝请求 HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BoardUnavailable(f"kb 服务不可达：{e}") from e
