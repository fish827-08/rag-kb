# 本地监控 Agent 实施设计（qwen3:4b 常驻 + 看板联动）

- 日期：2026-08-25
- 状态：设计书（TASK-0015 产出），待拆实现卡
- 依据：用户需求"qwen3:4b 挂载做监控汇总（免费安全），看板随 kb 服务启动常驻置顶"；TASK-0011 的 `logging_setup.py` 与 `kb/api.py` lifespan 模式；TASK-0014 看板（HTML 二期）；`kb/llm.py` 本地调用（已内置 stream:false/think:false）；AGENTS.md 显存约束（BGE-M3 1.1GB + qwen3:4b 3.2GB = 4.3GB 可共存）
- 硬约束（用户给定）：提示词 ≤1500 token（num_ctx 4096）、think:false、temperature 0.2、max_tokens 300、**强制本地不得路由云端**；零常驻（寄生 kb serve 进程内）

## 0. 设计原则

- **零常驻**：监控线程寄生 kb serve 进程内（同 KBWatcher 线程模式），不新增进程/服务
- **显存不新增**：复用本地 qwen3:4b（Ollama 按需加载，与 /ask 共用同一模型实例），监控每 `KB_MONITOR_INTERVAL` 分钟一次，不持续占用
- **只读监控 + 结论写入**：只读任务板/registry/交流窗，产出摘要写 `comm:monitor`（交流窗新频道）

## 1. 监控循环时序与数据流

```
kb serve lifespan 启动
  → 若 KB_MONITOR_ENABLED：启动 MonitorAgent 线程（Event 控制停止，同 watcher）
  → 若 KB_DASHBOARD_AUTOOPEN：webbrowser.open(KB_DASHBOARD_URL)（服务就绪后触发一次）

MonitorAgent 循环（每 KB_MONITOR_INTERVAL 分钟；KB_MONITOR_STARTUP_RUN=true 时启动即跑一轮）：
  1) 收集紧凑快照（进程内直读 service 层，零 HTTP）：
      任务板  ：list_records(tag="taskboard") → 每卡一行（TASK status assignee | title）
               → 只保留 pending/claimed（最多各 4 行）+ done/verified 各 1 行摘要
      worker  ：list_records(tag="registry") → 每 worker 一行（名字 模型 状态 最后活跃）
               → 最多 8 行
      交流窗  ：list_records() 过滤 tag 前缀 comm:*（排除 comm:monitor 自身）
               → 最近 5 条（source + 文本）
  2) 组装提示词（第 2 节模板）→ 调 llm.chat(messages, max_tokens=300, prefer="local")
  3) 成功：add_memory(摘要, tags=["comm:monitor"], source="kb-monitor")
  4) 异常兜底（LLM 不可用/调用失败/快照异常）：
      记 WARNING 日志（logging_setup 已配好 kb.* logger），跳过本轮不写 comm:monitor
      （避免刷屏），不崩溃、不影响服务主流程
lifespan finally → 置停止 Event → 线程 join 退出
```

## 2. 提示词模板（含上下文预算表）

**system**（固定）：
```
你是 kb 系统的本地监控助理。基于协作快照，用 ≤100 字中文总结当前协作状态：待办与进行中任务、各 worker 状态、近期重要事件。只陈述快照中的事实，不评价、不编造。
```

**user**（固定壳 + 快照填充）：
```
协作快照（{time}）：
【任务板】
{taskboard_section}
【worker】
{registry_section}
【交流窗】
{comm_section}
请输出 ≤100 字中文摘要。
```

**上下文预算表**（token 估算，1 中文字≈1.5 token 保守计）：

| 段 | 内容 | 预算（token） |
|---|---|---|
| system | 固定提示 | ~60 |
| user 固定指令 | 壳 + 分隔 | ~60 |
| 任务板 | pending/claimed 各 ≤4 行 + done/verified 各 1 行，每行 ~30 | ~240 |
| worker | registry ≤8 行，每行 ~15 | ~120 |
| 交流窗 | 最近 5 条，每条 ~30 | ~150 |
| 时间戳/杂项 | | ~30 |
| **合计** | | **≤ 700 token** |

- 快照在进程内已压缩（只取关键行），**总预算 ≤700 token，远低于 1500 硬上限**，为 num_ctx 4096 与输出留足余量
- 输出：`max_tokens=300` 足以覆盖 ≤100 字中文摘要（~150 token）

## 3. LLM 调用方式论证（二选一 → 结论：进程内直调 service 层）

| 维度 | A：进程内直调 `self.llm.chat(messages, max_tokens=300, prefer="local")` | B：走 `/ask`（service.ask 或 HTTP POST /ask） |
|---|---|---|
| 语义匹配 | 纯快照摘要，无检索需求 | /ask 强制检索知识库（top_k=5 + RAG 护栏），语义不符 |
| 强制本地 | `prefer="local"` 硬约束，绝不路由云端 | auto 模式含 COMPLEX 云端路由与敏感覆盖，需改全局 llm_mode 才能锁本地（污染其他能力） |
| token 控制 | max_tokens 精确 300，提示词自组装 | /ask 走分类（+5）+ 可能压缩 + 缓存，token 不可精确控制 |
| 缓存 | 无状态直调，不碰 /ask LRU 缓存 | 命中缓存可能返回旧摘要（快照过期） |
| 开销 | 进程内零网络 | HTTP 序列化 + 自身往返（无谓开销） |

