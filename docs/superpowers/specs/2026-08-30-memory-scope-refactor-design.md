# 记忆范围重构设计（client + project 双键隔离，agent_id 降级为主键）

- 日期：2026-08-30
- 状态：设计书（先文档后代码）
- 依据：2026-08-29 实战验证结论——agent_id（AI 自报）不可信，AI 可随意编造，导致审计无法溯源、隔离语义空洞；client 已由 MCP 握手 clientInfo 自动识别（框架级可信）；本次将**记忆归属从「AI 自报 agent_id」改为「环境承载的 client + project 双键」**，agent_id 从身份参数中移除，主键由服务端生成（现状已是 uuid4，无需 AI 参与）。
- 上位文档：`2026-08-29-agent-isolation-design.md`（本 spec 取代其隔离语义，审计/查询入口延续其模式）

## 1. 需求修正（v2 结论）

### 1.1 实战暴露的问题

1. **agent_id 不可信**：agent_id 由 AI 自报（LLM 工具参数），AI 可随意编造字符串，用户无法判断记录归属是否真实；隔离与审计建立在不诚实前提上。
2. **client 本应可信**：客户端来源可由 MCP 握手 clientInfo 自动识别（框架级，AI 无法伪造），但现状 REST 默认 `default`、MCP 仍是 AI 传参，未充分利用。
3. **CSK 项目归属不明**：对话在哪个目录/项目下执行可精确判断（cwd），未利用；默认对话（无项目）应有"该客户端默认记忆"兜底。
4. **调用负担重**：8 个 MCP 工具都要 AI 额外编 agent_id，是无效脑力负担；AI 应只专注存取与查询。
5. **token 浪费**：双语长描述 + 必填身份参数，session 固定成本偏高（估算 ~2.4K/工具 schema + ~2K/SKILL 触发）。

### 1.2 修正后目标

- **隔离键改为 `(client, project)` 双键**：
  - `client`：来源客户端，**框架自动识别**（MCP clientInfo；REST 默认 `HTTP`；CLI 默认 `CLI`），AI 不传、不可编造；
  - `project`：任务/项目归属，**环境承载**（MCP 连接配置 `x-kb-project` 头声明；CLI 自动取 cwd 目录名；REST 显式传）。未声明 → 归入**该客户端的默认记忆桶**（`project=""`）。
  - memory 类型按 `(client, project)` 严格隔离；doc/web 共享知识库不变。
- **agent_id 降级为服务端主键**：记录 ID（uuid4）服务端生成；`agent_id` 不再作身份/隔离/审计键。新写入时 `Record.agent_id` 落为 project 值（若无则 `default`）仅作旧查询层兼容冗余，不再承担隔离语义。
- **MCP 工具移除 `agent_id` 入参**：8 个工具签名去掉 agent_id（不再必填），`client`/`project` 由环境承载；AI 无需自曝身份。
- **描述双语化**：MCP 工具描述/服务端错误消息按系统语言环境（`KB_LANG`，默认 auto 检测系统 locale）选择中文或英文，避免双语描述 double token 成本。
- **遗忘机制可验证**：衰减/去重/遗忘候选的时间基准改为**可注入时钟**（单元/集成秒级验证机理）；并提供**加速实验法**（临时缩小半衰期跑真实服务，端到端真实运行证据）。写入 USER_GUIDE 验证章节。

### 1.3 非目标（YAGNI）

- 不做认证级身份校验（client+project 是归属划分，非安全边界；鉴权仍由 `KB_API_KEY` 承担）
- 不做真机长周期遗忘等待验证（以时钟注入 + 加速实验替代）
- 不做跨客户端记忆共享/ACL（共享走 doc/web）
- 不迁移既有数据改写（旧记录缺 client/project 回落 default，零迁移）

## 2. 架构

### 2.1 身份与隔离语义（v2）

```
写入：write_memory(content, tags?)  → client = clientInfo.name（框架自动）
                                      project = 连接声明 x-kb-project（缺省 ""=默认桶）
                                      Record.agent_id := project 或 "default"（兼容冗余，非隔离键）
                                      Record.id := 服务端 uuid4（主键）

检索/读/改/删/ask：无需身份参数
      - memory：仅返回 (client, project) == 调用方上下文 的记录（强制隔离）
      - doc_chunk / web_chunk：全部可见（共享知识库）
```

调用方上下文（每请求提取）：
- client：MCP = clientInfo.name（兜底 `HTTP`）；REST = `HTTP`；CLI = `CLI`
- project：MCP = 请求头 `x-kb-project`（连接配置声明）；CLI = cwd 目录名（`--project` 可覆盖）；REST = 请求参数 `project`（缺省默认桶）

### 2.2 数据模型（models.py）

`Record` 字段调整：

