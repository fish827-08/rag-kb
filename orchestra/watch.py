"""终端看板模块（TASK-0031 包化⑥：自 board.py 机械搬移）。

含 watch 相关：
- _watch_frame：渲染看板一轮文本（worker 段 + 任务卡段 + 可选交流窗段）
- cmd_watch：前台轮询重绘，支持 --once 单轮与 Ctrl+C 干净退出

board.py 仅做 CLI 调度，通过 import 复用本模块；
依赖方向：board.py → 本模块 → cards/registry/comm → client.py。
"""
import json
import time

from cards import TAG, _fmt_time, parse_header
from client import _request
from comm import _comm_tag, _truncate
from registry import REGISTRY_TAG


def _watch_frame(include_comm: bool = False) -> str:
    """渲染看板一轮文本：worker 段 + 任务卡段 +（可选）交流窗段。

    纯函数便于单测；行格式分别复用 cmd_workers / cmd_status / cmd_list_comm。
    """
    lines = []
    # worker 段（名字 模型 状态 最后活跃）
    cards = _request("GET", f"/memories?tag={REGISTRY_TAG}&limit=1000") \
        .get("items", [])
    if not cards:
        lines.append("无已注册 worker")
    else:
        for card in cards:
            try:
                data = json.loads(card["content"])
            except (ValueError, TypeError):
                continue
            lines.append(f"{data.get('worker', '?')} {data.get('model', '?')} "
                         f"{data.get('status', '?')} "
                         f"{data.get('last_seen', '?')}")
    # 任务卡段（TASK 状态 assignee HH:MM 标题）
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    for card in cards:
        try:
            h = parse_header(card["content"])
        except ValueError:
            continue
        lines.append(f"{h['task_id']} {h['status']} {h['assignee']} "
                     f"{_fmt_time(card.get('updated_at', ''))} {h['title']}")
    # 交流窗段（--comm 时附最近 5 条）
    if include_comm:
        comms = _request("GET", "/memories?limit=1000").get("items", [])
        comms = [c for c in comms if _comm_tag(c.get("tags")) is not None]
        comms.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        if comms:
            lines.append("-- 交流窗 --")
            for card in comms[:5]:
                tag = _comm_tag(card.get("tags")) or "comm:?"
                lines.append(f"{_fmt_time(card.get('updated_at', ''))} | {tag} "
                             f"| {card.get('source') or '?'} | "
                             f"{_truncate(card.get('content', ''))}")
    return "\n".join(lines)


def cmd_watch(interval: int = 5, comm: bool = False, once: bool = False) -> None:
    """终端看板：前台轮询重绘 worker 行 + 卡行（+--comm 交流窗）。

    interval 须 ≥1（秒）；--once 单轮模式便于测试/脚本；
    Ctrl+C 捕获后打印"watch 已退出"并干净返回（退出码 0）。
    """
    if interval < 1:
        raise ValueError(f"interval 须 ≥1，收到 {interval}")
    try:
        while True:
            print(_watch_frame(include_comm=comm))
            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        print("watch 已退出")
