# N18 日志查看端点实施设计（GET /api/v1/logs）

- 日期：2026-08-25
- 状态：设计书（TASK-0013 产出），待拆实现卡
- 依据：`docs/superpowers/specs/2026-08-24-logging-design.md`（第 4 节数据模型）；TASK-0011 已合入的 `kb/logging_setup.py`（文本行格式与轮转策略）；TASK-0010 已合入的 `board.py watch` 命令模式
- 设计原则：**零常驻**（复用 kb serve，不新增进程）；**只读**端点；与既有 REST 契约/错误格式一致

## 0. 前提核实（与卡描述的两处差异，已按真实实现定稿）

实测 TASK-0011 合入的 `kb/logging_setup.py` 与 `kb/config.py`，卡内两处假设与实际不符，本设计**按真实实现**定稿：

| 卡内假设 | 实际实现 | 本设计采用 |
|---|---|---|
| 日志在 `kb_data/logs/kb.log` | `settings.log_file` = `log_dir / "kb.log"`，默认 **`logs/kb.log`**（仓库根；`config.py` L36-39） | `settings.log_file` |
| 日志为 **JSON 行**、可"复用 0011 的 JSON 行解析" | `logging_setup.py` 输出 **文本行**：`%(asctime)s | %(levelname)s | %(name)s | %(message)s`（UTF-8，RotatingFileHandler） | 新增文本行解析纯函数 |

> 说明：0011 未产出 JSON 行解析器，故本设计需**新增** `_parse_log_line`（见第 2 节），不存在可复用的 JSON 解析。

## 1. 端点契约

### 1.1 `GET /api/v1/logs`（主端点）

**参数**（query）：

| 参数 | 类型 | 默认 | 语义与约束 |
|---|---|---|---|
| `limit` | int | 100 | 返回最大条目数；`1 ≤ limit ≤ 1000`，越界 → 422 |
| `level` | str | 无（全部） | 级别过滤：`DEBUG/INFO/WARNING/ERROR/CRITICAL`，**大小写不敏感**；非法值 → 422 |
| `event` | str | 无（不过滤） | 对 message 的**子串过滤**（大小写不敏感）；空串等价不过滤 |

**读取语义（明确 limit 与过滤）**：从日志文件**尾部向前扫描**，逐行解析 + 按 level/event 过滤，**凑满 `limit` 条为止**；扫描行数上限 `SCAN_MAX = 20000`（常量），到文件头或触上限即停。`items` 按文件顺序（时间升序）返回。

**响应**（200）：
```json
{
  "items": [
    {"time": "2026-08-25 21:50:22,978", "level": "INFO",
     "logger": "kb.serve", "message": "服务启动 version=1.0.2 host=127.0.0.1 port=8000",
     "line": 1}
  ],
  "total": 1,
  "truncated": false
}
```
- `time`：asctime 原文（含毫秒逗号格式），不解析为 datetime（避免时区歧义，消费方可自解析）
- `logger`：日志 `name` 字段（如 `kb.serve` / `kb.api` / `kb.retriever`）
- `line`：在文件中的 1 基行号（便于定位）
- `total`：返回条目数（`≤ limit`）
- `truncated`：是否因触 `SCAN_MAX` 提前停止（为 true 说明可能漏掉更早匹配行）

**错误码**：
| 码 | 场景 |
|---|---|
| 200 | 正常（含日志文件不存在/为空 → `items=[]`，不视为错误） |
| 422 | 参数非法（limit 越界、level 不在枚举；走 FastAPI 统一校验） |
| 500 | 意外 IO 错误：`{"error": "LOG_READ_ERROR", "message": "..."}`（文件存在但不可读等） |

### 1.2 `GET /api/v1/logs/events`（可选，事件统计）

**参数**：`window` int 默认 1000（`1 ≤ window ≤ 10000`，扫描最近 N 行）；`level` 可选（只统计该级别，非法 → 422）。

**响应**（200）：
```json
{"window": 1000, "total_lines": 88,
 "by_level": {"INFO": 85, "WARNING": 3},
 "by_logger": {"kb.serve": 40, "kb.api": 45, "kb.watcher": 3}}
```

> 说明：当前文本行格式**无结构化 event 字段**，故"event 计数"以 **logger（模块）** 与 **level** 两个维度统计（按行数）。若未来引入结构化 event 再扩展，契约留扩展位。

