# agent-orchestra MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 rag-kb 仓 orchestra/ 目录实现任务板 CLI、协议文档与 worker skill，跑通 TraeWork 内跨任务开发委派 MVP。

**Architecture:** kb 服务当共享黑板（零改动），任务卡 = tag 为 `taskboard` 的 kb 记录（首行状态行 + 结构化字段）；协调者用 board.py（纯标准库 CLI，走 kb REST）管理卡片；worker 通过 MCP + orchestra-worker skill 领卡执行。

**Tech Stack:** Python 3.10+ 标准库（urllib/argparse）、kb REST API（`http://127.0.0.1:8000/api/v1`）、pytest（rag-kb venv 已有）。

**设计文档：** `orchestra/docs/superpowers/specs/2026-08-24-agent-orchestra-mvp-design.md`（已批准）

**分支：** `orchestra`（已创建并提交设计文档）。测试运行命令统一用 rag-kb venv：`venv\Scripts\python.exe -m pytest orchestra/tests/ -v`（显式路径，不受根 pytest.ini testpaths 影响）。

---

## 文件结构

```
orchestra/
├── board.py                      # 协调者 CLI（单文件，~180 行）
├── protocol.md                   # 通信范式总纲（卡片格式/状态机/上限表）
├── worker-prompt.md              # worker 协议源文档
├── coordinator-prompt.md         # 协调者规约
├── skills/orchestra-worker/SKILL.md  # skill 源文件（安装到 ~/.trae-cn/skills/）
├── tests/
│   ├── conftest.py               # sys.path 注入 + mock fixture
│   └── test_board.py             # board.py 单测
└── docs/superpowers/
    ├── specs/2026-08-24-agent-orchestra-mvp-design.md   # 已存在
    └── plans/2026-08-24-agent-orchestra-mvp.md          # 本计划
```

kb REST 契约（实现依据，来自 kb/api.py 实测代码）：

- `POST /api/v1/memories` body `{content, tags, source?, namespace?}` → `{id, ...record}`
- `GET /api/v1/memories?tag=taskboard&limit=1000` → `{items: [record], total}`（record 含 id/content/tags/created_at/updated_at）
- `GET /api/v1/memories/{id}` → record
- `PATCH /api/v1/memories/{id}` body `{content}` → record
- `GET /api/v1/healthz` → `{status, records, ...}`

---

### Task 1: 测试基建 + HTTP 客户端

**Files:**
- Create: `orchestra/tests/conftest.py`
- Create: `orchestra/tests/test_board.py`
- Create: `orchestra/board.py`

- [ ] **Step 1: 写 conftest（path 注入 + mock fixture）**

```python
"""orchestra 测试配置：把 orchestra/ 加入 sys.path，并提供 _request mock。"""
import sys
from pathlib import Path

import pytest

ORCHESTRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCHESTRA_DIR))


@pytest.fixture
def mock_request(monkeypatch):
    """拦截 board._request：记录调用并返回预置响应。

    用法：先设 mock_request.responses = {"GET /memories": {...}}，
    断言 mock_request.calls == [("GET", "/memories", body), ...]。
    """
    import board

    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        key = f"{method} {path}"
        if key in responses:
            return responses[key]
        for pattern, resp in responses.items():
            if pattern.endswith("*") and key.startswith(pattern[:-1]):
                return resp
        raise AssertionError(f"未预置的请求：{key}")

    responses: dict = {}
    monkeypatch.setattr(board, "_request", fake)
    holder = type("Mock", (), {})()
    holder.calls = calls
    holder.responses = responses
    return holder
```

- [ ] **Step 2: 写失败测试（HTTP 客户端）**

```python
"""board.py 单测：HTTP 客户端、卡片纯函数、五个子命令。"""
import json

import pytest


class TestRequest:
    """_request 走 urllib 并正确解码 JSON。"""

    def test_request_解析JSON响应(self):
        import board
        payload = json.dumps({"status": "ok"}).encode("utf-8")

        class FakeResp:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        captured = {}

        def fake_urlopen(req, timeout):
            captured["method"] = req.method
            captured["url"] = req.full_url
            return FakeResp(payload)

        monkey = pytest.MonkeyPatch()
        monkey.setattr(board.urllib.request, "urlopen", fake_urlopen)
        try:
            result = board._request("GET", "/healthz")
        finally:
            monkey.undo()
        assert result == {"status": "ok"}
        assert captured["method"] == "GET"
        assert captured["url"] == "http://127.0.0.1:8000/api/v1/healthz"

    def test_request_连接失败抛BoardUnavailable(self, monkeypatch):
        import board
        import urllib.error

        def raise_urlerror(req, timeout):
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(board.urllib.request, "urlopen", raise_urlerror)
        with pytest.raises(board.BoardUnavailable):
            board._request("GET", "/healthz")
```

