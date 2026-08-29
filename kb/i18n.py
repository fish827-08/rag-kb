"""双语消息（i18n）：服务端错误/校验消息按系统语言环境输出（B4 节点 spec 2.6）。

- `KB_LANG` 配置：zh / en / auto（默认 auto = 检测系统 locale，中文系统用中文，否则英文）
- 工具描述/错误消息收敛到这里，避免双语长描述 double token 成本（MCP 端按语言选一套）
- 使用：`from kb.i18n import t; t("content_blank")` → 按当前系统语言返回消息
"""
import locale

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
        "zh": "project 格式非法：仅允许字母/数字/中文/下划线/连字符，1~64 字符（当前：{v!r}）",
        "en": "invalid project: only letters/digits/CJK/underscore/hyphen allowed, 1~64 chars (got {v!r})",
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