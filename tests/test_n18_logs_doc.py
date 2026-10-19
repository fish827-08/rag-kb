"""N18 日志端点文档化（TASK-0027）。

- USER_GUIDE.md 含日志端点小节（/api/v1/logs 与 /logs/events，含 curl 示例）
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUIDE = HERE.parent / "docs" / "USER_GUIDE.md"


def _guide() -> str:
    """读取 USER_GUIDE.md；不存在直接断言失败（红灯）。"""
    assert GUIDE.is_file(), f"产物缺失：{GUIDE}"
    return GUIDE.read_text(encoding="utf-8")


def test_user_guide_含日志端点小节():
    """USER_GUIDE.md 补日志端点用法：两个端点路径 + curl 示例。"""
    g = _guide()
    assert "/api/v1/logs" in g
    assert "/logs/events" in g
    assert "curl" in g
    # 含查询参数说明（limit/level/event 至少其一）
    assert any(k in g for k in ("limit", "level", "event"))