- [ ] **Step 3: 跑测试确认失败**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'board'`）

- [ ] **Step 4: 实现 board.py 骨架（常量 + 异常 + _request）**

```python
#!/usr/bin/env python3
"""agent-orchestra 任务板 CLI：管理 kb 服务上的 taskboard 任务卡。

协调者专用；worker 走 MCP（orchestra-worker skill）。
仅标准库；kb REST 契约见 rag-kb kb/api.py。

用法：
    board.py add --assignee w1 --title T --goal G --input I --constraints C --acceptance A
    board.py status
    board.py show TASK-0003
    board.py verify TASK-0003 --pass | --reject [--note 原因]
    board.py new-worker NAME
"""
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

KB_BASE = "http://127.0.0.1:8000/api/v1"
TAG = "taskboard"
# 各字段字符上限（设计文档第 4 节）
LIMITS = {"title": 30, "goal": 300, "input": 300,
          "constraints": 200, "acceptance": 200, "result": 1000}
# 状态机合法值
STATUSES = ("pending", "claimed", "done", "failed", "verified")


class BoardUnavailable(Exception):
    """kb 服务不可达或服务端错误。"""
```

（`_request` 实现追加在类定义后）

```python
def _request(method: str, path: str, body: dict | None = None) -> dict:
    """kb REST 请求封装。

    连接失败/5xx → BoardUnavailable（退出码 2）；
    4xx → RuntimeError（退出码 1，提示调用方参数或状态问题）。
    """
    url = f"{KB_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise BoardUnavailable(f"kb 服务错误 HTTP {e.code}") from e
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"kb 拒绝请求 HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BoardUnavailable(f"kb 服务不可达：{e}") from e
```

- [ ] **Step 5: 跑测试确认通过**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py -v`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
git add orchestra/board.py orchestra/tests/
git commit -m "orchestra: board.py HTTP 客户端与测试基建"
```

---

### Task 2: 卡片纯函数（渲染/解析/校验）

**Files:**
- Modify: `orchestra/board.py`
- Modify: `orchestra/tests/test_board.py`

- [ ] **Step 1: 写失败测试**

追加到 test_board.py：

```python
class TestCardFunctions:
    """卡片渲染与解析纯函数。"""

    def test_render_标准卡片(self):
        import board
        content = board.render_card(
            "TASK-0001", "pending", "worker-1", "重构异常处理",
            goal="统一异常为 StorageError", input_="kb/storage.py",
            constraints="不改接口签名", acceptance="测试全绿")
        lines = content.split("\n")
        assert lines[0] == "TASK-0001 pending worker-1 | 重构异常处理"
        assert lines[1] == "目标：统一异常为 StorageError"
        assert lines[2] == "输入：kb/storage.py"
        assert lines[3] == "约束：不改接口签名"
        assert lines[4] == "验收：测试全绿"
        assert lines[5] == "结果："

    def test_parse_header_往返(self):
        import board
        header = board.parse_header("TASK-0003 claimed worker-1 | 修复空指针")
        assert header == {"task_id": "TASK-0003", "status": "claimed",
                          "assignee": "worker-1", "title": "修复空指针"}

    def test_parse_header_非法格式报错(self):
        import board
        with pytest.raises(ValueError):
            board.parse_header("这不是一张任务卡")

    def test_check_limits_超限报错(self):
        import board
        with pytest.raises(ValueError) as ei:
            board.check_limits(title="x" * 31)
        assert "title" in str(ei.value)
        # 恰好 30 字符不报错
        board.check_limits(title="x" * 30)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py::TestCardFunctions -v`
Expected: FAIL（`AttributeError: module 'board' has no attribute 'render_card'`）

- [ ] **Step 3: 实现三个纯函数**

追加到 board.py：

```python
def render_card(task_id: str, status: str, assignee: str, title: str,
                goal: str, input_: str, constraints: str,
                acceptance: str, result: str = "", note: str = "") -> str:
    """渲染完整卡片文本；首行为可检索状态行。"""
    lines = [
        f"{task_id} {status} {assignee} | {title}",
        f"目标：{goal}",
        f"输入：{input_}",
        f"约束：{constraints}",
        f"验收：{acceptance}",
        f"结果：{result}",
    ]
    if note:
        lines.append(f"备注：{note}")
    return "\n".join(lines)


_HEADER_RE = re.compile(r"^(TASK-\d{4}) (\w+) (\S+) \| (.+)$")


