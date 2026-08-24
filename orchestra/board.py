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


def render_card(task_id: str, status: str, assignee: str, title: str,
                goal: str, input_: str, constraints: str,
                acceptance: str, result: str = "", note: str = "") -> str:
    """渲染完整卡片文本；首行为可检索状态行。"""
    lines = [
        f"{task_id} {status} {assignee} | {title}",
        f"目标：{goal}",
        f"输入：{input_}",
        f"约束：{constraints}",
        f"验收：{acceptance}",
        f"结果：{result}",
    ]
    if note:
        lines.append(f"备注：{note}")
    return "\n".join(lines)


_HEADER_RE = re.compile(r"^(TASK-\d{4}) (\w+) (\S+) \| (.+)$")


def parse_header(content: str) -> dict:
    """解析卡片首行 → {task_id, status, assignee, title}；非法格式抛 ValueError。"""
    header = content.split("\n", 1)[0].strip()
    m = _HEADER_RE.match(header)
    if not m:
        raise ValueError(f"卡片首行格式非法：{header!r}")
    return {"task_id": m.group(1), "status": m.group(2),
            "assignee": m.group(3), "title": m.group(4)}


def check_limits(**fields: str) -> None:
    """字段长度校验；超限抛 ValueError（中文提示字段名与上限）。"""
    for name, value in fields.items():
        if value and len(value) > LIMITS[name]:
            raise ValueError(
                f"字段 {name} 超长：{len(value)} 字符 > 上限 {LIMITS[name]}")
