#!/usr/bin/env python3
"""agent-orchestra 任务板 CLI 入口：argparse 子命令调度。

协调者专用；worker 走 MCP（orchestra-worker skill）。
各子命令实现按模块分层（protocol.md §10）：
client（HTTP）/ cards（任务卡 CRUD+纯函数）/ registry（注册）/
comm（交流窗）/ worktree（隔离）/ watch（看板）/ feedback（B2 反馈卡）。
用法：python orchestra\\board.py <子命令>（--help 看各子命令参数）。
"""
import argparse
import sys

from client import BoardUnavailable
from cards import (cmd_add, cmd_claim, cmd_list_pending, cmd_new_worker,
                   cmd_pending_count, cmd_show, cmd_status, cmd_verify)
from comm import COMM_CHANNELS, cmd_list_comm, cmd_report
from feedback import TYPES, cmd_fbk_add, cmd_fbk_list, cmd_fbk_show, cmd_fbk_decide
from registry import cmd_register, cmd_workers
from watch import cmd_watch
from worktree import (cmd_worktree_clean, cmd_worktree_enter,
                      cmd_worktree_setup)
from b3 import (check_summary_tags, cmd_add_with_rounds, cmd_resume,
                 get_quota, increment_rounds, render_rounds)
from relation import (cmd_relation_add, cmd_relation_list,
                      cmd_relation_remove)
