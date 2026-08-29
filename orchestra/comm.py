"""交流窗模块（TASK-0030 包化④：自 board.py 机械搬移）。

含交流窗相关：
- COMM_CHANNELS / COMM_TEXT_LIMIT：频道枚举与 text 上限（协议 §7 / V2-2 设计书）
- cmd_report：写一条交流窗记录
- cmd_list_comm：按频道列最新 N 条记录
- _comm_tag / _truncate：标签提取与超长截断辅助函数

board.py 仍负责 CLI 调度，通过 import 复用本模块；
依赖方向单向：board.py → 本模块 → cards.py / client.py。
"""
from cards import _fmt_time
from client import _request

# 交流窗频道枚举与 text 上限（protocol §7 / V2-2 设计书）
# TASK-0057：补 dispatch 频道（comm:dispatch 异常调度播报，TASK-0049 写入）
COMM_CHANNELS = ("done", "issue", "test", "system", "dispatch")
COMM_TEXT_LIMIT = 300


def cmd_report(channel: str, from_: str, text: str) -> None:
    """写一条交流窗记录（tag=comm:<channel>，source=report 者）。

    校验：channel 为枚举、from 非空、text 非空且 ≤300 字符（协议 §7 上限）；
    不合法抛 ValueError（不发请求）。
    """
    if channel not in COMM_CHANNELS:
        raise ValueError(
            f"channel 非法：{channel!r}，可选 {', '.join(COMM_CHANNELS)}")
    if not from_:
        raise ValueError("report 需要 --from 非空")
    if not text.strip():
        raise ValueError("report 需要 --text 非空")
    if len(text) > COMM_TEXT_LIMIT:
        raise ValueError(
            f"text 超长：{len(text)} 字符 > 上限 {COMM_TEXT_LIMIT}")
    resp = _request("POST", "/memories",
                    {"content": text, "tags": [f"comm:{channel}"],
                     "source": from_})
    print(f"已写 comm:{channel}（记录 {resp['id']}）")


def _comm_tag(tags) -> str | None:
    """取记录中首个 comm:* 标签；无则返回 None。"""
    for t in tags or []:
        if t.startswith("comm:"):
            return t
    return None


def _truncate(text: str, n: int = 60) -> str:
    """超长截断至 n 字符并追加省略号，避免刷屏。"""
    return text[:n] + "…" if len(text) > n else text


def cmd_list_comm(channel: str | None = None, limit: int = 10) -> None:
    """按频道列最新 N 条交流窗记录；缺省频道列全部 comm:*，updated_at 降序。

    输出一行一条：HH:MM | comm:<tag> | <source> | <text 截断>；空表明确提示。
    """
    if limit < 1:
        raise ValueError(f"limit 须 ≥1，收到 {limit}")
    if channel is not None:
        cards = _request(
            "GET", f"/memories?tag=comm:{channel}&limit=1000").get("items", [])
        # 服务端按 tag 过滤后再本地复核，避免脏数据混入
        cards = [c for c in cards
                 if _comm_tag(c.get("tags")) == f"comm:{channel}"]
    else:
        cards = _request("GET", "/memories?limit=1000").get("items", [])
        cards = [c for c in cards if _comm_tag(c.get("tags")) is not None]
    if not cards:
        print("无交流窗记录")
        return
    cards.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    for card in cards[:limit]:
        tag = _comm_tag(card.get("tags")) or "comm:?"
        print(f"{_fmt_time(card.get('updated_at', ''))} | {tag} "
              f"| {card.get('source') or '?'} | "
              f"{_truncate(card.get('content', ''))}")
