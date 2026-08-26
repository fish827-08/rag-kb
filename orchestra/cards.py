"""orchestra 卡片纯函数模块（TASK-0029 包化②：从 board.py 机械拆出）。

只含不依赖 HTTP 的卡片格式化 / 解析 / 校验 / 渲染函数与常量：
- LIMITS / STATUSES：字段上限与状态机合法值
- render_card：渲染完整卡片文本
- parse_header：解析卡片首行
- check_limits：字段长度校验
- _fmt_time：ISO 时间 → HH:MM
- _next_task_id：现有卡最大编号 +1

board.py 仍负责 HTTP 请求（_request）与子命令（cmd_*），通过 import 复用本模块。
"""
import re
from datetime import datetime

# 各字段字符上限（设计文档第 4 节）
LIMITS = {"title": 30, "goal": 300, "input": 300,
          "constraints": 200, "acceptance": 200, "result": 1000}
# 状态机合法值
STATUSES = ("pending", "claimed", "done", "failed", "verified")

_HEADER_RE = re.compile(r"^(TASK-\d{4}) (\w+) (\S+) \| (.+)$")


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


def _fmt_time(updated_at: str) -> str:
    """ISO 时间 → HH:MM；解析失败返回 '???'。"""
    try:
        return datetime.fromisoformat(updated_at).strftime("%H:%M")
    except (ValueError, TypeError):
        return "???"


def _next_task_id(cards: list[dict]) -> str:
    """现有卡最大编号 +1，四位数零填充。"""
    max_num = 0
    for card in cards:
        try:
            h = parse_header(card["content"])
            num = int(h["task_id"].split("-")[1])
            max_num = max(max_num, num)
        except (ValueError, IndexError):
            continue  # 非法卡不参与编号
    return f"TASK-{max_num + 1:04d}"