from mount import (ROLES, TTL_DEFAULT, cmd_heartbeat, cmd_mount,
                   cmd_mount_claim, cmd_mount_idle, cmd_mount_status,
                   cmd_unmount)


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
                        ("pending-count", "统计各状态卡数（一行输出）"),
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

    p_mount = sub.add_parser("mount", help="开始/刷新挂载会话（B5 挂载常驻）")
    p_mount.add_argument("name")
    p_mount.add_argument("--role", default="worker", choices=ROLES,
                         help="角色：worker/designer/subcoordinator/parent")
    p_mount.add_argument("--ttl", type=int, default=TTL_DEFAULT,
                         help="空闲挂载 TTL 秒（默认 900，0=常驻）")

    p_heartbeat = sub.add_parser("heartbeat", help="刷新挂载心跳")
    p_heartbeat.add_argument("name")

    p_unmount = sub.add_parser("unmount", help="退出挂载（exited）")
    p_unmount.add_argument("name")
    p_unmount.add_argument("--reason", default="", help="退出原因")

    p_mount_status = sub.add_parser("mount-status", help="一行一 agent 挂载看板")
    p_mount_status.add_argument("--role", choices=ROLES, default=None,
                                help="按角色过滤")

    p_mount_claim = sub.add_parser("mount-claim",
                                   help="领卡登记主题（更新连续相关链）")
    p_mount_claim.add_argument("name")
    p_mount_claim.add_argument("--topic", required=True,
                               help="任务主题（连续相关≤5 判定依据）")

    p_mount_idle = sub.add_parser("mount-idle",
                                  help="完成任务后转回空闲监听")
    p_mount_idle.add_argument("name")

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
    p_wt.add_argument("task_id", nargs="?", default="",
                      help="任务卡号，如 TASK-0025（clean --all 时可省略）")
    p_wt.add_argument("--repo", default=None,
                      help="仓库根（默认自动探测；测试注入用）")
    p_wt.add_argument("--all", dest="all_", action="store_true",
                      help="clean 时批量清理所有 worktree（非原子，逐个失败不影响其他）")

    p_fb = sub.add_parser("feedback", help="B2 反馈卡（add/list/show）")
    fb_sub = p_fb.add_subparsers(dest="fb_action", required=True)
    p_fb_add = fb_sub.add_parser("add", help="创建反馈卡（open）")
    p_fb_add.add_argument("--proposer", required=True,
                          help="提出者（coordinator/designer/worker）")
    p_fb_add.add_argument("--task", dest="task_id", required=True,
                          help="关联目标卡，如 TASK-0026")
    p_fb_add.add_argument("--type", dest="fb_type", required=True,
                          choices=TYPES, help="objection|risk|clarify")
    p_fb_add.add_argument("--stage", required=True,
                          choices=["precheck", "milestone", "review"],
                          help="节点：precheck|milestone|review")
    p_fb_add.add_argument("--summary", required=True, help="摘要（≤100 字符）")
    p_fb_add.add_argument("--alt", default="",
                          help="替代方案（objection 必附）")
    p_fb_add.add_argument("--impact", default="",
                          help="阻塞点/影响面（risk 必附）")
    p_fb_add.add_argument("--question", default="",
                          help="澄清问题（clarify 必附）")
    p_fb_list = fb_sub.add_parser("list", help="一行一反馈卡")
    p_fb_list.add_argument("--task", dest="task_id", default="",
                           help="按目标卡 TASK-NNNN 过滤（缺省列全部）")
    p_fb_show = fb_sub.add_parser("show", help="打印整张反馈卡")
    p_fb_show.add_argument("fbk_id", help="如 FBK-0001")
    p_fb_decide = fb_sub.add_parser("decide", help="裁决反馈卡（open→accepted/rejected）+ comm:feedback 归档")
    p_fb_decide.add_argument("fbk_id", help="如 FBK-0001")
    dec_group = p_fb_decide.add_mutually_exclusive_group(required=True)
    dec_group.add_argument("--accepted", dest="action", action="store_const",
                           const="accepted", help="采纳（进入方案修订）")
    dec_group.add_argument("--rejected", dest="action", action="store_const",
                           const="rejected", help="打回")
    p_fb_decide.add_argument("--note", default="", help="裁决理由（结论级，入 comm:feedback）")
    p_fb_decide.add_argument("--decider", default="coordinator",
                              help="裁决者身份（默认 coordinator）")

    # B3 成本管控（TASK-0053）：配额/rounds/summary 校验，纯函数接线
    p_b3 = sub.add_parser("b3", help="B3 成本管控（配额/rounds/summary 校验）")
    b3_sub = p_b3.add_subparsers(dest="b3_action", required=True)
    p_b3_quota = b3_sub.add_parser("quota", help="按复杂度打印配额")
    p_b3_quota.add_argument("complexity", nargs="?", default="",
                             help="simple/medium/complex（默认 medium）")
    p_b3_render = b3_sub.add_parser("rounds-render", help="渲染新 ROUNDS 记录（全零）")
    p_b3_render.add_argument("task_id", help="如 TASK-0046")
    p_b3_render.add_argument("complexity", nargs="?", default="",
                              help="simple/medium/complex（默认 medium）")
    p_b3_incr = b3_sub.add_parser("rounds-increment",
                                   help="递增 ROUNDS 记录节点计数（输入 ROUNDS 文本）")
    p_b3_incr.add_argument("content", help="ROUNDS 记录文本")
    p_b3_incr.add_argument("node", choices=["precheck", "milestone", "review"],
                            help="递增节点")
    p_b3_check = b3_sub.add_parser("summary-check",
                                    help="校验 SUMMARY 保留标签四类齐全")
    p_b3_check.add_argument("content", help="SUMMARY 记录文本")
    p_b3_resume = b3_sub.add_parser("resume",
                                     help="claimed 卡唤醒续做：先读该卡 summary（不依赖对话历史）")
    p_b3_resume.add_argument("task_id", help="如 TASK-0046")

    # B3 关联窗口（TASK-0055，spec §4.4）：关联/查关联/解除关联，超限自动归档
    p_rel = sub.add_parser("relation", help="B3 关联窗口（add/list/remove，超限自动归档）")
    rel_sub = p_rel.add_subparsers(dest="rel_action", required=True)
    p_rel_add = rel_sub.add_parser("add", help="关联一张卡（超限自动归档最早关联）")
    p_rel_add.add_argument("task_id", help="主任务卡，如 TASK-0046")
    p_rel_add.add_argument("related", help="被关联卡，如 TASK-0035")
    p_rel_list = rel_sub.add_parser("list", help="查某任务的关联")
    p_rel_list.add_argument("task_id", help="如 TASK-0046")
    p_rel_remove = rel_sub.add_parser("remove", help="解除关联")
    p_rel_remove.add_argument("task_id", help="主任务卡，如 TASK-0046")
    p_rel_remove.add_argument("related", help="被关联卡，如 TASK-0035")
    return parser


