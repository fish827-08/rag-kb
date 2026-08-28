"""orchestra 挂载状态模块（B5-1：挂载常驻改造第一节点）。

管理 worker/designer/子协调者的"挂载生命周期"状态（kb 记录 tag=mount_state）：
- cmd_mount：开始/刷新挂载会话（mounted，清空主题链，换新 session_id）
- cmd_heartbeat：刷新心跳（空闲监听存活信号）
- cmd_unmount：退出挂载（exited）
- cmd_mount_status：一行一 agent 挂载看板
- cmd_mount_claim：领卡登记主题（working，更新连续相关链/streak，达 5 打印重置标记）
- cmd_mount_idle：完成任务回写后转回空闲监听（mounted，刷新 idle_since/心跳）

设计依据：orchestra/docs/superpowers/specs/2026-08-28-orchestra-mount-design.md
board.py 仅做 CLI 调度，通过 import 复用本模块；
依赖方向：board.py → 本模块 → client.py。
"""
import json
from datetime import datetime

from client import _request

# 挂载态记录 tag
MOUNT_TAG = "mount_state"

# 角色枚举（挂载主体；parent 不挂载，仅作身份保留）
ROLES = ("worker", "designer", "subcoordinator", "parent")
# 挂载生命周期状态机：mounted(空闲监听) → working(干活) → mounted / exited(停机)
MOUNT_STATUSES = ("mounted", "working", "exited")

# 默认参数（可被 CLI 参数覆盖）
TTL_DEFAULT = 900    # worker/designer 空闲挂载 TTL：15 分钟
TTL_INFINITE = 0     # 子协调者常驻（0 = 不超时）
STREAK_LIMIT = 5     # 连续相关任务上限（协议 §连续相关≤5）


