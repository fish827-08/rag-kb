"""orchestra 反馈卡模块（TASK-0032：B2 双向反馈闭环）。

数据模型（设计书 B2 §1/§4）：kb 记录 tag=feedback，关联目标卡 TASK-NNNN。
- 编号 FBK-NNNN（可回溯关联目标卡）
- 三类型：objection（异议，必附替代方案）/ risk（风险，必附阻塞点+影响面）/
  clarify（澄清，必附澄清问题）
- 状态机：结果 open → accepted / rejected（add 默认 open）

命令：add / list / show；依赖方向：board.py → 本模块 → client.py。
"""
import re
import sys
from datetime import datetime

from client import _request

# 反馈卡记录 tag
TAG = "feedback"

# 类型与节点枚举（设计书 §1）
TYPES = ("objection", "risk", "clarify")
STAGES = ("precheck", "milestone", "review")
# 结果状态机：open → accepted / rejected
RESULTS = ("open", "accepted", "rejected")

# 字段字符上限
LIMITS = {"summary": 100, "alt": 500, "impact": 500, "question": 500}

# 各类型必附字段（设计书 §4 判定规则，缺失即无效反馈）
REQUIRED_BY_TYPE = {
    "objection": "替代方案",
    "risk": "阻塞点/影响面",
    "clarify": "澄清问题",
}

# 首行：FBK-0001 feedback TASK-0026 | objection precheck
_FBK_HEADER_RE = re.compile(
    r"^(FBK-\d{4}) feedback (TASK-\d{4}) \| (\w+) (\w+)$")


def render_fbk(fbk_id: str, proposer: str, task_id: str, fb_type: str,
               stage: str, summary: str, alt: str = "", impact: str = "",
               question: str = "", result: str = "open") -> str:
    """渲染完整反馈卡文本；首行为可检索状态行，必附字段按类型落行。"""
    lines = [
        f"{fbk_id} feedback {task_id} | {fb_type} {stage}",
        f"提出者：{proposer}",
        f"目标卡：{task_id}",
        f"类型：{fb_type}",
        f"节点：{stage}",
        f"摘要：{summary}",
    ]
    if alt:
        lines.append(f"替代方案：{alt}")
    if impact:
        lines.append(f"阻塞点/影响面：{impact}")
    if question:
        lines.append(f"澄清问题：{question}")
    lines.append(f"结果：{result}")
    return "\n".join(lines)


def parse_fbk_header(content: str) -> dict:
    """解析反馈卡首行 → {fbk_id, task_id, fb_type, stage}；非法抛 ValueError。"""
    header = content.split("\n", 1)[0].strip()
    m = _FBK_HEADER_RE.match(header)
    if not m:
        raise ValueError(f"反馈卡首行格式非法：{header!r}")
    return {"fbk_id": m.group(1), "task_id": m.group(2),
            "fb_type": m.group(3), "stage": m.group(4)}


def parse_fbk_result(content: str) -> str:
    """解析"结果："字段；缺省视为 open。"""
    for line in content.split("\n"):
        if line.startswith("结果："):
            return line[len("结果："):].strip()
    return "open"


def check_fbk_limits(summary: str, alt: str = "", impact: str = "",
                     question: str = "") -> None:
    """字段长度校验；超限抛 ValueError（中文提示字段名与上限）。"""
    for name, value in (("summary", summary), ("alt", alt),
                        ("impact", impact), ("question", question)):
        if value and len(value) > LIMITS[name]:
            raise ValueError(
                f"字段 {name} 超长：{len(value)} 字符 > 上限 {LIMITS[name]}")


def check_fbk_required(fb_type: str, alt: str = "", impact: str = "",
                       question: str = "") -> None:
    """按类型必附字段校验（B2 §4 判定规则）。

    objection 必附替代方案 / risk 必附阻塞点影响面 / clarify 必附澄清问题，
    缺失抛 ValueError（无替代方案的异议直接驳回）。
    """
    need = REQUIRED_BY_TYPE.get(fb_type)
    if not need:
        raise ValueError(f"类型非法：{fb_type}（应为 objection/risk/clarify）")
    provided = {"替代方案": alt, "阻塞点/影响面": impact, "澄清问题": question}
    if not provided[need]:
        raise ValueError(f"{fb_type} 反馈必附{need}")


def check_result_transition(current: str, target: str) -> None:
    """结果状态机校验：仅 open → accepted / rejected（B2 §1）。"""
    if current != "open":
        raise ValueError(f"仅 open 结果可流转，当前为 {current}")
    if target not in ("accepted", "rejected"):
        raise ValueError(f"结果非法：{target}（应为 accepted/rejected）")


def _fmt_time(updated_at: str) -> str:
    """ISO 时间 → HH:MM；解析失败返回 '???'。"""
    try:
        return datetime.fromisoformat(updated_at).strftime("%H:%M")
    except (ValueError, TypeError):
        return "???"


def _next_fbk_id(cards: list[dict]) -> str:
    """现有反馈卡最大编号 +1，四位数零填充。"""
    max_num = 0
    for card in cards:
        try:
            h = parse_fbk_header(card["content"])
            num = int(h["fbk_id"].split("-")[1])
            max_num = max(max_num, num)
        except (ValueError, IndexError):
            continue  # 非法卡不参与编号
    return f"FBK-{max_num + 1:04d}"


