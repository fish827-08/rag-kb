"""双语消息（i18n）与 MCP 接入规约。

- `KB_LANG`：服务端错误/校验消息语言（zh/en/auto，默认 auto）
- `KB_MEMORY_LANG`：记忆书写语言（auto/zh/en/...，默认 auto = 操作系统默认语言）
- 自定义记忆规则：`~/.kb/rules.md` + `<data_dir>/rules.md`，截断后注入 MCP instructions

English: Bilingual messages plus the MCP instructions contract.
- KB_LANG: language of server error/validation messages (zh/en/auto).
- KB_MEMORY_LANG: language used when writing memories (auto = OS default language).
- Custom memory rules: ~/.kb/rules.md plus <data_dir>/rules.md, truncated and injected
  into the MCP instructions.
"""
import locale
from pathlib import Path

from kb.config import get_settings


def detect_lang() -> str:
    """检测系统语言：中文系统→zh，其他→en；KB_LANG=zh/en 时直接采用。
    English: Detect system language: Chinese systems→zh, otherwise→en; KB_LANG=zh/en wins."""
    try:
        cfg_lang = get_settings().lang
        if cfg_lang in ("zh", "en"):
            return cfg_lang
    except Exception:
        pass
    # auto：检测系统 locale
    try:
        code, _ = locale.getdefaultlocale()
        if code and code.lower().startswith("zh"):
            return "zh"
    except Exception:
        pass
    return "en"


# 常见语言代码 → 语言名（供 MCP instructions 明确要求 agent 使用的书写语言）
_LANG_NAMES: dict[str, str] = {
    "zh": "Chinese (简体中文)",
    "en": "English",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "de": "German (Deutsch)",
    "fr": "French (Français)",
    "es": "Spanish (Español)",
    "pt": "Portuguese (Português)",
    "it": "Italian (Italiano)",
    "ru": "Russian (Русский)",
    "ar": "Arabic (العربية)",
    "hi": "Hindi (हिन्दी)",
    "nl": "Dutch (Nederlands)",
    "pl": "Polish (Polski)",
    "tr": "Turkish (Türkçe)",
}


def system_language_code() -> str:
    """操作系统默认语言代码（如 zh-CN / fr-FR）；检测失败回退 en。
    English: OS default language code (e.g. zh-CN / fr-FR); falls back to en."""
    code = ""
    try:
        code = locale.getlocale()[0] or ""
    except Exception:
        pass
    if not code:
        try:
            code, _ = locale.getdefaultlocale()
        except Exception:
            pass
    return (code or "en").replace("_", "-")


def memory_lang() -> str:
    """记忆书写语言名：KB_MEMORY_LANG 显式指定优先，auto 按操作系统语言。
    English: Display name of the memory-writing language: KB_MEMORY_LANG wins;
    auto follows the OS language."""
    try:
        cfg = (get_settings().memory_lang or "auto").strip().lower()
    except Exception:
        cfg = "auto"
    code = system_language_code() if cfg == "auto" else cfg
    base = code.replace("_", "-").split("-")[0].lower()
    return _LANG_NAMES.get(base, code or "English")


