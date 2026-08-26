"""B3 精细化成本管控：配额表 / rounds 记录 / summary 校验 / 建卡联动 / 中断恢复。

核心函数（get_quota/parse_rounds/increment_rounds/render_rounds/check_summary_tags/
extract_complexity/find_latest_summary）为纯函数，不调 LLM 不发 HTTP；
命令函数（cmd_add_with_rounds/cmd_resume）复用 cards 纯函数 + client._request 接线。
依据：docs/superpowers/specs/2026-08-26-b3-cost-control-design.md
  §1.1 ROUNDS 格式、§1.2 SUMMARY 保留标签、§2 流程、§3 配额表、§4.5 中断恢复。

- get_quota：按复杂度返回 precheck/milestone/total 配额
- parse_rounds / increment_rounds / render_rounds：ROUNDS 记录解析/递增/渲染
- check_summary_tags：SUMMARY 保留标签四类齐全校验
- extract_complexity：从卡约束字段提取复杂度（TASK-0054）
- find_latest_summary：从 summary 记录列表找该卡最新摘要（TASK-0054）
- cmd_add_with_rounds：建卡+初始化 rounds 记录（TASK-0054）
- cmd_resume：claimed 卡唤醒续做，先读该卡 summary（TASK-0054）
"""
from cards import (TAG, _next_task_id, check_limits, parse_header, render_card)
from client import _request

# 配额表（spec §3）：simple=1/1/3, medium=2/2/5, complex=2/3/8
_QUOTA_TABLE = {
    "simple": {"precheck": 1, "milestone": 1, "total": 3},
    "medium": {"precheck": 2, "milestone": 2, "total": 5},
    "complex": {"precheck": 2, "milestone": 3, "total": 8},
}
_DEFAULT_COMPLEXITY = "medium"

# SUMMARY 保留标签四类（spec §1.2）：强制不被摘要压缩
_REQUIRED_TAGS = ["决策", "参数", "验收标准", "阻塞点"]

# rounds 可递增节点（review 不计配额但仍递增计数）
_ROUND_NODES = ("precheck", "milestone", "review")


def get_quota(complexity: str = "") -> dict:
    """按复杂度返回配额 dict（precheck/milestone/total）；未注明/非法默认 medium。

    返回副本，调用方修改不影响内部表。
    """
    c = (complexity or _DEFAULT_COMPLEXITY).strip().lower()
    if c not in _QUOTA_TABLE:
        c = _DEFAULT_COMPLEXITY
    return dict(_QUOTA_TABLE[c])


def parse_rounds(content: str) -> dict:
    """解析 ROUNDS 记录内容（spec §1.1），返回结构化 dict。

    格式：
        ROUNDS TASK-0046 | precheck=1 milestone=0 review=0 total=1
        复杂度：simple
        配额：precheck=1 milestone=1 total=3

    返回：{"task_id", "precheck", "milestone", "review", "total",
           "complexity", "quota"}；quota 由复杂度推导（不依赖配额行解析）。
    解析失败抛 ValueError。
    """
    lines = content.strip().split("\n")
    if not lines or not lines[0].startswith("ROUNDS "):
        raise ValueError("ROUNDS 记录首行必须以 'ROUNDS ' 开头")
    # 首行：ROUNDS <task_id> | precheck=N milestone=N review=N total=N
    rest = lines[0][len("ROUNDS "):]
    parts = rest.split("|", 1)
    task_id = parts[0].strip()
    if not task_id:
        raise ValueError("ROUNDS 记录缺少 task_id")
    counts = {"precheck": 0, "milestone": 0, "review": 0, "total": 0}
    if len(parts) > 1:
        for token in parts[1].strip().split():
            if "=" in token:
                k, v = token.split("=", 1)
                if k in counts:
                    try:
                        counts[k] = int(v)
                    except ValueError:
                        pass
    # 复杂度行（默认 medium）
    complexity = _DEFAULT_COMPLEXITY
    for line in lines[1:]:
        if line.startswith("复杂度："):
            complexity = line[len("复杂度："):].strip().lower()
            break
    quota = get_quota(complexity)
    return {
        "task_id": task_id,
        "precheck": counts["precheck"],
        "milestone": counts["milestone"],
        "review": counts["review"],
        "total": counts["total"],
        "complexity": complexity if complexity in _QUOTA_TABLE else _DEFAULT_COMPLEXITY,
        "quota": quota,
    }


def increment_rounds(content: str, node: str) -> str:
    """递增 ROUNDS 记录指定节点计数，total 同步 +1；返回新记录内容。

    node 必须是 precheck/milestone/review 之一；review 不计配额但仍递增。
    保留原复杂度/配额行，配额由复杂度重新推导。
    """
    if node not in _ROUND_NODES:
        raise ValueError(f"节点必须是 {_ROUND_NODES} 之一，got {node}")
    parsed = parse_rounds(content)
    parsed[node] += 1
    parsed["total"] += 1
    q = parsed["quota"]
    first = (f"ROUNDS {parsed['task_id']} | precheck={parsed['precheck']} "
             f"milestone={parsed['milestone']} review={parsed['review']} "
             f"total={parsed['total']}")
    quota_line = f"配额：precheck={q['precheck']} milestone={q['milestone']} total={q['total']}"
    return f"{first}\n复杂度：{parsed['complexity']}\n{quota_line}"


