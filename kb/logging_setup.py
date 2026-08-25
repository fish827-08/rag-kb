"""日志体系装配（N17）：setup_logging 一次调用配置 kb.* 日志树。

设计文档 docs/superpowers/specs/2026-08-24-logging-design.md 第 6 节：
- 控制台 handler：精简格式（asctime 只保留时分秒，省略日期部分）；
- RotatingFileHandler：完整格式（含模块名），按 KB_LOG_MAX_BYTES 轮转，
  保留 KB_LOG_BACKUP_COUNT 个备份；
- 格式统一 ``%(asctime)s | %(levelname)s | %(name)s | %(message)s``。

说明：handler 挂在 "kb" 命名日志树根上（kb.serve / kb.api / kb.watcher
等子 logger 自动继承级别与 handler），不碰进程根 logger——避免第三方库
（urllib3 / watchdog 等）日志混入 kb.log。可重复调用（create_app 每次
装配都会走到）：先关闭并摘除旧 handler 再挂新的，规避 Windows 文件
句柄占用导致轮转失败。
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 统一格式（设计第 6 节）：时间 | 级别 | 模块 | 消息
_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
# 控制台版 datefmt：只保留时分秒（省略日期部分保持简洁）
_CONSOLE_DATEFMT = "%H:%M:%S"

# KB_LOG_LEVEL 合法级别表；非法值回退 INFO（容错不阻断启动）
_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
           "WARNING": logging.WARNING, "ERROR": logging.ERROR,
           "CRITICAL": logging.CRITICAL}


def setup_logging(settings=None) -> logging.Logger:
    """装配 kb 日志树：控制台 + 轮转文件双 handler，返回 "kb" logger。

    settings 缺省时读全局配置单例（get_settings）。日志目录不存在时
    自动创建；级别名大小写不敏感，非法值回退 INFO。
    """
    if settings is None:
        from kb.config import get_settings
        settings = get_settings()

    level = _LEVELS.get(str(settings.log_level).upper(), logging.INFO)
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("kb")
    root.setLevel(level)
    # 关闭并摘除旧 handler（测试/重复装配场景），防 Windows 句柄占用
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()

    console = logging.StreamHandler()  # 默认 stderr
    console.setFormatter(logging.Formatter(_FORMAT, datefmt=_CONSOLE_DATEFMT))
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "kb.log", maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)
    return root