**结论：采用 A（进程内直调 service 层）**。理由：监控是"对快照做摘要"而非 RAG 问答；直调可强制本地、精确控 token、零网络、不碰 /ask 路由与缓存。实现上 MonitorAgent 持有 `KBService` 引用（lifespan 中从 `app.state.kb` 获取），直接调 `self.llm.chat(...)`（`LLMClient._chat_local` 已内置 think:false/stream:false/temperature 0.2/num_ctx 4096，无需重复设置）。

## 4. 看板自启动与置顶方案

**自启动**（依赖 TASK-0014 看板 + kb 静态挂载 `/dashboard` 合入）：
- 配置 `KB_DASHBOARD_AUTOOPEN`（默认 true）与 `KB_DASHBOARD_URL`（默认 `http://127.0.0.1:8000/dashboard/`）
- 接线：lifespan 服务就绪后（首次 serve）调 `webbrowser.open(KB_DASHBOARD_URL)` 一次；未合入挂载前 URL 不可达，浏览器仅提示，服务不受影响
- 注意：**不**用 file:// 直接开本地 HTML——kb 无 CORS，file:// 下 fetch 会被同源策略拦截（看板设计书已定"同源挂载免 CORS"）

**置顶**（浏览器无法代码强制置顶，运维说明）：
1. 安装 Microsoft PowerToys：`winget install Microsoft.PowerToys`
2. 打开看板浏览器窗口
3. 选中该窗口，按 **Win+Ctrl+T** 切换"始终置顶"（再次按取消）
4. 可选：PowerToys「Always on Top」设置中按进程/窗口标题预设默认置顶

## 5. 配置项清单（KB_MONITOR_* 与 KB_DASHBOARD_*）

全部走 `kb/config.py`（禁止硬编码），`.env.example` 补键名与空值：

| 配置项 | 类型/默认 | 说明 |
|---|---|---|
| `KB_MONITOR_ENABLED` | bool / `True` | 是否启用监控线程 |
| `KB_MONITOR_INTERVAL` | int / `10` | 轮询间隔（分钟），`≥1`，非法回退默认 |
| `KB_MONITOR_STARTUP_RUN` | bool / `True` | 启动时立即跑一轮（便于验证） |
| `KB_MONITOR_MAX_TOKENS` | int / `300` | 摘要输出上限（护栏：≤300，硬约束） |
| `KB_DASHBOARD_AUTOOPEN` | bool / `True` | serve 启动自动打开看板 |
| `KB_DASHBOARD_URL` | str / `http://127.0.0.1:8000/dashboard/` | 看板地址（可覆盖） |

- 温度/think/num_ctx 走 LLMClient 护栏默认值，**不新增配置**（护栏参数硬编码原则）
- 快照行数上限（任务板 4+1+1、worker 8、交流窗 5）为代码常量，YAGNI 不配置化

## 6. TDD 红灯基准（落 `tests/test_monitor_agent.py`）

```python
def test_快照收集纯函数():
    # mock service.list_records 返回 taskboard/registry/comm 记录
    # build_snapshot() → 文本含【任务板】【worker】【交流窗】三段，行数受限

def test_提示词模板token预算():
    # 构造模板填入最大快照，估算 token <1500（且 <700 余量充足）

def test_LLM调用_prefer_local_max_tokens():
    # mock llm.chat，断言调用 kwargs 含 prefer="local" 且 max_tokens=300

def test_摘要写入comm_monitor():
    # mock 摘要返回 → add_memory 收到 tags=["comm:monitor"] 且 source="kb-monitor"

def test_LLM不可用兜底():
    # mock llm.chat 抛 LLMError → 不写 comm:monitor、记 WARNING、不崩溃

def test_interval非法回退():
    # KB_MONITOR_INTERVAL=0 → 回退默认 10（或启动即报错拒绝）

def test_看板自启动开关():
    # KB_DASHBOARD_AUTOOPEN=false 时不调用 webbrowser.open（mock）
```

**回归**：kb 全量测试保持全绿；监控为旁路能力，不得破坏既有行为（lifespan 中默认启用，测试 create_app 需可注入禁用）。

## 7. 边界与不做（YAGNI）

- 不做跨轮状态持久化/去重（每轮独立快照；高频刷屏由 INTERVAL ≥1 分钟控制）
- 不做监控摘要进 /ask 缓存（直调 llm.chat，天然隔离）
- 不做云端兜底（硬约束强制本地；云端不可用不影响——直调 prefer="local" 只认本地）
- 不监控 kb 自身进程健康（服务活着线程才跑，天然覆盖）；进程级监控留给系统看护
