# 记忆全共享设计（v3：去除 (client, project) 隔离）

- 日期：2026-08-31
- 状态：设计书（先文档后代码）
- 依据：2026-08-30 v2 实战验证结论——(client, project) 双键隔离在本地单用户场景不可行：
  1）**project 读取不理想**：AI 通过 HTTP 访问时拿不到目录环境，项目归属识别失败；
  2）**client 识别失真**：HTTP 通道统一识别为 `HTTP`，丢失真实客户端；
  3）框架识别再准，也架不住"读不到"——隔离键缺失使记忆整体不可见，违背外置记忆服务的初衷。
- 上位文档：`2026-08-30-memory-scope-refactor-design.md`（本 spec **取代其隔离语义**；审计/查询入口延续其模式）

## 1. 需求修正（v3 结论）

### 1.1 v2 实战暴露的问题

1. **project 读取失败**：MCP 连接头 `x-kb-project` 与 CLI cwd 目录名在真实客户端（HTTP 兜底通道）中不可靠，AI 无法感知自己在哪个项目下读取，导致记忆按 project 隔离后"读不到"。
2. **client 识别失真**：AI 经 HTTP 访问时，服务端一律识别为 `HTTP`，与真实客户端（TraeWork / 豆包等）脱节；即使 clientInfo 能识别 MCP 客户端，HTTP 通道天然丢失该信息。
3. **隔离反噬可用性**：本项目是**本地单用户**的外置记忆系统——记不住比记乱更致命。隔离缺失键值时全部记忆不可达，用户偏好/决策跨 agent、跨任务无法共享，违背"外置持久记忆"的产品定位。

### 1.2 修正后目标

- **去除 (client, project) 隔离**：所有记录（memory + doc_chunk + web_chunk）**全共享**，任何客户端、任何任务、任何 AI 均可检索 / 读取 / 更新 / 删除全部记忆——跨 agent、跨任务共享同一份用户记忆。
- **client / project 降级为"审计归类 + 元数据展示"**：字段结构保留、写入仍落值（框架能识别就识别，识别不到不影响可用性），仅用于存取审计按文件分类与记录元数据展示，**不参与任何可见性过滤**。
- **agent_id 维持兼容冗余**：记录主键服务端生成（uuid4），agent_id 仅旧查询层展示用。
- **存取审计保留**：`logs/agent-audit/<客户端>__<项目>.log` 机制不删（本地单用户仍有"谁存了/读了什么"的追溯价值）；client/project 识别不到时统一落兜底桶（`HTTP__default` 等），不阻塞主流程。

### 1.3 非目标（YAGNI）

- 不做认证级安全边界：client+project 本就是归属划分而非安全机制；鉴权仍由 `KB_API_KEY` 承担。全共享后如未来需要多用户/多租户隔离，**再引入正式鉴权与租户模型**，不做一次性修补。
- 不迁移既有数据改写：旧记录缺 client/project 照常共享可见，零迁移。
- doc/web 本就共享，无变化。

## 2. 架构

### 2.1 可见性语义（v3）

```
写入：add_memory(content, tags?)  → client/project 尽量自动识别（MCP clientInfo /
                                    HTTP 兜底 / CLI cwd），仅落审计与元数据
                                    Record.agent_id := project 或 "default"（兼容冗余）
                                    Record.id := 服务端 uuid4（主键）

检索/读/改/删/ask：无需身份参数、无可见性过滤
      - memory：全部可见（不按 client/project 过滤）
      - doc_chunk / web_chunk：全部可见（共享知识库，同前）
```

### 2.2 数据模型（models.py）

`Record` 字段**不变**（client/project/agent_id 结构保留），语义调整：

```python
id: str = field(default_factory=lambda: uuid4().hex)   # 主键，服务端生成（不变）
agent_id: str = "default"   # 兼容冗余：新写入=project（无则 default）；不再作隔离键（不变）
client: str = "default"     # 来源客户端（仅审计归类与元数据）
project: str = ""           # 项目/任务归属（仅审计归类与元数据）
```

- `to_metadata()/from_chroma()` 不变（project 键照常读写，旧记录回落默认桶，零迁移）
- **删除全部**基于 client/project/agent_id 的可见性比较

### 2.3 检索（retriever.py + service.py）

