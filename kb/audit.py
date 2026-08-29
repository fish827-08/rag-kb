"""记忆治理操作审计日志（N23b/TASK-0073，A3 spec §5）。

结构化审计日志闭环：每次去重拦截/衰减降权/新鲜度加权等治理操作写一条
JSON 行日志到 KB_LOG_DIR/governance-audit.log，按天轮转（TimedRotatingFileHandler）。

设计原则：
- 独立 logger "kb.audit"，propagate=False，不混入 kb.log；
- 纯 JSON 行格式：每行一个对象（timestamp/operation/record_id/namespace/detail）；
- 不阻塞主流程：审计失败记 WARNING 到 "kb" logger，不抛异常；
- 懒初始化：第一次调用 log_governance_event 时配置 handler，避免导入时创建文件。
"""
import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# 审计 logger 单例（懒初始化）
_audit_logger: logging.Logger | None = None
_audit_logger_configured = False


def _get_audit_logger(log_dir: Path | None = None) -> logging.Logger:
    """获取或初始化审计 logger（懒初始化，按天轮转）。

    log_dir 缺省时读全局配置单例；目录不存在自动创建。
    """
    global _audit_logger, _audit_logger_configured
    if _audit_logger_configured and _audit_logger is not None:
        return _audit_logger

    if log_dir is None:
        from kb.config import get_settings
        log_dir = Path(get_settings().log_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("kb.audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 不向上传播到 "kb"，避免混入 kb.log

    # 关闭并摘除旧 handler（测试/重复装配场景）
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    # 按天轮转：每天午夜切分，保留 30 天备份
    handler = TimedRotatingFileHandler(
        log_dir / "governance-audit.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8-sig",  # 带 BOM：Windows 记事本/旧 GBK 工具可直接打开查看中文
    )
    # 纯 JSON 行格式：直接输出 message（已是 JSON 字符串）
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    _audit_logger = logger
    _audit_logger_configured = True
    return logger


def reset_audit_logger() -> None:
    """重置审计 logger 单例（测试用，清除已配置状态与 handler）。"""
    global _audit_logger, _audit_logger_configured
    if _audit_logger is not None:
        for h in list(_audit_logger.handlers):
            _audit_logger.removeHandler(h)
            h.close()
    _audit_logger = None
    _audit_logger_configured = False


def log_governance_event(operation: str, record_id: str,
                          detail: dict | None = None,
                          namespace: str = "default",
                          log_dir: Path | None = None) -> None:
    """记录一条治理操作审计日志（JSON 行）。

    参数：
        operation: 操作类型（如 dedup_blocked / decay_applied / freshness_applied）
        record_id: 目标记录 ID
        detail: 详情 dict（如 {"similarity": 0.92, "duplicate_of": "xxx"}）
        namespace: 命名空间，默认 "default"
        log_dir: 日志目录（测试用，缺省读全局配置）

    不阻塞主流程：任何异常（磁盘满/权限等）记 WARNING 到 "kb" logger，不抛出。
    """
    try:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "record_id": record_id,
            "namespace": namespace,
            "detail": detail or {},
        }
        line = json.dumps(event, ensure_ascii=False)
        logger = _get_audit_logger(log_dir)
        logger.info(line)
    except Exception as exc:
        # 审计失败不阻塞主流程，记 WARNING 到 kb logger
        kb_logger = logging.getLogger("kb")
        kb_logger.warning("审计日志写入失败 operation=%s record_id=%s error=%s",
                           operation, record_id, exc)


# ---- Agent 存取审计（A 节点 spec：2026-08-29）----
# 按 Agent 分类：在 log_dir/agent-audit/ 下，每个 (client, project, agent_id) 一个独立文件
#   <客户端名>__<项目名>_<任务名>.log（如 TraeWork__kb_agent_TASK-0076.log / Claude Code__worker-1.log），
# 不再把所有 Agent 写进一个 access-audit.log（人读混乱）。JSON 行，按天轮转 30 天。
# 身份不重复落行内：client/project/agent 由文件名承载（更新任务名=重命名文件即可），
# 查询侧从文件名解析补回。敏感红线：content/query 只记前 _ACCESS_SNIPPET 字符摘要，不落全文。
_ACCESS_SNIPPET = 50
_ACCESS_DIR = "agent-audit"
# 文件名非法字符清理白名单：仅保留字母数字、中文、_、-、·、.、空格（其余替换为 _）
_SAFE_RE = re.compile(r"[^\w\u4e00-\u9fff.\-· ]")
# 两段分隔符：client__project.log（project 可空→default）
_SEP = "__"
_access_loggers: dict[tuple[str, str], logging.Logger] = {}


def _clean_name(s: str | None) -> str:
    """清理命名段：去掉路径非法字符、折叠连续下划线（保证 `__` 分隔可解析）。"""
    s = _SAFE_RE.sub("_", (s or "").strip())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or "unknown"


def _agent_file_name(client: str, project: str) -> str:
    """生成按 (client, project) 分类的审计文件名。

    v2（2026-08-30）：<客户端>__<项目>.log；project 为空 → <客户端>__default.log。
    身份（client/project）由文件名承载，行内不重复记录——项目更名=改连接声明的 project，
    重命名对应文件即可。清理路径非法字符→_、折叠连续下划线，避免跨目录注入。
    English: Build the per-(client, project) audit file name: <client>__<project>.log, or
    <client>__default.log when project is empty. Identity lives in the file name only;
    renaming a project = renaming the file. Sanitizes path-illegal characters to prevent
    directory injection."""
    client_c = _clean_name(client)
    if not client_c or client_c == "unknown":
        client_c = "default"
    project_c = _clean_name(project) if project else "default"
    if not project_c or project_c == "unknown":
        project_c = "default"
    return f"{client_c}{_SEP}{project_c}.log"


def parse_agent_file_name(filename: str) -> dict:
    """从审计文件名解析 (client, project)；轮转后缀（.log.YYYY-MM-DD）自动剥离。

    v2 文件名规则：<client>__<project>.log（两段式）。project 段 "default" 表示默认桶。
    兼容旧三段式 <client>__<project>__<agent>.log 与两段式 <client>__<agent>.log
    （旧数据零迁移；解析只返回 client/project，agent 保留供旧查询展示）。
    English: Parse (client, project) from an audit file name; rotation suffixes are stripped.
    v2 naming: <client>__<project>.log (two segments); "default" project = default bucket.
    Old three-segment and two-segment agent-based names are still parsed for zero-migration reads."""
    name = Path(filename).name
    for suffix in (".log",):
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            break
    else:
        base = name
    # 去掉可能的按天轮转后缀 .YYYY-MM-DD
    parts = base.split(".")
    stem = parts[0] if parts else base
    segs = stem.split(_SEP)
    client = segs[0] if len(segs) > 0 else "unknown"
    project = segs[1] if len(segs) > 1 else "default"
    agent = segs[-1] if len(segs) > 2 else ""
    return {"client": client, "project": project, "agent": agent}


def _get_access_logger(client: str, project: str,
                       log_dir: Path | None = None) -> logging.Logger:
    """按 (client, project) 取独立存取审计 logger（懒建，按天轮转 30 天）。

    English: Get the per-(client, project) access-audit logger (lazy, daily rotation, 30 backups)."""
    key = (client or "default", project or "")
    if key in _access_loggers:
        return _access_loggers[key]
    if log_dir is None:
        from kb.config import get_settings
        log_dir = Path(get_settings().log_dir)
    log_dir = Path(log_dir)
    agent_dir = log_dir / _ACCESS_DIR
    agent_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"kb.audit.access.{key[0]}__{key[1] or 'default'}")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 不向上传播到 "kb"，避免混入 kb.log
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    handler = TimedRotatingFileHandler(
        agent_dir / _agent_file_name(client, project), when="midnight",
        backupCount=30, encoding="utf-8-sig")  # 带 BOM：Windows 记事本/旧 GBK 工具可直接查看中文
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    _access_loggers[key] = logger
    return logger