def count_task_feedback(task_id: str, cards: list[dict]) -> dict:
    """统计某任务卡各节点反馈卡数量 → {precheck, milestone, review, total}。

    仅统计首行合法且 task_id 匹配的反馈卡；非法首行跳过。
    """
    counts = {"precheck": 0, "milestone": 0, "review": 0, "total": 0}
    for card in cards:
        try:
            h = parse_fbk_header(card["content"])
        except ValueError:
            continue
        if h["task_id"] != task_id:
            continue
        stage = h["stage"]
        if stage in counts:
            counts[stage] += 1
        counts["total"] += 1
    return counts


def check_quota(task_id: str, stage: str, cards: list[dict]) -> None:
    """分节点配额硬门禁（B2 §3）：precheck≤2 / milestone≤2 / 总≤5 / review不计。

    超限抛 ValueError（提示转协调者仲裁）；仅在创建新反馈卡前调用。
    review 节点为沉淀性反馈，不占配额。
    """
    if stage == "review":
        return  # 复盘节点不计配额
    c = count_task_feedback(task_id, cards)
    if c["total"] >= 5:
        raise ValueError(
            f"任务 {task_id} 反馈已达总上限 5 轮，超限拒绝新卡，请转协调者仲裁")
    if stage == "precheck" and c["precheck"] >= 2:
        raise ValueError(
            f"任务 {task_id} precheck 反馈已达 2 轮上限，超限拒绝新卡，请转协调者仲裁")
    if stage == "milestone" and c["milestone"] >= 2:
        raise ValueError(
            f"任务 {task_id} milestone 反馈已达 2 轮上限，超限拒绝新卡，请转协调者仲裁")


def _find_fbk(fbk_id: str) -> tuple[dict, dict]:
    """按 FBK 编号找反馈卡；返回 (记录, 首行解析)，找不到 SystemExit(1)。"""
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    for card in cards:
        try:
            h = parse_fbk_header(card["content"])
        except ValueError:
            continue
        if h["fbk_id"] == fbk_id:
            return card, h
    print(f"错误：反馈卡 {fbk_id} 不存在", file=sys.stderr)
    raise SystemExit(1)


def cmd_fbk_add(proposer: str, task_id: str, fb_type: str, stage: str,
                summary: str, alt: str = "", impact: str = "",
                question: str = "") -> None:
    """创建反馈卡（open）；类型/节点/必附字段/长度校验失败抛 ValueError。"""
    if stage not in STAGES:
        raise ValueError(f"节点非法：{stage}（应为 {'/'.join(STAGES)}）")
    check_fbk_limits(summary=summary, alt=alt, impact=impact,
                     question=question)
    check_fbk_required(fb_type, alt=alt, impact=impact, question=question)
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    check_quota(task_id, stage, cards)  # TASK-0033 分节点配额硬门禁
    fbk_id = _next_fbk_id(cards)
    content = render_fbk(fbk_id, proposer=proposer, task_id=task_id,
                         fb_type=fb_type, stage=stage, summary=summary,
                         alt=alt, impact=impact, question=question)
    resp = _request("POST", "/memories",
                    {"content": content, "tags": [TAG]})
    print(f"已创建 {fbk_id}（{fb_type} → {task_id}）→ 记录 {resp['id']}")


def cmd_fbk_list() -> None:
    """一行一反馈卡：FBK-0001 open TASK-0026 objection precheck 摘要。"""
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    if not cards:
        print("无反馈卡")
        return
    for card in cards:
        try:
            h = parse_fbk_header(card["content"])
        except ValueError:
            print(f"[警告] 记录 {card.get('id', '?')} 首行非法，已跳过")
            continue
        result = parse_fbk_result(card["content"])
        # 摘要取"摘要："行，超宽截断显示
        summary = next((l[len("摘要："):] for l in
                        card["content"].split("\n")
                        if l.startswith("摘要：")), "")
        print(f"{h['fbk_id']} {result} {h['task_id']} {h['fb_type']} "
              f"{h['stage']} {_fmt_time(card.get('updated_at', ''))} {summary}")


def cmd_fbk_show(fbk_id: str) -> None:
    """打印整张反馈卡（核验/回溯用）。"""
    card, _ = _find_fbk(fbk_id)
    print(card["content"])


def cmd_fbk_decide(fbk_id: str, action: str, note: str = "",
                    decider: str = "coordinator") -> None:
    """裁决反馈卡（open → accepted/rejected），结论级写 comm:feedback（≤300 字符）。

    仅 open 状态可流转（check_result_transition 校验）；非 open 抛 ValueError。
    裁决后 PATCH 更新反馈卡结果字段 + POST 写 comm:feedback 归档留痕。
    """
    card, h = _find_fbk(fbk_id)
    current = parse_fbk_result(card["content"])
    check_result_transition(current, action)
    # 替换"结果："行为新状态
    lines = card["content"].split("\n")
    new_lines = [f"结果：{action}" if l.startswith("结果：") else l for l in lines]
    new_content = "\n".join(new_lines)
    _request("PATCH", f"/memories/{card['id']}", {"content": new_content})
    # comm:feedback 结论级归档（≤300 字符，不贴过程流水）
    comm = (f"[comm:feedback] {decider} 裁决 {fbk_id}"
            f"（{h['fb_type']} → {h['task_id']}，{h['stage']}）：{action}")
    if note:
        comm += f"。{note[:200]}"
    if len(comm) > 300:
        comm = comm[:297] + "..."
    _request("POST", "/memories",
             {"content": comm, "tags": ["comm:feedback"], "source": decider})
    print(f"已裁决 {fbk_id} → {action}（comm:feedback 已归档）")