# 消息表：key → {zh, en}
_MESSAGES: dict[str, dict[str, str]] = {
    # 校验类（service.validate_* 供 MCP/REST 共用）
    "agent_id_required": {
        "zh": "agent_id 必填且必须是有意义的任务名（如 TASK-0076 / worker-1），不能用 default/unknown 等占位",
        "en": "agent_id is required and must be a meaningful task name (e.g. TASK-0076 / worker-1); placeholders like default/unknown are rejected",
    },
    "agent_id_format": {
        "zh": "agent_id 格式非法：仅允许字母/数字/中文/下划线/连字符，1~64 字符（当前：{v!r}）",
        "en": "invalid agent_id: only letters/digits/CJK/underscore/hyphen allowed, 1~64 chars (got {v!r})",
    },
    "client_format": {
        "zh": "client 格式非法：仅允许字母/数字/中文/下划线/连字符/空格/点，1~64 字符（当前：{v!r}；不传则自动识别）",
        "en": "invalid client: only letters/digits/CJK/underscore/hyphen/space/dot allowed, 1~64 chars (got {v!r}; omit for auto-detect)",
    },
    "project_format": {
        "zh": "project 格式非法：仅允许字母/数字/中文/下划线/连字符，1~64 字符且不能是系统保留名（当前：{v!r}）",
        "en": "invalid project: only letters/digits/CJK/underscore/hyphen allowed, 1~64 chars, reserved OS names rejected (got {v!r})",
    },
    # 业务错误（MCP 工具返回的错误消息）
    "content_blank": {
        "zh": "content 不能为空或纯空白",
        "en": "content must not be empty or blank",
    },
    "top_k_min": {
        "zh": "top_k 必须 >= 1",
        "en": "top_k must be >= 1",
    },
    "record_none": {
        "zh": "记录不存在或无权访问",
        "en": "record not found or forbidden",
    },
    "record_forbidden": {
        "zh": "无权访问该记忆（非当前客户端/项目归属）",
        "en": "memory not accessible (not owned by this client/project)",
    },
    "llm_disabled": {
        "zh": "未检测到可用的 LLM：请在 .env 配置 KB_LLM_MODE=local（Ollama）或 KB_LLM_API_KEY 启用云端（任意 OpenAI 兼容服务商）",
        "en": "no LLM available: configure KB_LLM_MODE=local (Ollama) or KB_LLM_API_KEY in .env for a cloud provider (any OpenAI-compatible endpoint)",
    },
    "unsupported_format": {
        "zh": "不支持的文件格式",
        "en": "unsupported file format",
    },
    "web_fetch_failed": {
        "zh": "网页抓取/正文提取失败",
        "en": "webpage fetch/body extraction failed",
    },
    "file_not_found": {
        "zh": "文件不存在",
        "en": "file not found",
    },
    "bad_request": {
        "zh": "请求参数不合法",
        "en": "invalid request parameters",
    },
}


def t(key: str, **fmt) -> str:
    """按当前语言返回 key 的消息；未知 key 回退英文（或 key 本身）。
    English: Return the message for key in the current language; unknown keys fall back to English."""
    msg = _MESSAGES.get(key, {})
    text = msg.get(detect_lang()) or msg.get("en") or key
    try:
        return text.format(**fmt)
    except Exception:
        return text


def custom_rules() -> str:
    """读取用户自定义记忆规则：全局 ~/.kb/rules.md 在前，本安装 rules.md 在后，
    拼接后按 KB_RULES_MAX_CHARS 截断（默认 4000）。
    English: Load user-defined memory rules: the global ~/.kb/rules.md first, then
    this installation's rules.md, joined and truncated to KB_RULES_MAX_CHARS (4000)."""
    try:
        settings = get_settings()
    except Exception:
        return ""
    paths = [Path.home() / ".kb" / "rules.md", settings.rules_file]
    parts: list[str] = []
    for path in paths:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
        except OSError:
            continue
    limit = max(0, int(settings.rules_max_chars or 0))
    return "\n\n".join(parts)[:limit]