def parse_header(content: str) -> dict:
    """解析卡片首行 → {task_id, status, assignee, title}；非法格式抛 ValueError。"""
    header = content.split("\n", 1)[0].strip()
    m = _HEADER_RE.match(header)
    if not m:
        raise ValueError(f"卡片首行格式非法：{header!r}")
    return {"task_id": m.group(1), "status": m.group(2),
            "assignee": m.group(3), "title": m.group(4)}


def check_limits(**fields: str) -> None:
    """字段长度校验；超限抛 ValueError（中文提示字段名与上限）。"""
    for name, value in fields.items():
        if value and len(value) > LIMITS[name]:
            raise ValueError(
                f"字段 {name} 超长：{len(value)} 字符 > 上限 {LIMITS[name]}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add orchestra/board.py orchestra/tests/test_board.py
git commit -m "orchestra: 卡片渲染/解析/校验纯函数"
```

---

### Task 3: list_cards + status 命令

**Files:**
- Modify: `orchestra/board.py`
- Modify: `orchestra/tests/test_board.py`

- [ ] **Step 1: 写失败测试**

```python
def _card(content, updated_at="2026-08-24T12:30:00"):
    """构造 kb list 返回的单条记录。"""
    return {"id": "abc123", "content": content, "tags": ["taskboard"],
            "updated_at": updated_at}


class TestStatus:
    """status：列出全部任务卡，一行一卡。"""

    def test_status_一行一卡含时间与标题(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0001 pending worker-1 | 重构异常\n目标：x"),
                _card("TASK-0002 done worker-2 | 修复空指针\n目标：y",
                      updated_at="2026-08-24T09:15:00"),
            ], "total": 2}
        board.cmd_status()
        out = capsys.readouterr().out
        assert "TASK-0001 pending worker-1 12:30 重构异常" in out
        assert "TASK-0002 done worker-2 09:15 修复空指针" in out

    def test_status_空板提示(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        board.cmd_status()
        assert "无任务卡" in capsys.readouterr().out

    def test_status_非法卡片跳过并警告(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("坏卡片内容"), _card("TASK-0009 pending w1 | 正常|卡")],
            "total": 2}
        board.cmd_status()
        out = capsys.readouterr().out
        assert "TASK-0009" in out
        assert "非法" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py::TestStatus -v`
Expected: FAIL（`AttributeError: ... has no attribute 'cmd_status'`）

- [ ] **Step 3: 实现 list_cards 与 cmd_status**

```python
def list_cards() -> list[dict]:
    """列出全部任务卡记录（按 tag 过滤）；服务不可达抛 BoardUnavailable。"""
    resp = _request("GET", "/memories", None)
    # 实际带查询串，见下：_request 的 path 直接拼查询参数
    return resp.get("items", [])


def _fmt_time(updated_at: str) -> str:
    """ISO 时间 → HH:MM；解析失败返回 '???'。"""
    try:
        return datetime.fromisoformat(updated_at).strftime("%H:%M")
    except (ValueError, TypeError):
        return "???"


def cmd_status() -> None:
    """每卡一行：TASK-0003 claimed worker-1 12:30 标题。"""
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    if not cards:
        print("无任务卡")
        return
    for card in cards:
        try:
            h = parse_header(card["content"])
        except ValueError:
            print(f"[警告] 记录 {card.get('id', '?')} 首行非法，已跳过")
            continue
        print(f"{h['task_id']} {h['status']} {h['assignee']} "
              f"{_fmt_time(card.get('updated_at', ''))} {h['title']}")
```

注意：`_request` 的 GET 查询串直接拼在 path 上（urllib 不支持 params 参数），Task 1 实现已兼容（url = KB_BASE + path）。

- [ ] **Step 4: 跑测试确认通过**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add orchestra/board.py orchestra/tests/test_board.py
git commit -m "orchestra: status 命令（一行一卡紧凑输出）"
```

---

### Task 4: add 命令（编号递增 + 校验 + 创建）

**Files:**
- Modify: `orchestra/board.py`
- Modify: `orchestra/tests/test_board.py`

- [ ] **Step 1: 写失败测试**

```python
class TestAdd:
    """add：字段校验、编号递增、创建调用。"""

    def test_add_创建首张卡编号0001(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        mock_request.responses["POST /memories"] = {"id": "new-id"}
        board.cmd_add(assignee="worker-1", title="重构异常", goal="统一异常",
                      input_="kb/storage.py", constraints="不改接口",
                      acceptance="测试全绿")
        out = capsys.readouterr().out
        assert "TASK-0001" in out
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        body = post[2]
        assert body["tags"] == ["taskboard"]
        assert body["content"].startswith(
            "TASK-0001 pending worker-1 | 重构异常")

    def test_add_编号取最大值加一(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [
                _card("TASK-0007 done worker-1 | 旧卡"),
                _card("TASK-0003 pending worker-2 | 旧卡"),
            ], "total": 2}
        mock_request.responses["POST /memories"] = {"id": "x"}
        board.cmd_add(assignee="worker-1", title="t", goal="g", input_="i",
                      constraints="c", acceptance="a")
        post = [c for c in mock_request.calls if c[0] == "POST"][0]
        assert post[2]["content"].startswith("TASK-0008 pending worker-1 | t")

    def test_add_字段超长拒绝(self, mock_request):
        import board
        with pytest.raises(ValueError):
            board.cmd_add(assignee="w1", title="x" * 31, goal="g",
                          input_="i", constraints="c", acceptance="a")
        assert not any(c[0] == "POST" for c in mock_request.calls)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py::TestAdd -v`