def _now_iso() -> str:
    """当前本地时间 ISO 格式（秒精度），挂载时间戳用。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _new_session_id(now: str) -> str:
    """生成挂载会话 ID（M-<时间戳>，去掉分隔符便于识别）。"""
    return "M-" + now.replace("-", "").replace(":", "").replace("T", "")


def _find_mount(name: str) -> tuple[dict | None, dict | None]:
    """按 agent 名找挂载态记录；返回 (记录, 解析后的 content dict)；无则 (None, None)。"""
    cards = _request("GET", f"/memories?tag={MOUNT_TAG}&limit=1000") \
        .get("items", [])
    for card in cards:
        try:
            data = json.loads(card["content"])
        except (ValueError, TypeError):
            continue  # 非 JSON 脏记录跳过
        if data.get("agent") == name:
            return card, data
    return None, None


def _new_payload(name: str, role: str, ttl: int, now: str) -> dict:
    """构造一条全新挂载态 content（mounted，链清零）。"""
    return {
        "agent": name, "role": role,
        "session_id": _new_session_id(now),
        "mount_status": "mounted",
        "mounted_at": now, "last_heartbeat": now, "idle_since": now,
        "ttl": ttl,
        "topic_chain": [], "topic_streak": 0,
        "reset_reason": "",
    }


def cmd_mount(name: str, role: str, ttl: int = TTL_DEFAULT) -> None:
    """开始/刷新挂载会话：首次 POST 建记录，已存在则 PATCH 刷新并清空主题链。"""
    if not name:
        raise ValueError("mount 需要 name 非空")
    if role not in ROLES:
        raise ValueError(f"role 非法：{role!r}，可选 {', '.join(ROLES)}")
    if ttl < 0:
        raise ValueError(f"ttl 须 ≥0（0=常驻），收到 {ttl}")
    now = _now_iso()
    card, _ = _find_mount(name)
    payload = _new_payload(name, role, ttl, now)
    content = json.dumps(payload, ensure_ascii=False)
    if card is None:
        _request("POST", "/memories",
                 {"content": content, "tags": [MOUNT_TAG], "source": name})
        print(f"已挂载 {name}（{role}，ttl={ttl}，session {payload['session_id']}）")
    else:
        _request("PATCH", f"/memories/{card['id']}", {"content": content})
        print(f"已重挂载 {name}（{role}，ttl={ttl}，session {payload['session_id']}，主题链已重置）")


def cmd_heartbeat(name: str) -> None:
    """刷新 last_heartbeat（空闲监听存活信号）；未挂载或已退出报错。"""
    card, data = _find_mount(name)
    if card is None:
        raise ValueError(f"{name} 尚未挂载，先 board.py mount")
    if data.get("mount_status") == "exited":
        raise ValueError(f"{name} 已退出挂载，需重新 mount")
    now = _now_iso()
    data["last_heartbeat"] = now
    _request("PATCH", f"/memories/{card['id']}",
             {"content": json.dumps(data, ensure_ascii=False)})
    print(f"{name} 心跳 {now}")


def cmd_unmount(name: str, reason: str = "") -> None:
    """退出挂载（exited），可带原因；未挂载报错。"""
    card, data = _find_mount(name)
    if card is None:
        raise ValueError(f"{name} 尚未挂载，无法退出")
    data["mount_status"] = "exited"
    data["reset_reason"] = reason
    _request("PATCH", f"/memories/{card['id']}",
             {"content": json.dumps(data, ensure_ascii=False)})
    print(f"{name} 已退出挂载" + (f"（{reason}）" if reason else ""))


def cmd_mount_status(role: str | None = None) -> None:
    """一行一 agent 挂载看板：名字 角色 状态 心跳 ttl streak；可按角色过滤。"""
    cards = _request("GET", f"/memories?tag={MOUNT_TAG}&limit=1000") \
        .get("items", [])
    rows = []
    for card in cards:
        try:
            data = json.loads(card["content"])
        except (ValueError, TypeError):
            print(f"[警告] mount_state 记录 {card.get('id', '?')} 内容非 JSON，已跳过")
            continue
        if role and data.get("role") != role:
            continue
        rows.append(data)
    if not rows:
        print("无挂载中的 agent")
        return
    rows.sort(key=lambda d: (d.get("role", ""), d.get("agent", "")))
    for d in rows:
        print(f"{d.get('agent', '?')} {d.get('role', '?')} "
              f"{d.get('mount_status', '?')} 心跳{d.get('last_heartbeat', '?')} "
              f"ttl={d.get('ttl', '?')} streak={d.get('topic_streak', 0)}")


def cmd_mount_claim(name: str, topic: str) -> None:
    """领卡登记主题：转 working、更新连续相关链/streak，达上限打印重置标记。"""
    if not topic:
        raise ValueError("mount-claim 需要 --topic 非空")
    card, data = _find_mount(name)
    if card is None:
        raise ValueError(f"{name} 尚未挂载，先 board.py mount")
    if data.get("mount_status") == "exited":
        raise ValueError(f"{name} 已退出挂载，需重新 mount")
    chain = data.get("topic_chain", [])
    if chain and chain[-1] == topic:
        chain.append(topic)      # 与上一主题相同 → 连续相关累加
    else:
        chain = [topic]          # 不同主题 → 开新链，计数重置
    streak = len(chain)
    data["topic_chain"] = chain
    data["topic_streak"] = streak
    data["mount_status"] = "working"
    data["idle_since"] = ""      # 干活期间无空闲计时
    data["last_heartbeat"] = _now_iso()
    _request("PATCH", f"/memories/{card['id']}",
             {"content": json.dumps(data, ensure_ascii=False)})
    flag = ""
    if streak >= STREAK_LIMIT:
        flag = (f"；⚠ 连续相关已达 {streak}（≥{STREAK_LIMIT}），"
                f"本卡完成后需上下文重置")
    print(f"{name} 领卡 主题={topic} 连续相关={streak}{flag}")


def cmd_mount_idle(name: str) -> None:
    """完成任务回写后转回空闲监听：mounted，刷新 idle_since 与心跳。"""
    card, data = _find_mount(name)
    if card is None:
        raise ValueError(f"{name} 尚未挂载，先 board.py mount")
    now = _now_iso()
    data["mount_status"] = "mounted"
    data["idle_since"] = now
    data["last_heartbeat"] = now
    _request("PATCH", f"/memories/{card['id']}",
             {"content": json.dumps(data, ensure_ascii=False)})
    print(f"{name} 转空闲监听（idle_since={now}）")
