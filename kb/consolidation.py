"""智能层 consolidation 基础模块（A3 智能层 spec，N23c/TASK-0076）。

职责（基础框架，提示词按 TASK-0074 spec §4 落地）：
- MergeResult：单对记录的归并决策结果模型
- detect_conflict：启发式冲突检测纯函数（spec §3.1，无 LLM 可用）
- consolidate_pair：LLM 归并决策封装（spec §4，复用 kb/llm.py，仅本地禁云端）
- consolidate_dry_run：dry-run 骨架（spec §7 preview 语义，只返回建议不写库）

安全护栏（spec §9）：默认关（consolidation_enabled=False 零行为变化）；
LLM 输出校验失败 / 置信度低于门槛 → 降级 human；LLM 不可用 → 降级 human。
零新增依赖；不触碰 service/api/retriever/cli/audit。
"""
import json
import re
from datetime import datetime

from pydantic import BaseModel, Field

from kb.config import Settings
from kb.llm import LLMClient, LLMError
from kb.models import Record


class ConsolidationError(Exception):
    """consolidation 不可用（未启用 / 配置非法等）。"""


class MergeResult(BaseModel):
    """单对记录的归并决策结果（spec §4.3 输出结构）。

    - action：merge（合并）/ independent（保持独立）/ human（人工确认）
    - merged_content：merge 时的合并后内容；其余决策为 None
    - confidence：LLM 置信度 0.0-1.0；低于门槛会被强制升级 human
    - conflict_type：预筛冲突标记（attribute_conflict / temporal / none）
    """

    action: str
    merged_content: str | None = None
    confidence: float = 0.0
    reason: str = ""
    conflict_type: str = "none"


# 决策枚举（spec §4.1）
_ACTIONS = ("merge", "independent", "human")

# 属性模式：`关键词[:：] 值`（值取到首个中文标点/换行为止，spec §3.1）
_KEY_VALUE_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9_\-]{1,16})\s*[:：]\s*([^，。；\n]+)")
# 断定词（spec §3.1 陈述语气）
_ASSERTION_WORDS = ("是", "为", "用", "使用", "采用")
# 时间矛盾的判定阈值（spec §3.1：updated_at 差距 >30 天）
_TEMPORAL_GAP_DAYS = 30.0
# 同主题判定：共享字符 bigram 数下限（关键词重叠的轻量近似）
_TOPIC_OVERLAP_BIGRAMS = 3


def _bigrams(text: str) -> set[str]:
    """文本的字符 bigram 集合（去空白后滑动窗口，用于同主题近似判定）。"""
    cleaned = re.sub(r"\s+", "", text)
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def _extract_key_values(content: str) -> dict[str, str]:
    """从内容提取 key:value 属性对（spec §3.1 属性模式匹配）。

    同一 key 多次出现取首个（以最早表述为准，后续由 LLM 综合判断）。
    """
    kv: dict[str, str] = {}
    for m in _KEY_VALUE_RE.finditer(content):
        key = m.group(1).lower()
        if key not in kv:
            kv[key] = m.group(2).strip()
    return kv


def detect_conflict(record_a: Record, record_b: Record) -> str:
    """启发式冲突检测纯函数（spec §3.1，无 LLM 可用）。

    返回 conflict_type：
    - attribute_conflict：两记录提取到同 key 不同值（值无包含关系）
    - temporal：同主题（bigram 重叠≥3）+ updated_at 差>30 天 + 含断定词
    - none：未检测到明显冲突（仍可送 LLM 判断是否互补合并）

    预筛只做标记不做决策，最终决策由 LLM 做出（spec §3.1）。
    """
    kv_a = _extract_key_values(record_a.content)
    kv_b = _extract_key_values(record_b.content)
    # ① 同 key 不同值（值包含关系视为兼容，如 Python vs Python 3.10）
    for key, va in kv_a.items():
        if key in kv_b:
            vb = kv_b[key]
            if va == vb or va in vb or vb in va:
                continue  # 同值或兼容细化
            return "attribute_conflict"
    # ② 时间矛盾：同主题 + 时间差>30天 + 陈述语气
    overlap = _bigrams(record_a.content) & _bigrams(record_b.content)
    if len(overlap) >= _TOPIC_OVERLAP_BIGRAMS and _has_assertion(
            record_a.content, record_b.content):
        try:
            ta = datetime.fromisoformat(record_a.updated_at)
            tb = datetime.fromisoformat(record_b.updated_at)
        except (ValueError, TypeError):
            return "none"  # 日期无效不做时间判断
        if abs((ta - tb).total_seconds()) > _TEMPORAL_GAP_DAYS * 86400:
            return "temporal"
    return "none"


def _has_assertion(*contents: str) -> bool:
    """任一内容含断定词（是/为/用等，spec §3.1 陈述语气）。"""
    return any(w in c for c in contents for w in _ASSERTION_WORDS)