Expected: FAIL（`AttributeError: ... has no attribute 'cmd_add'`）

- [ ] **Step 3: 实现 cmd_add**

```python
def _next_task_id(cards: list[dict]) -> str:
    """现有卡最大编号 +1，四位数零填充。"""
    max_num = 0
    for card in cards:
        try:
            h = parse_header(card["content"])
            num = int(h["task_id"].split("-")[1])
            max_num = max(max_num, num)
        except (ValueError, IndexError):
            continue  # 非法卡不参与编号
    return f"TASK-{max_num + 1:04d}"


def cmd_add(assignee: str, title: str, goal: str, input_: str,
            constraints: str, acceptance: str) -> None:
    """创建任务卡（pending）；字段超长抛 ValueError。"""
    check_limits(title=title, goal=goal, input_=input_,
                 constraints=constraints, acceptance=acceptance)
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    task_id = _next_task_id(cards)
    content = render_card(task_id, "pending", assignee, title,
                          goal=goal, input_=input_, constraints=constraints,
                          acceptance=acceptance)
    resp = _request("POST", "/memories",
                    {"content": content, "tags": [TAG]})
    print(f"已创建 {task_id} → 记录 {resp['id']}（assignee: {assignee}）")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py -v`
Expected: 12 passed

- [ ] **Step 5: 提交**

```bash
git add orchestra/board.py orchestra/tests/test_board.py
git commit -m "orchestra: add 命令（编号递增+字段校验）"
```

---

### Task 5: show + verify 命令

**Files:**
- Modify: `orchestra/board.py`
- Modify: `orchestra/tests/test_board.py`

- [ ] **Step 1: 写失败测试**

```python
CARD_FULL = ("TASK-0005 done worker-1 | 修复空指针\n"
             "目标：修复检索空指针\n输入：kb/retriever.py\n"
             "约束：不改接口\n验收：测试全绿\n"
             "结果：已修复第 42 行，测试通过")


class TestShow:
    def test_show_打印整卡(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_FULL)], "total": 1}
        board.cmd_show("TASK-0005")
        out = capsys.readouterr().out
        assert "修复空指针" in out and "结果：已修复" in out

    def test_show_卡不存在报错(self, mock_request, capsys):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        with pytest.raises(SystemExit):
            board.cmd_show("TASK-0099")
        assert "不存在" in capsys.readouterr().err + capsys.readouterr().out


class TestVerify:
    def test_verify_pass_done转verified(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_FULL)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        board.cmd_verify("TASK-0005", action="pass", note="")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        assert patch[2]["content"].startswith("TASK-0005 verified worker-1")

    def test_verify_reject_回pending带备注(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card(CARD_FULL)], "total": 1}
        mock_request.responses["PATCH /memories/abc123"] = {}
        board.cmd_verify("TASK-0005", action="reject", note="结果超长")
        patch = [c for c in mock_request.calls if c[0] == "PATCH"][0]
        content = patch[2]["content"]
        assert content.startswith("TASK-0005 pending worker-1")
        assert "备注：结果超长" in content

    def test_verify_仅done_failed可流转(self, mock_request):
        import board
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [_card("TASK-0006 pending worker-1 | 未完成\n目标：x")],
            "total": 1}
        with pytest.raises(SystemExit):
            board.cmd_verify("TASK-0006", action="pass", note="")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py::TestShow orchestra/tests/test_board.py::TestVerify -v`
Expected: FAIL（`AttributeError`）

- [ ] **Step 3: 实现 cmd_show 与 cmd_verify**

