# A4 易用性实施设计（CLI 增强）

- 日期：2026-08-28
- 状态：设计书（先文档后代码）
- 依据：ROADMAP.md A4 节；`docs/superpowers/plans/2026-08-24-p2-roadmap.md` §4（P2-4：CLI 优先，Web UI 最后做）；`kb/cli.py`（现有 add/search/info/serve/forget/dedup）；`kb/governance.py` compute_stats（治理统计纯函数）
- 范围决策（2026-08-28 人工确认）：**Web UI 砍掉不开发**——评估报告结论维持（不做 Web UI/多用户/商业化），本 spec 仅覆盖 CLI 增强

## 1. 需求

### 1.1 问题

- `kb info` 只输出 records/device/llm 三行，记忆库的"健康度"（类型分布/热度/陈旧度）无终端入口
- 终端用户问 kb 必须起 REST 服务或经 agent，无快捷问答入口
- A3 治理元数据（access_count/last_accessed）与 A3.5 评测能力（eval）缺 CLI 暴露

### 1.2 目标

- `kb stats`：记忆库统计一览（类型分布 / 访问热度 top / 陈旧分布），纯读无副作用
- `kb ask`：终端快捷 RAG 问答（直连 KBService，不经 HTTP），LLM 不可用时友好降级提示
- `kb eval`：挂接 A3.5 评测框架（见 `2026-08-28-a35-retrieval-quality-design.md` §3.5，本 spec 不重复）

### 1.3 非目标

- 不做 Web UI / TUI 框架（rich 表格够用）
- 不做 `kb board`（orchestra 看板属 B 线维护模式，不扩）
- 不做交互式 REPL（用户终端主要与 agent 交流，kb CLI 是工具不是会话端）

## 2. 命令设计

### 2.1 kb stats

```
kb stats [--stale-days 90] [--top 5]
```

输出（rich 表格，复用现有 console 风格）：

```
记忆库统计
├─ 总记录数 / device / llm 状态          （复用 service.stats）
├─ 类型分布表：memory / doc_chunk / web_chunk 各自条数与占比
├─ 访问热度 top N：content 摘要 + access_count + last_accessed
└─ 陈旧分布：超 N 天未命中条数（默认 90，吃 governance.compute_stats）
```

- 数据源：`store.iter_all()` 内存聚合 + `governance.compute_stats`（已有纯函数复用，不重算）
- 空库：各表空态提示，不报错

### 2.2 kb ask

```
kb ask "问题文本" [--top-k 5]
```

- 直连 `KBService.ask()`（与 REST /ask 同一逻辑，零协议开销）
- 输出：答案 + 来源列表（id/score/摘要，rich 表格）
- LLM 不可用（LLMDisabledError）：友好提示（Ollama 启动指引 / DeepSeek Key 配置指引），退出码 1；**检索结果仍输出**（无 LLM 也有价值：展示 top-k 命中，标注"仅检索未生成"）

### 2.3 kb eval

见 A3.5 设计书 §3.5（`--file/--top-k/--mode/--rerank/--sparse`），CLI 层薄封装 `kb/eval.py` 的 `run_eval`。

## 3. 里程碑拆分

| 节点 | 内容 | 门禁 |
|---|---|---|
| **N28** | `kb stats` + `kb ask` 命令实现 + 单测（ask 的 LLM 降级路径 mock）+ 帮助文档 | 标准 |

## 4. 测试策略

```python
def test_stats_type_distribution():      # 三类记录条数与占比正确
def test_stats_empty_library():          # 空库不报错，输出空态
def test_stats_stale_count():            # 陈旧计数与 governance.compute_stats 一致
def test_ask_prints_answer_and_sources():# mock LLM → 输出含答案与来源
def test_ask_llm_disabled_still_shows_hits():  # LLM 不可用 → 退出码 1 + 检索结果仍输出
```

- stats 纯读测试走 env_isolated + 真实小模型语料；ask 的 LLM mock 走 service 替身（对齐 test_n10 模式）

## 5. 配置项

无新增配置（复用 KB_LLM_MODE / KB_* 既有配置；`--top-k` 为命令参数非环境变量）。

## 6. 风险与避坑

| 风险 | 规避 |
|---|---|
| `kb ask` 首次触发嵌入模型加载慢（CPU BGE-M3 ~30s） | 输出前置提示"正在加载模型…"；文档标注 CLI ask 适合服务已暖（serve 常驻）或轻量模型场景 |
| stats 大库 iter_all 慢 | 个人级规模（<10万）可接受；表格条目数有 --top 上限 |
| ask 与 serve 同时写库 | ChromaDB PersistentClient 进程级隔离，双进程读写同一目录有锁竞争风险——文档标注：ask 命令建议 serve 停止时使用（或接受最终一致） |
