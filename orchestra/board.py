#!/usr/bin/env python3
"""agent-orchestra 任务板 CLI：管理 kb 服务上的 taskboard 任务卡。

协调者专用；worker 走 MCP（orchestra-worker skill）。
仅标准库；kb REST 契约见 rag-kb kb/api.py。

用法：
    board.py add --assignee w1 --title T --goal G --input I --constraints C --acceptance A
    board.py status
    board.py list-pending
    board.py claim TASK-XXXX --assignee worker-N
    board.py register NAME --model X --client Y
    board.py workers
    board.py show TASK-0003
    board.py verify TASK-0003 --pass | --reject [--note 原因]
    board.py new-worker NAME
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

KB_BASE = "http://127.0.0.1:8000/api/v1"
TAG = "taskboard"
# worker 注册表记录 tag（orchestra v2 V2-1）
REGISTRY_TAG = "registry"
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


def _fmt_time(updated_at: str) -> str:
    """ISO 时间 → HH:MM；解析失败返回 '???'。"""
    try:
        return datetime.fromisoformat(updated_at).strftime("%H:%M")
    except (ValueError, TypeError):
        return "???"


def _now_iso() -> str:
    """当前本地时间 ISO 格式（分钟精度），registry 记录时间戳用。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


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


def cmd_add(assignee: str, title: str, goal: str, input_: str,
            constraints: str, acceptance: str) -> None:
    """创建任务卡（pending）；字段超长抛 ValueError。"""
    # 注意：input 用作 kwargs 键以对齐 LIMITS["input"]（而非形参名 input_）
    check_limits(title=title, goal=goal, input=input_,
                 constraints=constraints, acceptance=acceptance)
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    task_id = _next_task_id(cards)
    content = render_card(task_id, "pending", assignee, title,
                          goal=goal, input_=input_, constraints=constraints,
                          acceptance=acceptance)
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


def cmd_verify(task_id: str, action: str, note: str) -> None:
    """核验流转：pass → verified；reject → pending（note 写入备注行）。

    仅 done/failed 状态可流转；其他状态 SystemExit(1)。
    """
    card, h = _find_card(task_id)
    if h["status"] not in ("done", "failed"):
        print(f"错误：{task_id} 状态为 {h['status']}，"
              f"仅 done/failed 可核验", file=sys.stderr)
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


def cmd_register(name: str, model: str, client: str) -> None:
    """注册/刷新 worker 身份（tag=registry，内容为 JSON）。

    首次注册写新记录（registered_at/last_seen 均为当前时间，status=idle）；
    同名重复注册只刷新 model/client 与 last_seen，不重复建卡。
    """
    if not name or not model or not client:
        raise ValueError("register 需要 name、--model、--client 均非空")
    cards = _request("GET", f"/memories?tag={REGISTRY_TAG}&limit=1000") \
        .get("items", [])
    for card in cards:
        try:
            data = json.loads(card["content"])
        except (ValueError, TypeError):
            continue
        if data.get("worker") == name:
            data["model"] = model
            data["client"] = client
            data["last_seen"] = _now_iso()
            _request("PATCH", f"/memories/{card['id']}",
                     {"content": json.dumps(data, ensure_ascii=False)})
            print(f"已刷新 {name}（model: {model}, client: {client}）")
            return
    data = {"worker": name, "model": model, "client": client,
            "registered_at": _now_iso(), "last_seen": _now_iso(),
            "status": "idle"}
    _request("POST", "/memories",
             {"content": json.dumps(data, ensure_ascii=False),
              "tags": [REGISTRY_TAG]})
    print(f"已注册 {name}（model: {model}, client: {client}）")


def cmd_workers() -> None:
    """一行一 worker：名字 模型 状态 最后活跃；空表明确提示。"""
    cards = _request("GET", f"/memories?tag={REGISTRY_TAG}&limit=1000") \
        .get("items", [])
    if not cards:
        print("无已注册 worker")
        return
    for card in cards:
        try:
            data = json.loads(card["content"])
        except (ValueError, TypeError):
            print("[警告] registry 记录内容非 JSON，已跳过")
            continue
        print(f"{data.get('worker', '?')} {data.get('model', '?')} "
              f"{data.get('status', '?')} {data.get('last_seen', '?')}")


WORKER_INTRO = """你是 {name}，agent-orchestra 的执行者（worker）。
请在当前任务中执行 skill：orchestra-worker，然后按其协议开始工作：
查卡 → 认领 → 执行 → 回写 → 停止。若无待办任务，回复待命即可。"""


def cmd_new_worker(name: str) -> None:
    """打印该 worker 的引导语（用户复制到新 TraeWork 任务）。"""
    print(WORKER_INTRO.format(name=name))


def main() -> None:
    """CLI 入口；退出码 0 成功 / 1 参数或校验失败 / 2 服务不可达。"""
    parser = argparse.ArgumentParser(description="agent-orchestra 任务板")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="创建任务卡")
    p_add.add_argument("--assignee", required=True)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--goal", required=True)
    p_add.add_argument("--input", required=True)
    p_add.add_argument("--constraints", required=True)
    p_add.add_argument("--acceptance", required=True)

    sub.add_parser("status", help="一行一卡看板")
    sub.add_parser("list-pending", help="只列待办卡")

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

    p_new = sub.add_parser("new-worker", help="打印 worker 引导语")
    p_new.add_argument("name")

    p_register = sub.add_parser("register", help="注册/刷新 worker 身份")
    p_register.add_argument("name")
    p_register.add_argument("--model", required=True, help="模型名")
    p_register.add_argument("--client", required=True, help="客户端名")

    sub.add_parser("workers", help="一行一 worker 列表")

    args = parser.parse_args()
    try:
        if args.command == "add":
            cmd_add(assignee=args.assignee, title=args.title, goal=args.goal,
                    input_=args.input, constraints=args.constraints,
                    acceptance=args.acceptance)
        elif args.command == "status":
            cmd_status()
        elif args.command == "list-pending":
            cmd_list_pending()
        elif args.command == "claim":
            cmd_claim(task_id=args.task_id, assignee=args.assignee)
        elif args.command == "show":
            cmd_show(args.task_id)
        elif args.command == "verify":
            cmd_verify(args.task_id, action=args.action, note=args.note)
        elif args.command == "new-worker":
            cmd_new_worker(args.name)
        elif args.command == "register":
            cmd_register(args.name, model=args.model, client=args.client)
        elif args.command == "workers":
            cmd_workers()
    except BoardUnavailable as e:
        print(f"错误：{e}\n请先启动 kb 服务：python -m kb serve",
              file=sys.stderr)
        raise SystemExit(2) from e
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
