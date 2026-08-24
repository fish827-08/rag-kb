# 整项目日志体系设计（P2-1）

- 日期：2026-08-24
- 状态：已与需求方确认方向（P2 优先级第 1 位），待实施
- 优先级：P2-1（其后为 P2-2 鉴权、P2-3 遗忘机制）
- 前置依赖：无（基于 kb v1.0.1）

## 1. 设计洞察：先界定"整个项目的日志"到底是什么

本仓库有两个子系统，逐一分析日志需求后，结论是**日志主体 = kb serve 运行日志**：

| 组件 | 进程形态 | 日志需求分析 |
|---|---|---|
| kb serve | 唯一常驻进程 | **完整运行日志**（本设计核心）：启动、请求、检索、LLM 路由、错误 |
| board.py / kb CLI | 短命进程 | 终端输出即可；其操作全部经 kb REST，**已被服务端访问日志覆盖**（审计不丢） |
| orchestra 协作 | 无进程 | 任务卡本身即结构化审计记录（kb 记录带 updated_at 与状态行），**不重复造轮子** |
| worker/协调者 AI | TraeWork 会话 | 其行为已落在任务卡流转 + board.py 审计链里，不属本系统日志范畴 |

**原则**：一处集中（kb serve），处处覆盖（所有客户端操作最终都过 kb REST/MCP 端点）。

## 2. 目标

1. **可排查**：服务异常时能从日志定位（启动失败、OOM 降级、请求报错、LLM 路由异常）
2. **可审计**：谁在何时对记忆库做了什么（写入/修改/删除/检索）——为 P2-2 鉴权打基础
3. **可观测**：检索耗时、命中数、LLM 路由分布——为性能回归与 /ask 调优提供数据
4. **本地优先**：零新依赖（Python logging 标准库）、日志落本地目录、断网可用

## 3. 配置（走 kb/config.py，禁止硬编码）

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `KB_LOG_LEVEL` | `INFO` | 全局级别（DEBUG 开发期排查用） |
| `KB_LOG_DIR` | `logs` | 日志目录（相对仓库根；gitignore 排除） |
| `KB_LOG_MAX_BYTES` | `1048576` | 单文件 1MB |
| `KB_LOG_BACKUP_COUNT` | `5` | 保留 5 个轮转备份（合计 ≤6MB，个人项目量级） |

## 4. 日志体系结构（三层）

### 4.1 运行生命周期日志（logger: `kb`）

```
INFO  kb.serve   服务启动 version=1.0.1 host=127.0.0.1 port=8000
INFO  kb.embedder 模型加载 device=cuda 模型=BGE-M3 耗时=8.2s
INFO  kb.serve   服务就绪 records=10
INFO  kb.serve   服务停止
WARN  kb.embedder GPU 不可用，降级 device=cpu        ← 设备降级留痕
ERROR kb.api     未捕获异常 ...（含栈帧）              ← 兜底记录
```

### 4.2 请求访问日志（ASGI 中间件，logger: `kb.api`）

**中间件挂载在 ASGI 栈最外层**，REST 与 MCP 端点统一覆盖（MCP 的 initialize/tools/list/tools/call 全部可见）：

```
INFO kb.api GET  /api/v1/healthz 200 1ms
INFO kb.api POST /api/v1/memories 200 48ms
INFO kb.api POST /api/v1/search 200 26ms 命中=5
INFO kb.api GET  /mcp/ 200 3ms                     ← MCP 握手
INFO kb.api PATCH /api/v1/memories/abc123 200 12ms
WARN kb.api POST /api/v1/search 422 0ms            ← 校验拒绝也记录
```

格式：`方法 路径 状态码 耗时`，检索类附加 `命中=N`。

### 4.3 业务语义日志（关键路径埋点，logger: `kb.retriever` / `kb.llm`）

**检索链路**（每次 /search 与 /ask 的检索段）：

