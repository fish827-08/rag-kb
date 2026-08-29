"""orchestra 任务卡模块（TASK-0029 拆出纯函数；TASK-0031 收口并入任务板 CRUD）。

两层职责（均为任务卡领域）：
- 纯函数（无 HTTP）：LIMITS/STATUSES 常量、render_card/parse_header/
  check_limits/_fmt_time/_next_task_id
- 任务板 CRUD（走 client._request）：cmd_status/cmd_list_pending/cmd_add/
  cmd_show/cmd_claim/cmd_verify/cmd_new_worker；含 --docs 文档同步清单机制
  （包化设计 §4：add 声明清单渲染进卡，verify pass 未确认即拒绝）

board.py 仅做 CLI 调度，通过 import 复用本模块；
依赖方向：board.py → 本模块 → client.py。
"""
import re
import sys
from datetime import datetime

from client import _request

# 任务卡记录 tag
TAG = "taskboard"

# 各字段字符上限（设计文档第 4 节；docs 为文档同步清单上限，包化设计 §4）
LIMITS = {"title": 30, "goal": 300, "input": 300,
          "constraints": 200, "acceptance": 200, "result": 1000, "docs": 300}
# 状态机合法值
STATUSES = ("pending", "claimed", "done", "failed", "verified")

_HEADER_RE = re.compile(r"^(TASK-\d{4}) (\w+) (\S+) \| (.+)$")


def render_card(task_id: str, status: str, assignee: str, title: str,
                goal: str, input_: str, constraints: str,
                acceptance: str, result: str = "", note: str = "",
                docs: str = "") -> str:
    """渲染完整卡片文本；首行为可检索状态行。

    docs：文档同步清单（包化设计 §4），非空时追加"文档同步："行。
    """
    lines = [
        f"{task_id} {status} {assignee} | {title}",
        f"目标：{goal}",
        f"输入：{input_}",
        f"约束：{constraints}",
        f"验收：{acceptance}",
        f"结果：{result}",
    ]
    if docs:
        lines.append(f"文档同步：{docs}")
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


def cmd_status() -> None:
    """每卡一行：TASK-0003 claimed worker-1 12:30 标题。"""
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    if not cards:
        print("无任务卡")
        return
    for card in cards:
        try:
            h = parse_header(card["content"])
        except ValueError:
            print(f"[警告] 记录 {card.get('id', '?')} 首行非法，已跳过")
            continue
        print(f"{h['task_id']} {h['status']} {h['assignee']} "
              f"{_fmt_time(card.get('updated_at', ''))} {h['title']}")


def cmd_list_pending() -> None:
    """只列出 pending 状态的任务卡；无待办时给出明确提示。

    取卡与解析复用 cmd_status 的方式（_request + parse_header），
    一行一卡格式与 status 完全一致。
    """
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    lines = []
    for card in cards:
        try:
            h = parse_header(card["content"])
        except ValueError:
            continue  # 非法卡无法判定状态，不参与过滤
        if h["status"] == "pending":
            lines.append(f"{h['task_id']} {h['status']} {h['assignee']} "
                         f"{_fmt_time(card.get('updated_at', ''))} {h['title']}")
    if lines:
        print("\n".join(lines))
    else:
        print("无待办任务卡")


def cmd_pending_count() -> None:
    """统计任务板各状态卡数，一行输出（如 pending:3 claimed:2 done:1 verified:30 failed:1）。

    纯只读统计，不改卡；空板返回全零；非法首行记录跳过不计数。
    输出格式固定顺序、空格分隔，便于 shell 解析。
    """
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    counts = {"pending": 0, "claimed": 0, "done": 0, "verified": 0, "failed": 0}
    for card in cards:
        try:
            h = parse_header(card["content"])
        except ValueError:
            continue  # 非法首行跳过，不参与计数
        status = h["status"]
        if status in counts:
            counts[status] += 1
    print(" ".join(f"{k}:{v}" for k, v in counts.items()))


def cmd_add(assignee: str, title: str, goal: str, input_: str,
            constraints: str, acceptance: str, docs: str = "") -> None:
    """创建任务卡（pending）；字段超长抛 ValueError。

    docs：文档同步清单（包化设计 §4），如 "USER_GUIDE.md(端点速查节)"；
    非空时渲染为卡内"文档同步："行，verify pass 时为硬门禁。
    """
    # 注意：input 用作 kwargs 键以对齐 LIMITS["input"]（而非形参名 input_）
    check_limits(title=title, goal=goal, input=input_,
                 constraints=constraints, acceptance=acceptance, docs=docs)
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    task_id = _next_task_id(cards)
    content = render_card(task_id, "pending", assignee, title,
                          goal=goal, input_=input_, constraints=constraints,
                          acceptance=acceptance, docs=docs)
    resp = _request("POST", "/memories",
                    {"content": content, "tags": [TAG]})
    print(f"已创建 {task_id} → 记录 {resp['id']}（assignee: {assignee}）")


def _find_card(task_id: str) -> tuple[dict, dict]:
    """按 TASK 编号找卡；返回 (记录, 首行解析)，找不到 SystemExit(1)。"""
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    for card in cards:
        try:
            h = parse_header(card["content"])
        except ValueError:
            continue
        if h["task_id"] == task_id:
            return card, h
    print(f"错误：任务卡 {task_id} 不存在", file=sys.stderr)
    raise SystemExit(1)