# ---- MCP server instructions（v2.1 接入规约，2026-10-20）----
# 跨客户端全局提示词载体：任何客户端挂载 MCP 后自动注入 AI 上下文；
# 语言按 KB_MEMORY_LANG/系统语言确定，并注入用户自定义记忆规则。
# 核心：不要求先做健康探测、反馈极简、project 隔离项目记忆、全局记忆共享。
_MCP_INSTRUCTIONS: dict[str, str] = {
    "zh": """你是连接到 kb（本地 Agent 记忆与知识服务）的助手。kb 是本机常驻服务，你现在就能直接调用它的 MCP 工具写入/检索记忆与知识，默认情况下服务在正常运行，无需也不要求事前探测（health check）——直接调用工具即可，一切正常时请继续工作。

【身份与项目】
- client 由系统自动识别，你不需要自报身份，也不要展示。
- project 用于区分不同项目的记忆：当用户明确在某个项目上下文中工作（提到项目名、仓库名或项目目录）时，将该项目的稳定短名传给 project 参数；同一项目始终用同一个名字。
- 项目记忆只在该 project 下可见；不传 project 写入的是全局记忆（所有项目可见），适合用户偏好、通用事实与跨项目约定。不确定项目名时不要猜测：写全局或先问用户。
- 检索时传 project 会同时覆盖该项目与全局记忆；不传只看全局。

【记忆语言】
- 写入记忆一律使用 {memory_lang}；专有名词、代码、命令、路径保持原文，不要翻译。

【何时写入记忆（write_memory，内容精炼 1~3 句）】
1. 用户偏好：主题、工具链、代码风格、命名习惯、沟通方式。
2. 项目决策：已拍板的选型/架构取舍/约定，写清背景与结论。
3. 事实与约定：关键路径、命令、依赖、环境细节、版本约束等可复用事实。
4. 敏感约束：用户明示的不可做红线、安全/隐私要求。
5. 任务进度：复杂任务的关键进展与下一步，便于跨会话接力。
不写：纯寒暄、临时计算、可由代码/文档直接查到的实现细节。
- 写前先查重：先 search_memory，已有同类则 update_memory 覆盖，不新增重复。
- 敏感信息不入库：密钥、凭据、身份证号等严禁写入。

【何时检索（search_memory）】
- 任务开始、回答涉及历史决策/用户偏好、跨会话接力时，先主动检索召回，query 用自然语言描述语义。
- 两条记忆关系明确（同主题、前后替代、依赖、从属、矛盾）时，用 link_memory 建立关联；关联要少而准，不要把所有记忆都连起来。list_links 可查看一跳邻居，检索会自动带出关联记忆（结果中的 via 字段标明来源）。

【对用户反馈（重要，极简）】
- 写入/更新成功：只轻提一句，如"已记住你的偏好"；失败才说明原因（如"没记：与已有记忆重复/内容敏感/格式不支持"）。
- 不要展示：健康检查过程、工具名、记录 ID、JSON、检索命中详情。
- 若 MCP 工具调用报连接失败（服务确实不可达），简提"kb 服务未启动，需先运行 python -m kb serve"，不要臆造结果。""",
    "en": """You are connected to kb, a local Agent memory & knowledge service. kb is a resident local service whose MCP tools you can call right now to write/retrieve memories and knowledge. It is normally running; no health check or pre-probing is required — just call the tools directly and keep working when things are normal.

[Identity & projects]
- The client is auto-detected; never self-report or display it.
- project separates memories between projects: when the user is clearly working in a project context (project name, repository name, or project directory), pass that project's stable short name as project; always use the same name for the same project.
- Project memories are visible only under that project. Without project, writes go to global memory (visible to every project) — suitable for user preferences, general facts, and cross-project conventions. Never guess a project name: write globally or ask the user.
- Searching with project covers that project plus global memory; without it, only global memory.

[Memory language]
- Always write memories in {memory_lang}; keep proper nouns, code, commands, and paths unchanged — do not translate them.

[When to write memory (write_memory, 1-3 concise sentences)]
1. User preferences: themes, toolchain, code style, naming habits, communication style.
2. Project decisions: settled tech choices, architecture trade-offs, conventions — with context and conclusion.
3. Facts & conventions: key paths, commands, dependencies, environment details, version constraints.
4. Sensitive constraints: explicit hard rules ("do not"), security/privacy requirements.
5. Task progress: key progress of complex tasks and next steps, for cross-session handoff.
Do NOT write: chit-chat, transient computation, implementation details directly findable in code/docs.
- Dedupe before writing: search_memory first; if similar content exists, update_memory to overwrite instead of adding duplicates.
- Never store sensitive data: keys, credentials, ID numbers are forbidden.

[When to search (search_memory)]
- Proactively recall at task start, when an answer involves past decisions/preferences, or on cross-session handoff; ask in natural language describing the semantics.
- When two memories have a clear relationship (same topic, supersession, dependency, part-of, contradiction), link them with link_memory; keep links few and precise rather than connecting everything. list_links shows one-hop neighbors, and searches automatically surface linked memories (the via field marks the source).

[Feedback to user (important — keep it minimal)]
- On successful write/update: mention it briefly, e.g. "Noted your preference."
- On failure, state the reason (e.g. "Not saved: duplicate of an existing memory / sensitive content / unsupported format").
- Do NOT show: health-check process, tool names, record IDs, JSON, retrieval hit details.
- Only if an MCP tool call fails with a connection error (service truly unreachable), briefly say "kb service is not running; start it with python -m kb serve". Never fabricate results.""",
}


def mcp_instructions() -> str:
    """按当前系统语言返回 MCP instructions，并注入记忆语言与用户自定义规则。
    English: Return the MCP instructions for the current system language, with the
    memory language and user-defined rules injected."""
    lang = detect_lang()
    text = (_MCP_INSTRUCTIONS.get(lang) or _MCP_INSTRUCTIONS["en"]).format(
        memory_lang=memory_lang())
    rules = custom_rules()
    if not rules:
        return text
    if lang == "zh":
        return text + "\n\n【用户自定义记忆规则（优先遵守）】\n" + rules
    return text + "\n\n[User-defined memory rules (take precedence)]\n" + rules