```
INFO kb.retriever 检索 query="如何配置 MCP*" 模式=hybrid 向量耗时=18ms bm25耗时=4ms 命中=5
```

**LLM 路由**（每次 /ask）：

```
INFO kb.llm 路由 分类=COMPLEX 意图=云端 模型=deepseek-v4-flash 缓存=miss
INFO kb.llm 生成 耗时=2.3s tokens≈820 来源=cloud
INFO kb.llm 路由 分类=SIMPLE 意图=本地 模型=qwen3:4b 缓存=hit
INFO kb.llm 隐私隔离生效 意图=SENSITIVE 全程本地     ← 隐私护栏留痕
```

> 现状缺口：/ask 当前无结构化日志，路由决策只活在代码路径里——本层补齐后，P2-3 遗忘机制与 /ask 调优都有了数据依据。

## 5. 敏感数据红线（硬约束）

1. **不记完整请求 body**：只记方法/路径/状态/耗时；content/query 仅记**截断摘要（前 50 字符）**且只出现在业务语义日志
2. **密钥/凭据永不入日志**：`DEEPSEEK_API_KEY` 等（日志过滤器的 blocklist：`api_key`、`authorization`、`token` 字段名直接不渲染）
3. 日志文件属本地运行数据，随 `logs/` 进 gitignore（与 kb_data 同级对待）

## 6. 实现要点（开发 AI 执行参照）

| 模块 | 改动 |
|---|---|
| `kb/config.py` | 新增 4 个日志配置项（第 3 节） |
| `kb/logging_setup.py`（新） | `setup_logging()`：根 logger 配置双 handler——控制台（精简格式）+ RotatingFileHandler（完整格式）；启动时调用一次 |
| `kb/api.py` | ASGI 请求日志中间件（外层挂载）；检索端点埋"命中=N" |
| `kb/retriever.py` | 检索埋点：query 摘要/模式/双路耗时/命中数 |
| `kb/llm.py` + `kb/service.py` | 路由埋点：分类/意图/模型/缓存命中/耗时/隐私隔离 |
| `kb/__main__.py` / serve 路径 | 启动/停止/模型加载/降级日志 |

**格式统一**：`%(asctime)s | %(levelname)s | %(name)s | %(message)s`（文件版含模块名；控制台版省略 asctime 日期部分保持简洁）。

## 7. 验收测试（文档/测试 AI 提供）

1. `test_日志文件创建与基础写入`：serve 启动后 logs/kb.log 存在且含"服务启动"
2. `test_请求访问日志覆盖REST与MCP`：发起 REST 与 MCP 请求后，日志含对应方法/路径/状态码
3. `test_检索命中数记录`：search 后日志含 `命中=N` 且 N 与响应一致
4. `test_日志轮转`：设 KB_LOG_MAX_BYTES=1024 超限写入后产生 .1 备份文件
5. `test_敏感信息不入日志`：写入含 `api_key=xxx` 的内容后，日志全文不含 `xxx`
6. `test_配置项生效`：KB_LOG_LEVEL=WARNING 时 INFO 不落盘
7. 回归：原 65 项测试全绿（日志为旁路能力，不得破坏现有行为）

## 8. 节点拆分（进 P2 路线图）

| 节点 | 内容 | 门禁 |
|---|---|---|
| N17 | 配置项 + logging_setup + 生命周期日志 + 文件轮转 | 标准（测试 1/4/6/7） |
| N18 | 请求中间件 + 检索/LLM 埋点 + 敏感过滤 | 标准（测试 2/3/5/7） |

## 9. 明确不做（YAGNI）

- 不引入结构化 JSON 日志（人读优先；未来 Web UI 需要时再加 formatter，接口已留）
- 不做日志聚合/远程上报（本地优先）
- 不给 board.py/CLI 加独立文件日志（经 kb REST 已覆盖，见第 1 节）
- 不做基于日志的统计报表（先积累数据，P2-4 CLI 增强时再考虑 `kb log-stats` 类命令）
