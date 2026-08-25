# 简单看板实施设计（实时监控 worker 状态与任务执行）

- 日期：2026-08-25
- 状态：设计书（TASK-0008 产出），待拆实现卡
- 依据：用户需求"开发一个简单的看板，实时监控各个 worker 的状态以及任务执行情况"；board.py 现有 `status`/`workers`/`list-pending` 命令与 registry 数据模型；V2-2 设计书（`orchestra/docs/superpowers/specs/2026-08-25-orchestra-v2-2-comm-cli-design.md`，交流窗数据可上板）
- 设计原则：**零常驻**（复用 kb serve，不新增服务进程）；CLI 优先、终端为主（对齐 P2-4 方向）；看板**只读监控，不写库**

## 0. 数据面与字段映射（先定数据从哪来）

看板展示三块数据，全部来自 kb 现有记忆记录，**无新数据源**：

| 数据 | 来源 | 解析方式 | 字段映射 |
|---|---|---|---|
| Worker 状态 | `GET /api/v1/memories?tag=registry&limit=1000` | content 为 JSON：`{worker, model, client, registered_at, last_seen, status}` | worker→名字、model→模型、status→状态、last_seen→最后活跃、client→客户端 |
| 任务执行 | `GET /api/v1/memories?tag=taskboard&limit=1000` | content 首行 `TASK-XXXX status assignee \| title`（同 board.py `parse_header` 正则）+ 记录 updated_at | task_id/status/assignee/title→卡行、updated_at→时间 |
| 交流窗（可选） | `GET /api/v1/memories?limit=1000` 后过滤 tag 前缀 `comm:*` | tags/source/content | channel→频道、source→来源、content→文本、updated_at→时间 |

memory 记录字段以 kb `model_dump` 为准：`id/content/type/namespace/source/tags/importance/created_at/updated_at`。

## 1. 技术选型（零常驻，两方案）

两方案均满足"零常驻、不新增进程"（拉模式轮询，无 WebSocket/SSE）：

- **方案 A：CLI watch（终端看板）— 推荐一期**：`board.py watch [--interval N] [--comm]` 前台轮询循环，每 N 秒重绘 worker 行 + 任务卡行，Ctrl+C 退出。**零 kb 改动、无需重启，立即可用**；与既定"CLI 优先、终端+agent 交流为主"方向一致。每轮仅 2 个轻量 GET。
- **方案 B：静态 HTML 看板（浏览器）— 二期可选**：单文件 `orchestra/dashboard/index.html`，由 **kb serve 同源挂载**提供（`app.mount("/dashboard", StaticFiles(directory=..., html=True))`，约 3 行），页面 `fetch()` 轮询 kb REST。**关键洞察：同源即免 CORS**——已实测 kb 响应无 `Access-Control-Allow-Origin` 头（未开 CORS），但 HTML 由 kb 自己 serve 时浏览器同源策略不拦截相对路径 `fetch('api/v1/...')`，**无需改 CORS**。代价：属服务端改动，需协调者在 kb 重启窗口实施（可与已暂缓的 watcher 容错重启申请合并执行）。

> **选型结论**：一期实现 A（零改动、立即有看板）；B 作为视觉增强二期随 kb 重启落地。两方案数据解析逻辑完全一致（同一字段映射），解析函数可复用，后续从 A 升 B 成本低。

## 2. 数据来源与 API 契约

**复用现有 REST 端点，不新增 kb 端点**：

- `GET /api/v1/memories?tag=registry&limit=1000` → `{"items":[...], "total":N}`
- `GET /api/v1/memories?tag=taskboard&limit=1000` → 同上，content 首行解析
- 交流窗：`GET /api/v1/memories?limit=1000` → items 中 tag 前缀 `comm:` 者，按 updated_at 降序取最近 N

无新契约。若实现 B，kb 侧仅加静态挂载（实现卡完成，需重启），不影响 REST 契约与 MCP。

## 3. 页面 / 命令结构

