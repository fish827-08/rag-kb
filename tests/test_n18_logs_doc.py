"""N18 日志端点文档化 + 看板日志区（TASK-0027）。

- USER_GUIDE.md 含日志端点小节（/api/v1/logs 与 /logs/events，含 curl 示例）
- 看板 index.html 含日志面板标记（折叠显示 + 最近 20 条 request 事件 + 复用 fetch）
- parseLogs 纯函数（Node 执行）正确解析 /logs 响应
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GUIDE = HERE.parent / "docs" / "USER_GUIDE.md"
HTML_PATH = HERE.parent / "orchestra" / "dashboard" / "index.html"

NODE = shutil.which("node")
need_node = pytest.mark.skipif(NODE is None, reason="node 不可用，跳过 JS 执行测试")


def _guide() -> str:
    """读取 USER_GUIDE.md；不存在直接断言失败（红灯）。"""
    assert GUIDE.is_file(), f"产物缺失：{GUIDE}"
    return GUIDE.read_text(encoding="utf-8")


def _html() -> str:
    """读取看板 HTML；不存在直接断言失败（红灯）。"""
    assert HTML_PATH.is_file(), f"产物缺失：{HTML_PATH}"
    return HTML_PATH.read_text(encoding="utf-8")


def _inline_js() -> str:
    """提取 HTML 中唯一的内联 <script> 内容。"""
    m = re.search(r"<script>(.*)</script>", _html(), re.S)
    assert m, "HTML 中缺少内联 <script>"
    return m.group(1)


# ---- USER_GUIDE 文档断言 ----
def test_user_guide_含日志端点小节():
    """USER_GUIDE.md 补日志端点用法：两个端点路径 + curl 示例。"""
    g = _guide()
    assert "/api/v1/logs" in g
    assert "/logs/events" in g
    assert "curl" in g
    # 含查询参数说明（limit/level/event 至少其一）
    assert any(k in g for k in ("limit", "level", "event"))


# ---- 看板日志面板静态标记 ----
def test_dashboard_日志面板标记():
    """看板含日志面板：列表容器 + 拉取 request 事件 + 折叠显示标记。"""
    html = _html()
    assert "logs-list" in html             # 日志列表容器
    assert "event=request" in html         # 复用 fetch 拉最近 20 条 request 事件
    assert "logs?limit=20" in html or "logs?limit=20" in html
    # 折叠显示：details/summary 或 display:none
    assert ("<details" in html) or ("display:none" in html)


# ---- parseLogs 纯函数（Node 执行） ----
# Node 桩：最小 DOM/fetch/定时器，使内联脚本可无副作用加载
_JS_PREAMBLE = r"""
var __els = {};
function __mkEl(id) {
  return { id: id, innerHTML: '', textContent: '', style: {}, disabled: false,
           addEventListener: function () {},
           classList: { add: function () {}, remove: function () {} } };
}
var document = {
  getElementById: function (id) {
    if (!__els[id]) { __els[id] = __mkEl(id); }
    return __els[id];
  },
  addEventListener: function () {}
};
function fetch() { return new Promise(function () {}); }
function setInterval() { return 0; }
function clearInterval() {}
"""

_HARNESS = r"""
var out = {};
out.rows = parseLogs([
  {time: '2026-08-26 00:43:12,345', level: 'INFO', logger: 'kb.api',
   message: 'request.start method=GET path=/api/v1/healthz'},
  {time: '2026-08-26 00:44:00,000', level: 'WARNING', logger: 'kb.monitor',
   message: 'x'}
]);
out.html = logsListHTML(out.rows);
out.empty_html = logsListHTML([]);
console.log(JSON.stringify(out));
"""


def _run_js(workdir: Path) -> dict:
    """拼装 桩+内联脚本+线束 交 Node 执行，解析其 JSON 输出。"""
    script = _JS_PREAMBLE + "\n" + _inline_js() + "\n" + _HARNESS
    f = workdir / "logs_test.js"
    f.write_text(script, encoding="utf-8")
    p = subprocess.run([NODE, str(f)], capture_output=True,
                       encoding="utf-8", timeout=30)
    assert p.returncode == 0, f"node 执行失败：{p.stderr}"
    return json.loads(p.stdout.strip().splitlines()[-1])


@need_node
def test_parseLogs_纯函数(tmp_path):
    """/logs 响应 → 行对象（time 切 HH:MM / level / logger / message）+ HTML。"""
    out = _run_js(tmp_path)
    rows = out["rows"]
    assert rows[0]["time"] == "00:43"
    assert rows[0]["level"] == "INFO"
    assert rows[0]["logger"] == "kb.api"
    assert "request.start" in rows[0]["message"]
    assert "00:43" in out["html"]
    assert "request.start" in out["html"]
    # 空数据显示"无日志"提示
    assert "无日志" in out["empty_html"]