# ---- LLM 提示词（TASK-0074 spec §4.1/§4.2 原文落地） ----

_SYSTEM_PROMPT = """你是记忆归并代理（Memory Consolidation Agent）。
任务：给定 2-5 条记忆记录，判断它们是否应合并为一条更完整的记忆，或保持独立，或需要人工确认。

决策规则：
- merge：记录表达同一事实/同一主题互补信息/同一属性新值明确替代旧值 → 合并为一条
- independent：记录主题不同/角度不同且各自有独立检索价值 → 保持独立
- human：信息矛盾且无法判断哪个正确/合并可能丢失重要信息/置信度低 → 人工确认

合并要求：
- merged_content 保留所有有价值信息，去重，语言简洁
- 若同一属性有新旧值，保留较新值（updated_at 较晚者），除非旧值明显更准确
- 不添加记录中不存在的信息
- 不删除可能有独立检索价值的细节

输出严格 JSON，不输出任何其他文字：
{"decision":"merge|independent|human","reason":"简短原因","merged_content":"合并后内容或null","conflict_type":"attribute_conflict|temporal|none","confidence":0.0-1.0}"""


def _build_user_prompt(record_a: Record, record_b: Record,
                       conflict_type: str) -> str:
    """用户提示词模板（spec §4.2）：记录列表 + 预筛冲突标记。"""

    def _line(r: Record) -> str:
        return (f"[id={r.id}] created_at={r.created_at} "
                f"updated_at={r.updated_at} tags={r.tags} source={r.source}\n"
                f"内容：{r.content}")

    return (f"记忆记录列表：\n{_line(record_a)}\n{_line(record_b)}\n\n"
            f"预筛冲突标记：{conflict_type}"
            f"（若为 none 表示未检测到明显冲突）\n\n"
            f"请按系统提示规则决策，输出严格 JSON。")


def _parse_llm_output(raw: str, threshold: float) -> MergeResult:
    """解析并校验 LLM 输出（spec §4.3）。

    - JSON 解析失败 / decision 不在枚举 / merge 时 merged_content 空 → 降级 human
    - confidence < threshold → 强制升级 human
    """
    fallback = MergeResult(
        action="human", confidence=0.0,
        reason="LLM 输出校验失败，降级人工确认")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return fallback
    if not isinstance(data, dict):
        return fallback
    action = data.get("decision")
    if action not in _ACTIONS:
        return fallback
    try:
        confidence = float(data.get("confidence", 0.0))
    except (ValueError, TypeError):
        return fallback
    merged = data.get("merged_content") or None
    # merge 决策必须带非空合并内容
    if action == "merge" and not (merged and merged.strip()):
        return fallback
    # 置信度门槛：低于强制 human（spec §4.3）
    if confidence < threshold:
        return MergeResult(
            action="human", confidence=confidence,
            reason=f"置信度 {confidence:.2f} 低于门槛 {threshold}，强制人工确认",
            conflict_type=data.get("conflict_type", "none"))
    return MergeResult(
        action=action,
        merged_content=merged if action == "merge" else None,
        confidence=confidence,
        reason=str(data.get("reason", "")),
        conflict_type=data.get("conflict_type", "none"))


def consolidate_pair(record_a: Record, record_b: Record,
                     client: LLMClient,
                     settings: Settings | None = None) -> MergeResult:
    """LLM 归并决策封装（spec §4/§6）：对一对记录调本地 LLM 返回 MergeResult。

    - 复用 kb/llm.py LLMClient.chat，prefer="local"（智能层禁止云端外传）
    - 预筛冲突标记（detect_conflict）作为用户提示上下文
    - LLM 不可用/调用失败 → 该对降级 human（不抛异常，spec §6）
    """
    threshold = (settings or Settings()).consolidation_confidence_threshold
    conflict_type = detect_conflict(record_a, record_b)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(
            record_a, record_b, conflict_type)},
    ]
    try:
        raw = client.chat(messages, prefer="local")
    except LLMError as exc:
        return MergeResult(
            action="human", confidence=0.0,
            reason=f"LLM 不可用（{exc}），降级人工确认",
            conflict_type=conflict_type)
    return _parse_llm_output(raw, threshold)


def consolidate_dry_run(pairs: list[tuple[Record, Record]],
                         client: LLMClient,
                         settings: Settings) -> list[MergeResult]:
    """dry-run 归并建议（spec §7 preview 语义）：逐对返回建议，不写库。

    - consolidation_enabled=False 时抛 ConsolidationError（默认关，零行为变化）
    - 单对失败（LLM 不可用/输出非法）降级 human，不阻塞整批（spec §6）
    - 纯计算无副作用：不触碰存储层，apply/rollback 由后续节点实现
    """
    if not settings.consolidation_enabled:
        raise ConsolidationError(
            "consolidation 未启用（需 KB_CONSOLIDATION_ENABLED=true）")
    return [consolidate_pair(a, b, client, settings)
            for a, b in pairs]
