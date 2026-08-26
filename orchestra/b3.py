"""B3 精细化成本管控：配额表 / rounds 记录 / summary 校验（TASK-0053）。

纯函数模块，不调 LLM 不发 HTTP；board.py 接线子命令调用。
依据：docs/superpowers/specs/2026-08-26-b3-cost-control-design.md
  §1.1 ROUNDS 格式、§1.2 SUMMARY 保留标签、§3 配额表。

- get_quota：按复杂度返回 precheck/milestone/total 配额
- parse_rounds / increment_rounds / render_rounds：ROUNDS 记录解析/递增/渲染
- check_summary_tags：SUMMARY 保留标签四类齐全校验
"""

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
