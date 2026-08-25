"""TASK-0014 验收测试：HTML 看板二期（设计书方案 B，单文件纯静态）。

依据设计书 §4.2 方案 B 测试清单（静态断言 + 解析单测）：
- test_dashboard_html_存在且含三数据源：文件存在，含三数据源标记与 setInterval
- test_字段映射_registry到worker行：registry JSON → worker 行字段
- test_字段映射_taskboard到卡行：卡片首行 → {taskId, status, assignee, title}
- test_字段映射_comm过滤与排序：交流窗过滤/频道提取/时间降序（补充覆盖）
- test_页面控件：手动刷新/暂停继续/5 秒轮询标记

注：§4.2 的 test_冒烟_kb挂载dashboard（GET /dashboard/ 返回 200）属于
kb 侧静态挂载，由协调者在合并后的重启窗口统一实施，不在本卡范围，故不在此。

解析单测通过 Node 真实执行 HTML 中的内联 <script>（零依赖纯 JS 可直接运行），
断言解析函数的真实行为，而非仅静态字符串。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
# 产物：仓库根下 orchestra/dashboard/index.html
HTML_PATH = HERE.parent / "orchestra" / "dashboard" / "index.html"

NODE = shutil.which("node")
need_node = pytest.mark.skipif(NODE is None, reason="node 不可用，跳过 JS 执行测试")


def _html() -> str:
    """读取产物 HTML；不存在直接断言失败（红灯）。"""
    assert HTML_PATH.is_file(), f"产物缺失：{HTML_PATH}"
    return HTML_PATH.read_text(encoding="utf-8")


def _inline_js() -> str:
    """提取 HTML 中唯一的内联 <script> 内容。"""
    m = re.search(r"<script>(.*)</script>", _html(), re.S)
    assert m, "HTML 中缺少内联 <script>"
    return m.group(1)


# Node 测试环境桩：最小 DOM/fetch/定时器，使内联脚本可无副作用加载
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

# 测试线束：调用纯函数并打印 JSON 结果
_HARNESS = r"""
var out = {};
out.header_ok = parseHeader('TASK-0014 pending worker-4 | HTML看板二期实现');
out.header_bad = parseHeader('非法的首行');
out.time_ok = fmtTime('2026-08-25T21:02:33');
out.time_bad = fmtTime('不是时间');
out.registry = parseRegistry([
  {content: '{"worker":"worker-4","model":"qwen3.8-max","status":"busy","last_seen":"2026-08-25T22:01"}'},
  {content: '不是JSON'}
]);
out.row_busy = registryTableHTML([{name:'w1', model:'m1', status:'busy', lastSeen:'22:01'}]);
out.row_idle = registryTableHTML([{name:'w2', model:'m2', status:'idle', lastSeen:'21:59'}]);
out.tasks = parseTasks([
  {content: 'TASK-0001 done worker-1 | charset补全\n目标：x', updated_at: '2026-08-25T21:02:00'},
  {content: '坏首行', updated_at: '2026-08-25T21:02:00'}
]);
out.row_done = taskTableHTML(out.tasks);
out.comm = parseComm([
  {content: 'done消息', tags: ['comm:done'], source: 'worker-1', updated_at: '2026-08-25T21:00'},
  {content: '不是comm', tags: ['registry'], source: 'x', updated_at: '2026-08-25T23:00'},
  {content: 'issue消息', tags: ['comm:issue'], source: 'worker-2', updated_at: '2026-08-25T21:30'}
], 10);
out.row_comm = commListHTML(out.comm);
// TASK-0024：未注册 assignee 补 inferred 灰行（走 renderWorkers 真实渲染路径）
// registry 仅注册 worker-4；任务卡含未注册的 worker-9（claimed）与 worker-7（done）
renderWorkers(
  [{content: '{"worker":"worker-4","model":"qwen3.8-max","status":"busy","last_seen":"' + new Date().toISOString() + '"}'}],
  [{content: 'TASK-0024 claimed worker-9 | 看板inferred行', updated_at: '2026-08-26T00:00'},
   {content: 'TASK-0001 done worker-7 | 历史任务', updated_at: '2026-08-25T21:00'},
   {content: 'TASK-0002 claimed worker-4 | 已注册成员任务', updated_at: '2026-08-25T22:00'}]
);
out.worker_rows = document.getElementById('workers-body').innerHTML;
console.log(JSON.stringify(out));
"""


def _run_js(workdir: Path) -> dict:
    """拼装 桩+内联脚本+线束 交 Node 执行，解析其 JSON 输出。"""
    script = _JS_PREAMBLE + "\n" + _inline_js() + "\n" + _HARNESS
    f = workdir / "dashboard_test.js"
    f.write_text(script, encoding="utf-8")
    p = subprocess.run([NODE, str(f)], capture_output=True,
                       encoding="utf-8", timeout=30)
    assert p.returncode == 0, f"node 执行失败：{p.stderr}"
    return json.loads(p.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def js_out(tmp_path_factory):
    """模块级执行一次 JS 线束，供各解析测试复用。"""
    if NODE is None:
        pytest.skip("node 不可用")
    return _run_js(tmp_path_factory.mktemp("js"))


def test_dashboard_html_存在且含三数据源():
    """设计书 §4.2：文件存在，含 'tag=registry'、'tag=taskboard'、'comm' 与 'setInterval'。"""
    html = _html()
    assert "tag=registry" in html
    assert "tag=taskboard" in html
    assert "comm" in html
    assert "setInterval" in html


def test_页面控件():
    """卡内顶部要素：标题/上次刷新/间隔/手动刷新/暂停继续；默认轮询 10 秒（用户反馈调长）。"""
    html = _html()
    assert "手动刷新" in html
    assert "暂停" in html
    assert "继续" in html
    assert "10000" in html


@need_node
def test_字段映射_registry到worker行(js_out):
    """设计书 §0 映射：worker→名字、model→模型、status→状态、last_seen→最后活跃。"""
    assert js_out["registry"] == [
        {"name": "worker-4", "model": "qwen3.8-max",
         "status": "busy", "lastSeen": "22:01"}]
    # 非 JSON 记录被跳过；busy 高亮 / idle 灰 的着色类存在
    assert 'class="busy"' in js_out["row_busy"] and "w1" in js_out["row_busy"]
    assert 'class="idle"' in js_out["row_idle"]


@need_node
def test_字段映射_taskboard到卡行(js_out):
    """首行正则与 board.py parse_header 一致；解析出 卡号/状态/执行者/标题。"""
    assert js_out["header_ok"] == {
        "taskId": "TASK-0014", "status": "pending",
        "assignee": "worker-4", "title": "HTML看板二期实现"}
    assert js_out["header_bad"] is None
    assert js_out["time_ok"] == "21:02"
    assert js_out["time_bad"] == "???"
    assert js_out["tasks"] == [
        {"taskId": "TASK-0001", "status": "done", "assignee": "worker-1",
         "title": "charset补全", "time": "21:02"}]
    assert "st-done" in js_out["row_done"]


@need_node
def test_字段映射_comm过滤与排序(js_out):
    """只留 tag 前缀 comm:* 者；提取频道；按 updated_at 降序。"""
    channels = [c["channel"] for c in js_out["comm"]]
    assert channels == ["issue", "done"]
    assert js_out["comm"][0]["source"] == "worker-2"
    assert "ch-issue" in js_out["row_comm"]
    assert "ch-done" in js_out["row_comm"]


def test_inferred行静态标记():
    """TASK-0024：HTML 含"未注册 assignee 补 inferred 行"的逻辑与灰行样式标记。"""
    html = _html()
    assert "inferred" in html              # 模型列标识与行类名
    assert "tr.inferred" in html           # 灰行样式类（与既有状态着色体系一致）
    assert "未注册" in html                  # 补行逻辑的中文注释标记
    assert html.count("tr.inferred") >= 1


@need_node
def test_inferred行_未注册assignee补灰行(js_out):
    """TASK-0024：任务卡 assignee 减去 registry 已有者，补显示 inferred 行。

    场景：registry 仅注册 worker-4；任务卡含未注册 worker-9（claimed）
    与 worker-7（仅 done 卡）。
    """
    rows = js_out["worker_rows"]
    # 未注册且有 claimed 卡 → 补行：成员上板且不重复
    assert rows.count("worker-9") == 1
    # worker-9：claimed → busy
    assert "worker-9" in rows and "busy" in rows
    # worker-7：仅 done 卡（无 claimed）→ 状态推断为 idle
    assert "worker-7" in rows and "idle" in rows
    # 补行使用灰行类名
    assert 'class="inferred"' in rows
    # 已注册成员不受影响、不重复：worker-4 仅 1 行，模型列保持注册值，状态按 claimed 推断 busy
    assert rows.count("worker-4") == 1
    assert "qwen3.8-max" in rows


@need_node
def test_inferred行_已注册成员不重复且模型列不被覆盖(js_out):
    """TASK-0024：已注册成员不因任务卡再次出现 inferred 模型列（防重复/防串扰）。"""
    rows = js_out["worker_rows"]
    # 全表恰好两条补行（worker-9 与 worker-7），模型列 'inferred' 恰出现 2 次（行类名另计）
    assert rows.count('<td>inferred</td>') == 2
    # worker-4 所在行不含 inferred 模型列
    seg = rows.split("</tr>")
    w4 = [s for s in seg if "worker-4" in s]
    assert len(w4) == 1
    assert "inferred" not in w4[0]


def test_kb挂载dashboard返回200(env_isolated):
    """TASK-0014 集成项（协调者实施）：GET /dashboard/ 返回 200 且 text/html。"""
    from fastapi.testclient import TestClient

    from kb.api import create_app

    with TestClient(create_app()) as c:
        resp = c.get("/dashboard/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # 页面可渲染的基本标记：标题与轮询脚本
        assert "<title>" in resp.text
        assert "setInterval" in resp.text