def render_rounds(task_id: str, complexity: str = "") -> str:
    """渲染新 ROUNDS 记录（全零计数，按复杂度给配额）。"""
    c = (complexity or _DEFAULT_COMPLEXITY).strip().lower()
    if c not in _QUOTA_TABLE:
        c = _DEFAULT_COMPLEXITY
    q = _QUOTA_TABLE[c]
    first = f"ROUNDS {task_id} | precheck=0 milestone=0 review=0 total=0"
    quota_line = f"配额：precheck={q['precheck']} milestone={q['milestone']} total={q['total']}"
    return f"{first}\n复杂度：{c}\n{quota_line}"


def check_summary_tags(content: str) -> list[str]:
    """校验 SUMMARY 记录保留标签四类齐全（spec §1.2）。

    检查内容中是否包含 决策/参数/验收标准/阻塞点 四类标签；
    返回缺失的标签列表（空列表表示齐全）。
    校验范围为整个 content（首行摘要正文 + 保留标签行 + 后续行）。
    """
    missing = []
    for tag in _REQUIRED_TAGS:
        if tag not in content:
            missing.append(tag)
    return missing


# ---- TASK-0054：建卡联动与中断恢复 ----
import re as _re

_COMPLEXITY_RE = _re.compile(r"(?:配额|复杂度)[：:\s]+(simple|medium|complex)", _re.IGNORECASE)


def extract_complexity(constraints: str) -> str:
    """从卡约束字段提取复杂度（spec §3：配额由协调者拆卡时在卡内'约束'注明）。

    匹配 '配额 simple' / '复杂度：medium' 等格式；未注明返回空串（由 get_quota 默认 medium）。
    纯函数，不调 HTTP。
    """
    if not constraints:
        return ""
    m = _COMPLEXITY_RE.search(constraints)
    return m.group(1).lower() if m else ""


def find_latest_summary(summaries: list[dict], task_id: str) -> str | None:
    """从 summary 记录列表中找到该卡的最新 summary，返回 content（spec §4.5 中断恢复）。

    summaries 每条为 {"content": str, "updated_at": str}（kb list 返回的 items 子集）；
    SUMMARY 首行格式 'SUMMARY TASK-NNNN round-N | 正文'，按 updated_at 降序取第一条匹配。
    纯函数，不调 HTTP；无匹配返回 None。
    """
    matched = []
    for s in summaries:
        content = s.get("content", "")
        first = content.split("\n", 1)[0].strip()
        # 首行以 'SUMMARY <task_id>' 开头（兼容 round-N 后缀）
        if first.startswith(f"SUMMARY {task_id}"):
            matched.append(s)
    if not matched:
        return None
    # 按 updated_at 降序（字符串 ISO 格式可直接比较）
    matched.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return matched[0]["content"]


def cmd_add_with_rounds(assignee: str, title: str, goal: str, input_: str,
                         constraints: str, acceptance: str, docs: str = "") -> str:
    """创建任务卡 + 初始化 rounds 记录（TASK-0054 建卡联动）。

    复用 cards 纯函数（check_limits/render_card/_next_task_id）+ client._request；
    建卡后按 constraints 标注的复杂度创建 rounds 记录（未注明默认 medium）。
    返回 task_id；rounds 记录 tag=rounds。
    """
    check_limits(title=title, goal=goal, input=input_,
                 constraints=constraints, acceptance=acceptance, docs=docs)
    cards_list = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    task_id = _next_task_id(cards_list)
    content = render_card(task_id, "pending", assignee, title,
                          goal=goal, input_=input_, constraints=constraints,
                          acceptance=acceptance, docs=docs)
    resp = _request("POST", "/memories", {"content": content, "tags": [TAG]})
    print(f"已创建 {task_id} → 记录 {resp['id']}（assignee: {assignee}）")
    # 初始化 rounds 记录（spec §2 任务启动：协调者拆卡→标注复杂度→建 rounds 记录）
    complexity = extract_complexity(constraints)
    rounds_content = render_rounds(task_id, complexity)
    rounds_resp = _request("POST", "/memories",
                            {"content": rounds_content, "tags": ["rounds"]})
    label = complexity if complexity else f"{_DEFAULT_COMPLEXITY}(默认)"
    print(f"rounds 已初始化 {task_id} → 记录 {rounds_resp['id']}（复杂度: {label}）")
    return task_id


def cmd_resume(task_id: str) -> None:
    """claimed 卡唤醒续做：先读该卡 summary 记录（spec §4.5 中断恢复，不依赖对话历史）。

    从 kb 读取 tag=summary 记录，用 find_latest_summary 找该卡最新摘要并输出；
    无 summary 时明确提示（worker 从任务卡原文续做）。纯检索只读，不改卡。
    """
    summaries = _request("GET", "/memories?tag=summary&limit=1000").get("items", [])
    latest = find_latest_summary(summaries, task_id)
    if latest is None:
        print(f"[resume] {task_id} 无 summary 记录，从任务卡原文续做")
        return
    print(f"[resume] {task_id} 最新 summary：\n{latest}")