def reset_access_logger() -> None:
    """重置存取审计 logger 缓存（测试用，清除全部已建 handler）。"""
    global _access_loggers
    for logger in _access_loggers.values():
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
    _access_loggers = {}


def _snippet(text: str | None) -> str:
    """内容/query 摘要：空→""，其余截取前 50 字符（敏感红线，不落全文）。"""
    if not text:
        return ""
    return text[: _ACCESS_SNIPPET]


def log_access_event(agent_id: str, action: str, record_id: str | None = None,
                     type: str | None = None, content: str | None = None,
                     query: str | None = None, hits: int | None = None,
                     namespace: str = "default", source: str | None = None,
                     client: str | None = None, project: str | None = None,
                     log_dir: Path | None = None) -> None:
    """记录一条 Agent 存取审计（JSON 行）：谁在何时从哪个客户端做了什么。

    按 (client, project, agent_id) 分文件落盘到
    log_dir/agent-audit/<客户端>__<项目>_<任务名>.log；
    client/project/agent 由文件名承载（不重复落行内），供查询侧解析补回。
    action: write / search / read / update / delete / ask / ingest。
    content/query 只记前 50 字符摘要（敏感红线）。审计失败不阻塞主流程。
    English: Record one Agent access-audit line, bucketed into a per-agent file
    <client>__<project>_<agent>.log; identity lives in the file name only.
    """
    if not agent_id:
        agent_id = "default"
    try:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "type": type,
            "record_id": record_id,
            "namespace": namespace,
        }
        if source is not None:
            event["source"] = source
        if content is not None:
            event["content"] = _snippet(content)
        if query is not None:
            event["query"] = _snippet(query)
        if hits is not None:
            event["hits"] = hits
        line = json.dumps(event, ensure_ascii=False)
        _get_access_logger(client or "default", project or "", log_dir).info(line)
    except Exception as exc:
        # 审计失败不阻塞主流程，记 WARNING 到 kb logger
        kb_logger = logging.getLogger("kb")
        kb_logger.warning("存取审计写入失败 agent=%s action=%s error=%s",
                          agent_id, action, exc)
