"""worker 注册表模块（TASK-0030 包化③：自 board.py 机械搬移）。

含 registry 相关：
- REGISTRY_TAG：registry 记录 tag
- _now_iso：分钟精度时间戳
- cmd_register：注册/刷新 worker 身份
- cmd_workers：一行一 worker 列表

board.py 仍负责 CLI 调度，通过 import 复用本模块；
依赖方向单向：board.py → 本模块 → client.py。
"""
import json
from datetime import datetime

from client import _request

# worker 注册表记录 tag（orchestra v2 V2-1）
REGISTRY_TAG = "registry"


def _now_iso() -> str:
    """当前本地时间 ISO 格式（分钟精度），registry 记录时间戳用。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


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
