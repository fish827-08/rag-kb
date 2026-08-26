#!/usr/bin/env python3
"""agent-orchestra 任务板 CLI 入口：argparse 子命令调度。

协调者专用；worker 走 MCP（orchestra-worker skill）。
各子命令实现按模块分层（protocol.md §10）：
client（HTTP）/ cards（任务卡 CRUD+纯函数）/ registry（注册）/
comm（交流窗）/ worktree（隔离）/ watch（看板）。
用法：python orchestra\\board.py <子命令>（--help 看各子命令参数）。
"""
import argparse
import sys

from client import BoardUnavailable
from cards import (cmd_add, cmd_claim, cmd_list_pending, cmd_new_worker,
                   cmd_show, cmd_status, cmd_verify)
from comm import COMM_CHANNELS, cmd_list_comm, cmd_report
from registry import cmd_register, cmd_workers
from watch import cmd_watch
from worktree import (cmd_worktree_clean, cmd_worktree_enter,
                      cmd_worktree_setup)


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 解析器（12 个子命令，参数与拆分前完全一致）。"""
    parser = argparse.ArgumentParser(description="agent-orchestra 任务板")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="创建任务卡")
    for name in ("assignee", "title", "goal", "input",
                 "constraints", "acceptance"):
        p_add.add_argument(f"--{name}", required=True)
    p_add.add_argument("--docs", default="",
                       help="文档同步清单，如 USER_GUIDE.md(端点速查节)")

    for name, help_ in (("status", "一行一卡看板"),
                        ("list-pending", "只列待办卡"),
                        ("workers", "一行一 worker 列表")):
        sub.add_parser(name, help=help_)

    p_claim = sub.add_parser("claim", help="认领卡片（pending→claimed）")
    p_claim.add_argument("task_id")
    p_claim.add_argument("--assignee", required=True,
                          help="认领者 worker 名字")

    p_show = sub.add_parser("show", help="打印整卡")
    p_show.add_argument("task_id")

    p_verify = sub.add_parser("verify", help="核验流转")
    p_verify.add_argument("task_id")
    group = p_verify.add_mutually_exclusive_group(required=True)
    group.add_argument("--pass", dest="action", action="store_const",
                       const="pass")
    group.add_argument("--reject", dest="action", action="store_const",
                       const="reject")
    p_verify.add_argument("--note", default="")
    p_verify.add_argument("--docs-done", dest="docs_done",
                          action="store_true",
                          help="确认文档同步清单已完成（有清单时核验前置）")

    p_new = sub.add_parser("new-worker", help="打印 worker 引导语")
    p_new.add_argument("name")

    p_register = sub.add_parser("register", help="注册/刷新 worker 身份")
    p_register.add_argument("name")
    p_register.add_argument("--model", required=True, help="模型名")
    p_register.add_argument("--client", required=True, help="客户端名")

    p_report = sub.add_parser("report", help="写一条交流窗记录")
    p_report.add_argument("--channel", required=True,
                           choices=COMM_CHANNELS, help="交流窗频道")
    p_report.add_argument("--from", dest="from_", required=True,
                          help="report 者身份")
    p_report.add_argument("--text", required=True,
                           help="结论级内容（≤300 字符）")

    p_list_comm = sub.add_parser("list-comm", help="按频道列交流窗记录")
    p_list_comm.add_argument("--channel", choices=COMM_CHANNELS, default=None,
                              help="缺省列全部 comm:* 频道")
    p_list_comm.add_argument("--limit", type=int, default=10,
                              help="最多列几条（默认 10）")

    p_watch = sub.add_parser("watch", help="终端看板（实时监控）")
    p_watch.add_argument("--interval", type=int, default=5,
                         help="轮询间隔秒（默认 5，须 ≥1）")
    p_watch.add_argument("--comm", action="store_true",
                         help="底部附交流窗最近 5 条")
    p_watch.add_argument("--once", action="store_true",
                         help="单轮模式（便于测试/脚本）")

    p_wt = sub.add_parser("worktree", help="git worktree 隔离（TASK-0025）")
    p_wt.add_argument("action", choices=["setup", "enter", "clean"],
                      help="setup 建隔离目录 / enter 打印进入路径 / clean 清理")
    p_wt.add_argument("task_id", help="任务卡号，如 TASK-0025")
    p_wt.add_argument("--repo", default=None,
                      help="仓库根（默认自动探测；测试注入用）")
    return parser


# 子命令 → 处理器映射（入参为解析后的 argparse 命名空间）
_DISPATCH = {
    "status": lambda a: cmd_status(),
    "list-pending": lambda a: cmd_list_pending(),
    "workers": lambda a: cmd_workers(),
    "add": lambda a: cmd_add(assignee=a.assignee, title=a.title, goal=a.goal,
                             input_=a.input, constraints=a.constraints,
                             acceptance=a.acceptance, docs=a.docs),
    "claim": lambda a: cmd_claim(task_id=a.task_id, assignee=a.assignee),
    "show": lambda a: cmd_show(a.task_id),
    "verify": lambda a: cmd_verify(a.task_id, action=a.action, note=a.note,
                                   docs_done=a.docs_done),
    "new-worker": lambda a: cmd_new_worker(a.name),
    "register": lambda a: cmd_register(a.name, model=a.model, client=a.client),
    "report": lambda a: cmd_report(channel=a.channel, from_=a.from_,
                                   text=a.text),
    "list-comm": lambda a: cmd_list_comm(channel=a.channel, limit=a.limit),
    "watch": lambda a: cmd_watch(interval=a.interval, comm=a.comm,
                                 once=a.once),
    "worktree": {
        "setup": lambda a: cmd_worktree_setup(a.task_id, repo=a.repo),
        "enter": lambda a: cmd_worktree_enter(a.task_id, repo=a.repo),
        "clean": lambda a: cmd_worktree_clean(a.task_id, repo=a.repo),
    },
}


def main() -> None:
    """CLI 入口；退出码 0 成功 / 1 参数或校验失败 / 2 服务不可达。"""
    args = build_parser().parse_args()
    try:
        handler = _DISPATCH[args.command]
        if isinstance(handler, dict):  # worktree 按 action 二级分发
            handler = handler[args.action]
        handler(args)
    except BoardUnavailable as e:
        print(f"错误：{e}\n请先启动 kb 服务：python -m kb serve",
              file=sys.stderr)
        raise SystemExit(2) from e
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