def cmd_show(task_id: str) -> None:
    """打印整卡（核验用）。"""
    card, _ = _find_card(task_id)
    print(card["content"])


def cmd_claim(task_id: str, assignee: str) -> None:
    """认领卡片：pending→claimed 并更新 assignee。

    仅 pending 状态可认领；其他状态报错不误改。
    """
    card, h = _find_card(task_id)
    if h["status"] != "pending":
        print(f"错误：{task_id} 状态为 {h['status']}，"
              f"仅 pending 可认领", file=sys.stderr)
        raise SystemExit(1)
    # 重写首行：status 改 claimed，assignee 更新为指定值
    content = card["content"].split("\n", 1)
    rest = content[1] if len(content) > 1 else ""
    new_content = (f"{task_id} claimed {assignee} | {h['title']}"
                   + ("\n" + rest if rest else ""))
    _request("PATCH", f"/memories/{card['id']}", {"content": new_content})
    print(f"{task_id} → claimed（assignee: {assignee}）")


def cmd_verify(task_id: str, action: str, note: str,
               docs_done: bool = False) -> None:
    """核验流转：pass → verified；reject → pending（note 写入备注行）。

    仅 done/failed 状态可流转；其他状态 SystemExit(1)。
    文档同步硬门禁（包化设计 §4）：卡内含"文档同步："清单且未传
    docs_done 时拒绝 pass，防止文档同步被遗漏。
    """
    card, h = _find_card(task_id)
    if h["status"] not in ("done", "failed"):
        print(f"错误：{task_id} 状态为 {h['status']}，"
              f"仅 done/failed 可核验", file=sys.stderr)
        raise SystemExit(1)
    docs_line = next((l for l in card["content"].split("\n")
                      if l.startswith("文档同步：")), "")
    if action == "pass" and docs_line and not docs_done:
        print(f"错误：{task_id} 声明了文档同步清单未确认完成：\n{docs_line}\n"
              f"请核对清单内文档已同步后加 --docs-done 重新核验",
              file=sys.stderr)
        raise SystemExit(1)
    new_status = "verified" if action == "pass" else "pending"
    content = card["content"].split("\n", 1)
    # 重写首行；reject 时若有 note 追加备注行
    rest = content[1] if len(content) > 1 else ""
    if action == "reject" and note:
        # 去掉旧备注行（若有）再追加新备注
        rest = "\n".join(l for l in rest.split("\n")
                         if not l.startswith("备注："))
        rest = (rest + f"\n备注：{note}").strip("\n")
    new_content = (f"{task_id} {new_status} {h['assignee']} | {h['title']}"
                   + ("\n" + rest if rest else ""))
    _request("PATCH", f"/memories/{card['id']}", {"content": new_content})
    print(f"{task_id} → {new_status}" + (f"（备注：{note}）" if note else ""))


WORKER_INTRO = """你是 {name}，agent-orchestra 的执行者（worker，挂载常驻模式）。
请在当前任务中执行 skill：orchestra-worker，然后按其协议工作：
1. board.py mount {name} --role worker --ttl 900 启动挂载；
2. 进入挂载循环：查卡 → 有卡领卡干活回写 → mount-idle；无卡 heartbeat + sleep 60s；
3. 空闲满 15 分钟自动 unmount 停机；连续同主题 ≥5 写 summary 后 unmount 上下文重置。
完整协议见 orchestra/worker-prompt.md。"""

DESIGNER_INTRO = """你是 {name}，agent-orchestra 的设计者（designer，挂载常驻模式）。
职责：写设计书与验收测试草案，不写业务实现代码。挂载循环同 worker：
board.py mount {name} --role designer --ttl 900 后查卡 → 有卡设计回写 → mount-idle。
完整协议见 orchestra/designer-prompt.md。"""

SUBCOORD_INTRO = """你是 {name}，agent-orchestra 的子协调者（subcoordinator，持续挂载常驻）。
职责：拆卡、分发、监听、核验、仲裁、合并推送（确定性动作交机械臂 coordinator_loop.py）。
board.py mount {name} --role subcoordinator --ttl 0 启动常驻（不超时）。
完整协议见 orchestra/coordinator-prompt.md（先读文首"挂载常驻"节）。"""

PARENT_INTRO = """你是 {name}，agent-orchestra 的父协调者（parent，不挂载、按需唤醒）。
职责：面向用户接收高层目标 → 建"拆卡卡"(assignee=subcoordinator) 派活 → 终验汇报。
完整协议见 orchestra/parent-coordinator-prompt.md。"""

_AGENT_INTROS = {
    "worker": WORKER_INTRO,
    "designer": DESIGNER_INTRO,
    "subcoordinator": SUBCOORD_INTRO,
    "parent": PARENT_INTRO,
}


def cmd_new_worker(name: str, role: str = "worker") -> None:
    """打印该角色的引导语（用户复制到新 AI 会话，默认 worker）。"""
    if role not in _AGENT_INTROS:
        raise ValueError(f"role 非法：{role!r}，可选 {', '.join(_AGENT_INTROS)}")
    print(_AGENT_INTROS[role].format(name=name))