## 2. 实现要点（实现卡执行参照）

- **新增解析纯函数** `_parse_log_line(line: str) -> dict | None`：按**前 3 个 `|`** 切分（`asctime | level | name | message`），`message` 可能含 `|` 需保留余下全部；字段数不足或格式非法返回 `None`（调用方跳过）。建议放独立小模块 `kb/log_reader.py`（纯函数、无 service 依赖、便于单测），`kb/api.py` 的 create_app 引用。
- **读尾部**：`deque(open(path, encoding="utf-8", errors="replace"), maxlen=SCAN_MAX)` 取最近至多 20000 行，逆序扫描解析+过滤，收集 `limit` 条后 `reverse()` 再返回（保持文件序）。
- **路径**：一律用 `settings.log_file`（`config.py` L57-59），**不硬编码**；`log_dir` 为相对路径（相对启动 CWD，serve 从仓库根启动即为 `logs/kb.log`）。
- **轮转**：v1 只读当前 `kb.log`；`kb.log.1..N` 历史轮转文件**不读**（文档明示限制；RotatingFileHandler 轮转不影响读当前文件句柄/文件）。
- **敏感**：端点只透传日志原文，不新增内容；N17/N18 敏感过滤已保证日志内不含密钥等敏感字段。
- **注册**：`create_app` 中新增两个 GET 路由；`limit`/`window` 用 `Query(ge=1, le=...)`，`level` 用 `Literal` 枚举（大小写不敏感由实现 upper() 处理）。
- **常量**：`SCAN_MAX = 20000`、`LOG_LIMIT_MAX = 1000`、`EVENT_WINDOW_MAX = 10000` 置于 `kb/log_reader.py`。

## 3. 测试清单（TDD 红灯基准，落 `tests/test_n18_log_api.py`）

```python
def test_parse_log_line_正常行():
    # "2026-08-25 21:50:22,978 | INFO | kb.serve | 服务启动 version=1.0.2"
    # → {"time","level","logger","message"} 各字段正确

def test_parse_log_line_message含竖线():
    # message 含 "|" 时保留余下全部（不误切）

def test_parse_log_line_非法行返回None():
    # 字段不足/空行 → None（跳过不崩）

def test_logs_返回尾部N行():
    # 临时日志文件写 3 行，?limit=2 → items 长度 2、按文件序、字段齐全

def test_logs_limit默认与上限():
    # 缺省 limit=100；limit=0 / limit=1001 → 422

def test_logs_level过滤():
    # INFO+WARNING 混合，?level=WARNING → 只含 WARNING；?level=warn → 同效（大小写不敏感）

def test_logs_level非法422():
    # ?level=FOO → 422

def test_logs_event子串过滤():
    # ?event=命中 → 只含 message 含"命中"的行（大小写不敏感）

def test_logs_文件不存在返回空():
    # settings.log_file 指向不存在路径 → 200 {"items":[],"total":0,"truncated":false}

def test_logs_events_统计():
    # /logs/events?window=100 → by_level/by_logger 计数正确
```

**回归**：kb 全量测试保持全绿；端点纯新增，不改既有行为。

## 4. 与看板的集成点（后续卡）

- `board.py watch` 后续可加 `--log` 开关：`cmd_watch` 增加布尔参数，`_watch_frame` 追加"日志段"——调用 `GET /api/v1/logs?limit=5`，逐条显示 `time | level | logger | message 前 60 字符`（复用现有 `_truncate`）。
- 归属：该改动只动 `orchestra/board.py` 与 `orchestra/tests/test_board.py`，与 kb 侧 `log_reader.py`/`api.py` **零文件交集**，可与本端点实现并行；但需在端点合入 main 后冒烟联调。
- 数据契约即本设计 1.1 响应 schema，前端/CLI 按 `items[].{time,level,logger,message}` 取用即可。

## 5. 边界与不做（YAGNI）

- 不做日志分页游标（limit 够用；SCAN_MAX 封顶）
- 不读轮转历史文件（v1 单文件；需要时再加 `--file`/轮转索引）
- 不做日志文件行级权限控制（本地单用户；P2-2 鉴权上线后再评估）
- 不做 event 字段结构化改造（改日志格式属另一设计，本端点以 logger/level 代理统计）