### 3.1 方案 A：`board.py watch` 命令结构

- **签名**：`board.py watch [--interval N] [--comm]`；`N` 默认 5（秒），须 ≥1；`--comm` 时底部附最近 5 条交流窗记录
- **循环**：`load()` → 取 registry + taskboard（+comm）→ 打印两段（worker 行格式与 `workers` 一致：`名字 模型 状态 最后活跃`；卡行格式与 `status` 一致：`TASK 状态 assignee HH:MM 标题`）→ `time.sleep(interval)`
- **退出**：捕获 `KeyboardInterrupt` → 打印"watch 已退出"、干净返回（退出码 0）
- **可测性**：解析逻辑抽为纯函数（复用/抽取 `parse_header`、registry JSON 解析），`watch` 主循环支持测试注入（如 `--once` 单轮模式或注入轮数）以便单测
- **文件交集**：board.py 正被 TASK-0009（worker-2 的 report/list-comm）改动，**watch 实现须在 TASK-0009 合入 main 后串行进行**，避免同文件冲突

### 3.2 方案 B：HTML 看板页面结构

- **文件**：`orchestra/dashboard/index.html`（单文件、内联 CSS/JS、零依赖、纯静态）
- **布局**：顶部（标题 / 上次刷新时间 / 轮询间隔 / 手动刷新 / 暂停-继续）→ ① Worker 状态表（名字/模型/状态/最后活跃：busy 高亮、idle 灰、last_seen 距今超阈值标"离线"）→ ② 任务看板（一表列出，按状态着色：pending 待办黄 / claimed 进行中蓝 / done 待核验橙 / verified 完成灰）→ ③ 交流窗滚动区（channel 着色：done 绿 / issue 红 / test 蓝 / system 灰，source+时间+text）
- **JS**：`load()` 以相对路径并行 `fetch` 三数据源 → 渲染；`setInterval(load, 5000)`（默认 5s）；fetch 失败显示"服务不可用"提示、不崩溃；支持暂停/恢复
- **kb 侧**：静态挂载（实现卡完成，需重启窗口）

## 4. 测试清单（TDD 红灯基准）

### 4.1 方案 A（落 `orchestra/tests/test_board.py`）

```python
def test_watch_输出worker行与卡行(mock_request, capsys):
    # mock registry+taskboard 各若干条；watch 单轮模式
    # 断言输出含 worker 行（名字 模型 状态 最后活跃）与 TASK 卡行

def test_watch_空数据提示(mock_request, capsys):
    # registry/taskboard 为空 → 输出"无已注册 worker"，卡行为空不崩溃

def test_watch_interval非法():
    # --interval 0 → 报错（须 ≥1）

def test_watch_退出处理():
    # KeyboardInterrupt 被捕获 → 干净退出（退出码 0）
```

### 4.2 方案 B（静态断言 + 解析单测）

```python
def test_dashboard_html_存在且含三数据源():
    # orchestra/dashboard/index.html 存在，含 'tag=registry'、'tag=taskboard'、'comm' 与 'setInterval'

def test_字段映射_registry到worker行():
    # 解析函数：registry JSON → worker 行字段（worker/model/status/last_seen）

def test_字段映射_taskboard到卡行():
    # 卡片首行 → {task_id, status, assignee, title}

def test_冒烟_kb挂载dashboard():
    # kb 重启后 GET /dashboard/ 返回 200 且 Content-Type 含 text/html
```

**回归**：orchestra 全量测试保持全绿；不动既有命令行为（watch 为纯新增）。

## 5. 边界与不做（YAGNI）

- 不做 WebSocket/SSE 推送：拉模式足够简单；kb 的 SSE 仅 MCP 用，不引到看板
- 不做鉴权/写权限：看板只读监控；P2-2 鉴权上线后再考虑
- 不新增 kb 端点：复用现有契约，保持零契约漂移
- 交流窗为可选区块：一期 CLI 用 `--comm` 开关，默认关