- `search()`：向量路与 BM25 路均**全量检索**（不再按 (client, project) 拆 where / 传 filter_fn），融合后过滤阶段删除 memory 隔离校验。
- `get_memory / update_memory / delete_memory`：删除 (client, project) 归属校验——记录存在即可读/改/删。
- `ask` 内部检索同样全共享。

### 2.4 存取审计（audit.py，不变）

- 文件名仍为 `agent-audit/<客户端>__<项目>.log`（project 空 → `__default.log`）；client/project 为**尽量识别**的审计归类，识别失败落兜底桶，不影响功能。
- 行内不含身份、50 字符摘要、utf-8-sig BOM、30 天轮转均不变。

### 2.5 查询入口（不变）

- REST：`GET /api/v1/audit?client=<客户端>&project=<项目>`
- CLI：`kb audit --client TraeWork [--project kb]`

### 2.6 全局接入规约（MCP server instructions，v3 补充）

- 目的：解决"客户端全局提示词"难题——Trae 的全局规则不走 MCP 协议不生效、手动加 skill 有 token 开销且体验差。
- 载体：`MCPServer(instructions=...)`（mcp SDK 2.0.0 原生支持）。**任何客户端挂载 MCP 后，
  instructions 在握手时自动注入 AI 上下文**——跨客户端（TraeWork / Claude Code / Cursor）全局生效，
  无需 skill、无需客户端全局规则；只在握手注入一次，token 开销远小于 skill 全文。
- 双语：文案存 `kb/i18n.py::_MCP_INSTRUCTIONS`（zh/en 两套），按 `detect_lang()`（KB_LANG=auto 检测
  系统 locale，中文系统用中文，否则英文）选一套注入；与工具错误消息共用同一语言检测。
- 内容（核心三条）：
  1. **无需事前探测**：kb 常驻默认正常，直接调用工具；误报"服务未启动"的根因是 agent 用
     WebFetch/curl 探测 localhost 被代理/沙箱拦截。只有工具调用真正报连接失败才提示启动服务。
  2. **全共享规约**：记忆全共享、无需 client/project、何时写入（偏好/决策/事实/红线/进度）、
     写前查重、敏感不入库、主动检索。
  3. **反馈极简**：成功只轻提"已记住你的偏好"；失败才说原因；不展示健康检查过程/工具名/ID/JSON。
- 兜底：未挂载 MCP 的客户端（自建 agent / HTTP 通道）用精简版 `skills/kb-memory/SKILL.md` 或
  `AGENT_PROMPT.md`——两者同样**删除"先 healthz 探测"步骤**，改为"调用失败才排查代理/启动"。

## 3. 行为变化对照（v2 → v3）

| 操作 | v2（隔离） | v3（全共享） |
|---|---|---|
| client-A 写 memory | 仅 client-A 同 project 可见 | 所有客户端/任务可见 |
| client-A 读他人 memory | FORBIDDEN / 404 | 记录存在即 200 |
| client-A 改/删他人 memory | FORBIDDEN | 可改/可删 |
| 检索 memory | where + filter_fn 隔离 | 全量检索，无过滤 |
| 审计文件名 | client__project | 不变（归类，非隔离） |
| doc/web | 共享 | 共享（不变） |

## 4. 测试（验收要点）

1. 跨 client/project 写入与检索全共享：任意组合均可见
2. 读/改/删无归属校验：任何人可操作，仅"不存在"返回 NOT_FOUND
3. 默认桶（project=""）任意客户端可检索
4. 检索结果不随调用方 client/project 变化（隔离过滤已移除）
5. 审计闭环、身份字段规约、时钟注入、旧数据兼容等维持原验收
6. 回归：改写隔离断言后既有测试全绿

## 5. 实施顺序

| 节点 | 内容 | 门禁 |
|---|---|---|
| V3-1 | 代码：retriever/service/mcp/api/cli 移除隔离校验（全共享） | 相关测试（本 spec 1-4） |
| V3-2 | 测试：test_agent_isolation / test_improvements_isolation_recall 改写为共享语义 | 相关测试全绿 |
| V3-3 | 文档同步：spec / AGENTS.md / README / USER_GUIDE / PROJECT | 人工确认 |
| V3-4 | **核心架构变更** → 全量测试（kb 套件；orchestra B 线冻结不跑） | 全量绿 + 人工确认 |