```python
id: str = field(default_factory=lambda: uuid4().hex)   # 主键，服务端生成（不变）
agent_id: str = "default"   # 兼容冗余：新写入=project（无则 default）；不再作隔离键
client: str = "default"     # 来源客户端（结构保留；隔离键之一）
# 新增归属键（沿用现有 namespace 字段亦可，二选一定稿）
project: str = ""           # 项目/任务归属（默认桶=空串）；to_metadata/from_chroma 同步
```

- `to_metadata()`：增加 `"project"` 键；`from_chroma()`：`metadata.get("project", "")`（旧记录回落默认桶，零迁移）
- 隔离过滤改为 `(client, project)` 双键比较（不再比较 agent_id）

### 2.3 检索隔离（retriever.py + service.py）

`search(query, top_k, mode, type, tag, client=..., project=...)`：
- 融合后过滤阶段追加：
  - `rec.type == memory` 且 `(rec.client, rec.project) != (调用方 client, project)` → 跳过
  - doc/web 不过滤
- `ask` 内部检索同样透传 (client, project)

### 2.4 存取审计（audit.py，按 (client, project) 分文件）

文件名：`agent-audit/<客户端>__<项目>.log`（project 为空 → `<客户端>__default.log`）
- 不再含 agent 段（agent 已无意义）；任务更名=改连接声明的 project，文件名随之变化
- 行内不含身份（沿用文件名承载 + 查询侧解析补回）
- 其余（50 字符摘要、utf-8-sig BOM、30 天轮转）沿用 `2026-08-29-agent-isolation-design.md` §2.4

### 2.5 查询入口（变化）

- REST：`GET /api/v1/audit?client=<客户端>&project=<项目>`（原 `agent` 参数废弃或按 client 兼容）
- CLI：`kb audit --client TraeWork [--project kb] [--action write] [--days 7]`（缺省取当前 cwd 的 project）

### 2.6 双语（i18n）

- 配置 `KB_LANG`：`zh` / `en` / `auto`（默认 `auto` = 检测系统 locale，中文系统用中文，否则英文）
- 服务端错误/校验消息走 `kb/i18n.py` 的 `msg(key, lang)` 字典；MCP 工具 description 注册时按 lang 选一套（消除双语 double cost）
- 审计日志内容不翻译（摘要原文）

## 3. API 变更

| 端点/工具 | 变更 |
|---|---|
| MCP 8 工具 | **移除 `agent_id` 参数**；无 client 入参（clientInfo 自动）；project 不入函数签名（从连接头读取，见 §2.1） |
| `POST /api/v1/memories` | body 移除 agent_id；`client` 默认 `HTTP`；`project` 可选（默认桶） |
| `GET/PATCH/DELETE /api/v1/memories/{id}` | 同上去除 agent_id；隔离按 client+project |
| `POST /api/v1/search` / `ask` | 去除 agent_id；透传 client/project |
| 新增 | 无（审计查询参数改 client/project） |

## 4. 配置新增

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `KB_LANG` | `auto` | 消息语言：zh/en/auto（检测系统 locale） |
| （沿用）`KB_ACCESS_AUDIT_ENABLED` | `true` | 存取审计开关 |

## 5. 测试（验收要点）

1. 隔离按 (client, project)：client-A/project-P 写 memory，client-A/project-Q 与 client-B/project-P 均不可见/不可操作
2. 默认桶：无 project 的连接写入，归该 client 默认桶；同 client 有/无 project 互不可见
3. 共享知识不变：client-A 入库 doc，client-B 可检索
4. 旧数据兼容：无 project 元数据记录视为默认桶，可被检索
5. 主键服务端生成：write 返回 id，不依赖任何 AI 入参
6. 审计闭环：write/search/read/update/delete/ask/ingest 各一条 JSON 行；文件名 `client__project.log`
7. 双语：`KB_LANG=en` 下错误消息英文，`zh` 下中文，`auto` 按系统
8. 遗忘时钟注入：decay_factor 传 `now` 参数可拨到第 N 天，验证衰减/降权/遗忘候选；加速实验法（缩小半衰期）写入文档
9. 回归：既有测试按新隔离语义更新后全绿

## 6. 实施顺序

| 节点 | 内容 | 门禁 |
|---|---|---|
| B1 | models + i18n + service/retriever（双键隔离 + 时钟注入） | 标准（测试 1/2/3/4/5/8） |
| B2 | mcp 8 工具改签名（去 agent_id，clientInfo/client、连接头 project）+ REST 端点 | 标准（测试 1-5 通道层） |
| B3 | audit 文件名 client__project + 查询入口改造 | 标准（测试 6） |
| B4 | 双语（test 7）+ 文档同步（AGENT_PROMPT/SKILL/README/USER_GUIDE，含遗忘验证章节）+ 回归 | 标准（测试 9） |