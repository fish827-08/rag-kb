"""B3 关联窗口（spec §4.4）：单任务关联卡 ≤5 张，第 6 张起最早关联归档摘要并解除关联。

纯函数（parse_relation / render_relation / add_related / remove_related /
archive_relation_summary）不调 LLM 不发 HTTP；命令函数（cmd_relation_add /
cmd_relation_list / cmd_relation_remove）复用 client._request 接线。
勿动 cards.py。依据：docs/superpowers/specs/2026-08-26-b3-cost-control-design.md §1.3 / §4.4。
"""
import re

from client import _request

TAG = "relation"
MAX_WINDOW = 5  # 单任务关联卡上限（spec §4.4）

# 首行：RELATION TASK-0046 -> TASK-0035,TASK-0036
_RELATION_HEADER_RE = re.compile(r"^RELATION (TASK-\d{4}) ->\s*(.*)$")


def parse_relation(content: str) -> dict:
    """解析 RELATION 记录，返回 {"task_id", "related": [task_id,...], "count"}。

    首行格式：RELATION TASK-0046 -> TASK-0035,TASK-0036
    解析失败抛 ValueError。
    """
    lines = content.strip().split("\n")
    if not lines:
        raise ValueError("RELATION 记录为空")
    m = _RELATION_HEADER_RE.match(lines[0].strip())
    if not m:
        raise ValueError(f"RELATION 首行格式错误: {lines[0][:40]}")
    task_id = m.group(1)
    related_str = m.group(2).strip()
    related = [r.strip() for r in related_str.split(",") if r.strip()] if related_str else []
    return {"task_id": task_id, "related": related, "count": len(related)}


def render_relation(task_id: str, related: list[str]) -> str:
    """渲染 RELATION 记录内容（首行 + 关联数行）。"""
    related_str = ",".join(related) if related else ""
    count = len(related)
    return f"RELATION {task_id} -> {related_str}\n关联数：{count}（≤{MAX_WINDOW}）"


def add_related(relation_content: str, new_task_id: str,
                max_window: int = MAX_WINDOW) -> tuple[str, str | None]:
    """加关联卡；超过 max_window 时最早关联归档并解除关联。

    返回 (new_relation_content, archived_task_id or None)。
    archived_task_id 为被归档的最早关联卡（调用方据此生成 summary 归档记录）。
    已关联的卡不重复加。
    """
    parsed = parse_relation(relation_content)
    related = list(parsed["related"])
    if new_task_id in related:
        return relation_content, None
    related.append(new_task_id)
    archived = None
    if len(related) > max_window:
        archived = related.pop(0)  # 最早关联归档（spec §4.4）
    return render_relation(parsed["task_id"], related), archived


def remove_related(relation_content: str, task_id: str) -> str:
    """解除关联；返回新 relation 内容。"""
    parsed = parse_relation(relation_content)
    related = [r for r in parsed["related"] if r != task_id]
    return render_relation(parsed["task_id"], related)


def archive_relation_summary(related_card_content: str, archived_task_id: str) -> str:
    """将被归档的关联卡生成 SUMMARY 记录（复用 b3 summary 格式，通过 check_summary_tags）。

    从关联卡提取标题/目标/约束/验收/结果，四类保留标签（决策/参数/验收标准/阻塞点）
    齐全，缺失的标"无"。首行：SUMMARY <archived_task_id> relation-archive | 标题
    纯函数，不调 LLM（机械归档，从原卡提取结构化信息）。
    """
    lines = related_card_content.strip().split("\n")
    title = ""
    if lines:
        # 任务卡首行：TASK-NNNN status assignee | title
        parts = lines[0].split("|", 1)
        if len(parts) > 1:
            title = parts[1].strip()

    def _extract(prefix: str) -> str:
        for l in lines:
            if l.startswith(prefix):
                return l[len(prefix):].strip()
        return "无"

    goal = _extract("目标：")
    constraints = _extract("约束：")
    acceptance = _extract("验收：")
    result = _extract("结果：")
    decision = result if result != "无" else "关联窗口超限自动归档"
    return (
        f"SUMMARY {archived_task_id} relation-archive | {title or '关联卡归档'}\n"
        f"决策：{decision}\n"
        f"参数：目标={goal}；约束={constraints}\n"
        f"验收标准：{acceptance}\n"
        f"阻塞点：无（自动归档，无阻塞）"
    )


# ---- 命令函数（复用 client._request）----

def _find_relation_record(task_id: str) -> dict | None:
    """查找某任务的 RELATION 记录，返回 record dict or None。"""
    records = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    for r in records:
        try:
            p = parse_relation(r["content"])
            if p["task_id"] == task_id:
                return r
        except ValueError:
            continue
    return None


def _find_task_card(task_id: str) -> dict | None:
    """查找任务卡（tag=taskboard），返回 record dict or None。"""
    records = _request("GET", "/memories?tag=taskboard&limit=1000").get("items", [])
    for r in records:
        first = r["content"].split("\n", 1)[0].strip()
        if first.startswith(f"{task_id} "):
            return r
    return None


def cmd_relation_add(task_id: str, related_task_id: str) -> None:
    """关联一张卡；超过窗口上限自动归档最早关联为 summary 并解除关联。"""
    rec = _find_relation_record(task_id)
    if rec is None:
        # 新建 relation 记录
        new_content = render_relation(task_id, [related_task_id])
        _request("POST", "/memories", {"content": new_content, "tags": [TAG]})
        print(f"已关联 {task_id} -> {related_task_id}（新建 relation 记录）")
        return
    new_content, archived = add_related(rec["content"], related_task_id)
    if archived is not None:
        # 归档最早关联：读取该卡内容生成 summary（tag=summary）
        archived_card = _find_task_card(archived)
        if archived_card is not None:
            summary_content = archive_relation_summary(archived_card["content"], archived)
            _request("POST", "/memories", {"content": summary_content, "tags": ["summary"]})
            print(f"关联窗口超限，最早关联 {archived} 已归档为 summary")
        else:
            print(f"[警告] 被归档卡 {archived} 未找到，跳过 summary 生成")
    _request("PATCH", f"/memories/{rec['id']}", {"content": new_content})
    print(f"已关联 {task_id} -> {related_task_id}")


def cmd_relation_list(task_id: str) -> None:
    """查某任务的关联。"""
    rec = _find_relation_record(task_id)
    if rec is None:
        print(f"{task_id} 无关联记录")
        return
    p = parse_relation(rec["content"])
    if not p["related"]:
        print(f"{task_id} 关联数：0")
        return
    print(f"{task_id} 关联（{p['count']}张）：{', '.join(p['related'])}")


def cmd_relation_remove(task_id: str, related_task_id: str) -> None:
    """解除关联。"""
    rec = _find_relation_record(task_id)
    if rec is None:
        print(f"{task_id} 无关联记录")
        return
    new_content = remove_related(rec["content"], related_task_id)
    _request("PATCH", f"/memories/{rec['id']}", {"content": new_content})
    print(f"已解除关联 {task_id} -> {related_task_id}")