```python
def _find_card(task_id: str) -> tuple[dict, dict]:
    """按 TASK 编号找卡；返回 (记录, 首行解析)，找不到 SystemExit(1)。"""
    cards = _request("GET", f"/memories?tag={TAG}&limit=1000").get("items", [])
    for card in cards:
        try:
            h = parse_header(card["content"])
        except ValueError:
            continue
        if h["task_id"] == task_id:
            return card, h
    print(f"错误：任务卡 {task_id} 不存在", file=sys.stderr)
    raise SystemExit(1)


def cmd_show(task_id: str) -> None:
    """打印整卡（核验用）。"""
    card, _ = _find_card(task_id)
    print(card["content"])


def cmd_verify(task_id: str, action: str, note: str) -> None:
    """核验流转：pass → verified；reject → pending（note 写入备注行）。

    仅 done/failed 状态可流转；其他状态 SystemExit(1)。
    """
    card, h = _find_card(task_id)
    if h["status"] not in ("done", "failed"):
        print(f"错误：{task_id} 状态为 {h['status']}，"
              f"仅 done/failed 可核验", file=sys.stderr)
        raise SystemExit(1)
    new_status = "verified" if action == "pass" else "pending"
    content = card["content"].split("\n", 1)
    # 重写首行；reject 时若有 note 追加备注行
    rest = content[1] if len(content) > 1 else ""
    if action == "reject" and note:
        # 去掉旧备注行（若有）再追加新备注
        rest = "\n".join(l for l in rest.split("\n")
                         if not l.startswith("备注："))
        rest = (rest + f"\n备注：{note}").strip("\n")
    new_content = (f"{task_id} {new_status} {h['assignee']} | {h['title']}"
                   + ("\n" + rest if rest else ""))
    _request("PATCH", f"/memories/{card['id']}", {"content": new_content})
    print(f"{task_id} → {new_status}" + (f"（备注：{note}）" if note else ""))
```

注意：cmd_show 的错误测试断言输出到 stderr 或 out——测试里两者拼接检查，实现统一走 stderr + SystemExit(1)。

- [ ] **Step 4: 跑测试确认通过**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py -v`
Expected: 16 passed

- [ ] **Step 5: 提交**

```bash
git add orchestra/board.py orchestra/tests/test_board.py
git commit -m "orchestra: show/verify 命令（核验流转与打回）"
```

---

### Task 6: new-worker 命令 + CLI main 组装

**Files:**
- Modify: `orchestra/board.py`
- Modify: `orchestra/tests/test_board.py`

- [ ] **Step 1: 写失败测试**

```python
class TestNewWorker:
    def test_new_worker_输出引导语含名字与skill指令(self, capsys):
        import board
        board.cmd_new_worker("worker-1")
        out = capsys.readouterr().out
        assert "worker-1" in out
        assert "orchestra-worker" in out