# 子命令 → 处理器映射（入参为解析后的 argparse 命名空间）
_DISPATCH = {
    "status": lambda a: cmd_status(),
    "list-pending": lambda a: cmd_list_pending(),
    "pending-count": lambda a: cmd_pending_count(),
    "workers": lambda a: cmd_workers(),
    "add": lambda a: cmd_add_with_rounds(assignee=a.assignee, title=a.title,
                                          goal=a.goal, input_=a.input,
                                          constraints=a.constraints,
                                          acceptance=a.acceptance, docs=a.docs),
    "claim": lambda a: cmd_claim(task_id=a.task_id, assignee=a.assignee),
    "show": lambda a: cmd_show(a.task_id),
    "verify": lambda a: cmd_verify(a.task_id, action=a.action, note=a.note,
                                   docs_done=a.docs_done),
    "new-worker": lambda a: cmd_new_worker(a.name),
    "mount": lambda a: cmd_mount(a.name, role=a.role, ttl=a.ttl),
    "heartbeat": lambda a: cmd_heartbeat(a.name),
    "unmount": lambda a: cmd_unmount(a.name, reason=a.reason),
    "mount-status": lambda a: cmd_mount_status(role=a.role),
    "mount-claim": lambda a: cmd_mount_claim(a.name, topic=a.topic),
    "mount-idle": lambda a: cmd_mount_idle(a.name),
    "register": lambda a: cmd_register(a.name, model=a.model, client=a.client),
    "report": lambda a: cmd_report(channel=a.channel, from_=a.from_,
                                   text=a.text),
    "list-comm": lambda a: cmd_list_comm(channel=a.channel, limit=a.limit),
    "watch": lambda a: cmd_watch(interval=a.interval, comm=a.comm,
                                 once=a.once),
    "worktree": {
        "setup": lambda a: cmd_worktree_setup(a.task_id, repo=a.repo),
        "enter": lambda a: cmd_worktree_enter(a.task_id, repo=a.repo),
        "clean": lambda a: cmd_worktree_clean(a.task_id, repo=a.repo, all_=a.all_),
    },
    "feedback": {
        "add": lambda a: cmd_fbk_add(proposer=a.proposer, task_id=a.task_id,
                                     fb_type=a.fb_type, stage=a.stage,
                                     summary=a.summary, alt=a.alt,
                                     impact=a.impact, question=a.question),
        "list": lambda a: cmd_fbk_list(task_id=a.task_id),
        "show": lambda a: cmd_fbk_show(a.fbk_id),
        "decide": lambda a: cmd_fbk_decide(a.fbk_id, action=a.action,
                                            note=a.note, decider=a.decider),
    },
    "b3": {
        "quota": lambda a: print(get_quota(a.complexity)),
        "rounds-render": lambda a: print(render_rounds(a.task_id, a.complexity)),
        "rounds-increment": lambda a: print(increment_rounds(a.content, a.node)),
        "summary-check": lambda a: print(check_summary_tags(a.content)),
        "resume": lambda a: cmd_resume(a.task_id),
    },
    "relation": {
        "add": lambda a: cmd_relation_add(a.task_id, a.related),
        "list": lambda a: cmd_relation_list(a.task_id),
        "remove": lambda a: cmd_relation_remove(a.task_id, a.related),
    },
}


def main() -> None:
    """CLI 入口；退出码 0 成功 / 1 参数或校验失败 / 2 服务不可达。"""
    args = build_parser().parse_args()
    try:
        handler = _DISPATCH[args.command]
        if isinstance(handler, dict):  # 二级分发（worktree→action / feedback→fb_action / b3→b3_action）
            if args.command == "feedback":
                sub = getattr(args, "fb_action")
            elif args.command == "b3":
                sub = getattr(args, "b3_action")
            elif args.command == "relation":
                sub = getattr(args, "rel_action")
            else:
                sub = getattr(args, "action")
            handler = handler[sub]
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
