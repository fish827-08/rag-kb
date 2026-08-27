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
        encoding="utf-8",
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