class TestMain:
    def test_main_status分发(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(sys, "argv", ["board.py", "status"])
        mock_request.responses["GET /memories?tag=taskboard&limit=1000"] = {
            "items": [], "total": 0}
        board.main()
        assert "无任务卡" in capsys.readouterr().out

    def test_main_服务不可达退出码2(self, mock_request, monkeypatch, capsys):
        import board
        monkeypatch.setattr(sys, "argv", ["board.py", "status"])
        mock_request.responses["GET /boom*"] = None  # 触发 AssertionError 前，
        # 直接让 fake 抛 BoardUnavailable：
        monkeypatch.setattr(board, "_request",
                            lambda *a, **k: (_ for _ in ()).throw(
                                board.BoardUnavailable("down")))
        with pytest.raises(SystemExit) as ei:
            board.main()
        assert ei.value.code == 2
```

（第一个 main 测试的 mock_request 已注入 fake；第二个测试直接覆盖 _request 抛异常。）

- [ ] **Step 2: 跑测试确认失败**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/test_board.py::TestNewWorker orchestra/tests/test_board.py::TestMain -v`
Expected: FAIL（`AttributeError`）

- [ ] **Step 3: 实现 cmd_new_worker 与 main**

```python
WORKER_INTRO = """你是 {name}，agent-orchestra 的执行者（worker）。
请在当前任务中执行 skill：orchestra-worker，然后按其协议开始工作：
查卡 → 认领 → 执行 → 回写 → 停止。若无待办任务，回复待命即可。"""


def cmd_new_worker(name: str) -> None:
    """打印该 worker 的引导语（用户复制到新 TraeWork 任务）。"""
    print(WORKER_INTRO.format(name=name))


def main() -> None:
    """CLI 入口；退出码 0 成功 / 1 参数或校验失败 / 2 服务不可达。"""
    parser = argparse.ArgumentParser(description="agent-orchestra 任务板")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="创建任务卡")
    p_add.add_argument("--assignee", required=True)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--goal", required=True)
    p_add.add_argument("--input", required=True)
    p_add.add_argument("--constraints", required=True)
    p_add.add_argument("--acceptance", required=True)

    sub.add_parser("status", help="一行一卡看板")

    p_show = sub.add_parser("show", help="打印整卡")
    p_show.add_argument("task_id")

    p_verify = sub.add_parser("verify", help="核验流转")
    p_verify.add_argument("task_id")
    group = p_verify.add_mutually_exclusive_group(required=True)
    group.add_argument("--pass", dest="action", action="store_const",
                       const="pass")
    group.add_argument("--reject", dest="action", action="store_const",
                       const="reject")
    p_verify.add_argument("--note", default="")

    p_new = sub.add_parser("new-worker", help="打印 worker 引导语")
    p_new.add_argument("name")

    args = parser.parse_args()
    try:
        if args.command == "add":
            cmd_add(assignee=args.assignee, title=args.title, goal=args.goal,
                    input_=args.input, constraints=args.constraints,
                    acceptance=args.acceptance)
        elif args.command == "status":
            cmd_status()
        elif args.command == "show":
            cmd_show(args.task_id)
        elif args.command == "verify":
            cmd_verify(args.task_id, action=args.action, note=args.note)
        elif args.command == "new-worker":
            cmd_new_worker(args.name)
    except BoardUnavailable as e:
        print(f"错误：{e}\n请先启动 kb 服务：python -m kb serve",
              file=sys.stderr)
        raise SystemExit(2) from e
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
```

实现开头需补 `import argparse`（Task 1 骨架未含）。

- [ ] **Step 4: 跑全量测试确认通过**

Run: `venv\Scripts\python.exe -m pytest orchestra/tests/ -v`
Expected: 19 passed

- [ ] **Step 5: 手工冒烟（真服务）**

```bash
venv\Scripts\python.exe orchestra/board.py status
venv\Scripts\python.exe orchestra/board.py new-worker worker-1
venv\Scripts\python.exe orchestra/board.py add --assignee worker-1 --title "冒烟测试卡" --goal "验证board.py" --input "无" --constraints "无" --acceptance "卡片出现在status"
venv\Scripts\python.exe orchestra/board.py status
```

Expected: 卡片创建成功并出现在 status；随后用 show 找到记录 id，DELETE 清理（或留给 dry-run 用）。

- [ ] **Step 6: 提交**

```bash
git add orchestra/board.py orchestra/tests/test_board.py
git commit -m "orchestra: new-worker 命令与 CLI 入口组装"
```

---

### Task 7: 协议文档三件套

**Files:**
- Create: `orchestra/protocol.md`
- Create: `orchestra/worker-prompt.md`
- Create: `orchestra/coordinator-prompt.md`

- [ ] **Step 1: 写 protocol.md（总纲）**

```markdown
# agent-orchestra 协议总纲

版本：v1.0（2026-08-24）｜ 依据：docs/superpowers/specs/2026-08-24-agent-orchestra-mvp-design.md

## 1. 角色

| 角色 | 载体 | 职责 |
|---|---|---|
| 用户 | 人 | 发起需求、开 worker 任务、终验 |
| 协调者 | 一个 TraeWork 任务 | 拆卡、分发、核验、打回 |
| worker | 其他 TraeWork 任务（任意模型） | 领卡、执行、回写 |

## 2. 任务卡

kb 记录（tag=taskboard），首行状态行 + 六字段：

TASK-0003 pending worker-1 | 标题
目标：…（≤300 字符）
输入：…（≤300）
约束：…（≤200）
验收：…（≤200）
结果：…（≤1000，worker 回写）

## 3. 状态机

pending → claimed → done → verified（终态）
                  → failed → verified（终态）
done/failed 可被协调者打回 → pending

## 4. 硬纪律（全体 agent）

1. 单卡单轮：一次唤醒只处理一张卡
2. 禁止轮询：回合结束即待命，不主动重复查询
3. 禁止超范围：只做卡片"目标"内的事
4. 字符上限：结果 ≤1000，执行摘要 ≤200
5. 超时：claimed 超 30 分钟无 done → 协调者打回 pending

## 5. 查卡方式

- worker：`search_memory("TASK pending {我的名字}")` + `search_memory("TASK claimed {我的名字}")`（中断恢复优先续做 claimed）
- 协调者：`board.py status`（一行一卡，不读整卡）
```

- [ ] **Step 2: 写 worker-prompt.md**

```markdown
# worker 协议

你是 agent-orchestra 的 worker。每次被唤醒，严格按以下顺序执行：

1. **查卡**：`search_memory` 查 `"TASK pending {你的名字}"` 与
   `"TASK claimed {你的名字}"`（claimed 优先，那是上次中断的卡）
2. **无卡**：回复"无待办任务，待命中"后结束回合。不猜测、不闲聊、不做未委派的事
3. **有卡**：
   a. 认领：`update_memory` 把首行 `pending` 改为 `claimed`
   b. 执行：按卡内"目标/输入/约束/验收"工作，只做目标范围内的事
   c. 回写：`update_memory` 首行改 `done`（或 `failed`），"结果："后写
      做了什么/改了哪些文件/如何对照验收自查（≤1000 字符）
4. **收尾**：回复执行摘要（≤200 字）后结束回合

## 纪律

- 单卡单轮：一次唤醒一张卡，即使还有多张 pending 也留给下次唤醒
- 禁止轮询：回合结束即停，不要反复查卡
- 禁止超范围：卡片没写的不做；发现需要更多信息时回写 failed 并说明
- 结果超长时压缩：写关键改动与自查结论，不贴大段代码/日志
- 出错诚实：做不完回写 failed + 原因，不谎报 done
```

- [ ] **Step 3: 写 coordinator-prompt.md**

```markdown
# 协调者规约

你是 agent-orchestra 的协调者。职责：拆卡、分发、核验、打回。

## 拆卡原则

- 一卡一任务：粒度以 worker 单轮可完成为准（参考：改 1-3 个文件）
- 五字段齐：目标/输入/约束/验收都写清，worker 不需要猜
- assignee 明确：每张卡指定具体 worker，不发 any 卡
- 字符上限：标题 ≤30、目标 ≤300、输入 ≤300、约束 ≤200、验收 ≤200

## 分发流程

1. `board.py add --assignee … --title … …` 建卡
2. `board.py new-worker NAME` 生成引导语，用户粘贴到新任务

## 核验流程

1. `board.py status` 发现 done/failed 卡
2. `board.py show TASK-XXXX` 读整卡
3. 对照"验收"字段逐条检查（必要时读代码/跑测试）
4. `board.py verify TASK-XXXX --pass`（合格）或 `--reject --note 原因`（打回）

## 打回条件

- 验收项未全部满足
- 结果超长（>1000 字符）或格式混乱
- 做了卡片范围外的事
- claimed 超 30 分钟无 done（worker 失联）→ reject 回 pending

## token 纪律

- 日常用 `status`（一行一卡），只在核验时 `show` 单卡
- 不重读已 verified 的卡；汇总汇报给用户时只引用卡号与结论
```

- [ ] **Step 4: 提交**

```bash
git add orchestra/protocol.md orchestra/worker-prompt.md orchestra/coordinator-prompt.md
git commit -m "orchestra: 协议文档三件套（总纲/worker/协调者）"
```

---

### Task 8: orchestra-worker SKILL.md

**Files:**
- Create: `orchestra/skills/orchestra-worker/SKILL.md`

- [ ] **Step 1: 写 SKILL.md**

```markdown
---
name: orchestra-worker
description: agent-orchestra 执行者（worker）协议：被唤醒后到 kb 任务板领卡、执行、回写、待命。当用户说"你是 worker-N，开始工作"或要求领任务/查任务卡时使用。
---

# orchestra-worker：任务板执行者协议

你是 agent-orchestra 体系中的 worker（名字由用户指定，如 worker-1）。
工作载体是 kb 记忆服务（MCP 工具 search_memory / update_memory / read_memory）。

## 每次唤醒的固定流程

1. **确认身份**：若用户本轮未给出你的 worker 名字，先问一句再用
2. **查卡**（两次检索）：
   - `search_memory` 查询 `"TASK claimed {名字}"` → 有结果则优先续做（上次中断）
   - 否则 `search_memory` 查询 `"TASK pending {名字}"`
3. **无卡**：回复"无待办任务，待命中"，结束回合（不猜测、不闲聊）
4. **有卡**：
   - 认领：`update_memory(记录ID, 首行 pending 改为 claimed 的完整新内容)`
   - 按卡片"目标/输入/约束/验收"执行（只做目标范围内的事）
   - 回写：`update_memory` 首行改 `done`/`failed`，"结果："后写
     改动清单与验收自查（≤1000 字符）
5. **收尾**：回复执行摘要（≤200 字），结束回合

## 硬纪律

- **单卡单轮**：一次唤醒只处理一张卡
- **禁止轮询**：回合结束即待命，绝不主动循环查卡
- **禁止超范围**：卡片没写的不做；信息不足回写 failed 说明
- **诚实**：做不完写 failed + 原因，不谎报 done
- **更新内容要完整**：update_memory 是整卡替换，必须带上原卡的
  目标/输入/约束/验收字段原样 + 修改后的首行与结果

## 卡片格式（读写都以此为准）

TASK-0003 pending worker-1 | 标题
目标：…
输入：…
约束：…
验收：…
结果：…
```

- [ ] **Step 2: 提交**

```bash
git add orchestra/skills/orchestra-worker/SKILL.md
git commit -m "orchestra: orchestra-worker skill 源文件"
```

- [ ] **Step 3: 安装 skill 到本机**

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.trae-cn\skills\orchestra-worker" | Out-Null
Copy-Item "orchestra\skills\orchestra-worker\SKILL.md" "$env:USERPROFILE\.trae-cn\skills\orchestra-worker\SKILL.md"
```

Expected: 新开 TraeWork 任务后 skills 列表可见 orchestra-worker。

---

### Task 9: 端到端 dry-run（协议验证，零额外 AI）

**Files:** 无新文件（验证任务）

- [ ] **Step 1: 建两张测试卡**

```bash
venv\Scripts\python.exe orchestra/board.py add --assignee dryrun --title "dry-run卡A" --goal "验证协议链路" --input "无" --constraints "不真改代码" --acceptance "卡流转到verified"
venv\Scripts\python.exe orchestra/board.py add --assignee dryrun --title "dry-run卡B" --goal "验证打回链路" --input "无" --constraints "无" --acceptance "被reject后回到pending"
```

- [ ] **Step 2: 模拟 worker 用 MCP 走完整协议**

在本会话（我扮演 worker-dryrun）按 SKILL.md 流程执行：
1. `search_memory` 查 `"TASK pending dryrun"` → 应命中卡 A
2. `update_memory` 认领（首行 pending → claimed）
3. 执行（dry-run：不做真实改动）
4. `update_memory` 回写 done + 结果
5. 验证卡 B 同样流转

- [ ] **Step 3: 协调者核验**

```bash
venv\Scripts\python.exe orchestra/board.py status   # 应显示两张卡状态变化
venv\Scripts\python.exe orchestra/board.py verify TASK-0001 --pass
venv\Scripts\python.exe orchestra/board.py verify TASK-0002 --reject --note "dry-run打回测试"
venv\Scripts\python.exe orchestra/board.py status   # 卡A verified，卡B 回 pending
```

- [ ] **Step 4: 清理 dry-run 数据**

删除两张卡（按记录 id DELETE），`status` 确认无残留。

- [ ] **Step 5: 提交 dry-run 结果记录**

```bash
git commit --allow-empty -m "orchestra: 端到端 dry-run 通过（pending→claimed→done→verified→reject→pending）"
```

---

### Task 10: 真机实验准备（MVP 终验指引）

**Files:**
- Create: `orchestra/EXPERIMENT.md`

- [ ] **Step 1: 写实验指引（给用户的操作清单）**

```markdown
# agent-orchestra 真机实验指引（MVP 终验）

## 前置

- kb 服务运行中：`python -m kb serve`（另开终端保持）
- orchestra-worker skill 已安装（Task 8）
- 本仓切到 orchestra 分支

## 步骤

1. **提需求**：在协调者任务（本任务）对 AI 说一个小型开发需求，
   例如"给 orchestra 加一个 board.py list-pending 子命令"
2. **协调者拆卡**：观察 AI 用 board.py add 建 2-3 张卡（assignee=worker-1）
3. **开 worker 任务**：新开一个 TraeWork 任务（模型任选），
   粘贴 `board.py new-worker worker-1` 的输出引导语
4. **观察 worker**：它应加载 orchestra-worker skill → 领卡 → 执行 → 回写
5. **核验**：回协调者任务说"核验"，AI 用 board.py verify 流转
6. **检查点**：
   - [ ] 卡片全程符合字符上限（show 抽查）
   - [ ] worker 单卡单轮，无轮询
   - [ ] 协调者只靠 status/show 汇报（上下文增长受控）
   - [ ] 全链路 pending→…→verified 走通

## 失败排查

- worker 查不到卡 → 确认它挂载了 kb MCP、名字与 assignee 一致
- verify 报状态错 → 卡未到 done/failed，先看 worker 是否回写
- 全部卡卡死 claimed → 等 30 分钟超时或人工 reject
```

- [ ] **Step 2: 提交**

```bash
git add orchestra/EXPERIMENT.md
git commit -m "orchestra: 真机实验指引"
```

- [ ] **Step 3: 推送分支**

```bash
git push -u origin orchestra
```

---

## 计划自审结论

- **Spec 覆盖**：设计文档第 4 节（任务卡+上限+状态机）→ Task 2-5；第 5 节（三件套）→ Task 7；第 6 节（skill）→ Task 8；第 7 节（board.py 五命令）→ Task 1-6；第 9 节测试验收（单测/dry-run/真机）→ Task 1-6 / 9 / 10。无遗漏。
- **占位符**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致**：`cmd_add(assignee, title, goal, input_, constraints, acceptance)` 等签名在各任务间一致；mock fixture `mock_request` 全程复用。
- **已知取舍**：Task 3 的 `list_cards` 辅助函数被 cmd_status 内联替代（避免死代码），实现时以 cmd_status 直连 `_request` 为准。
