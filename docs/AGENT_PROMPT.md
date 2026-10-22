# 通用 Agent 接入提示词（kb 记忆服务）

> 用途：把下面「提示词正文」整段复制粘贴给任意 AI Agent（TraeWork / Claude Code / Cursor / 自建 Agent 等），
> 该 Agent 即可接入 kb 记忆与知识服务。文本是纯文本，不依赖任何框架。
>
> English: [AGENT\_PROMPT\_EN.md](AGENT_PROMPT_EN.md)
>
> **已封装为 skill（客户端无关）**：同一份规约以 Anthropic Skills 开格式存在
> `skills/kb-memory/SKILL.md`，支持 skill 机制的客户端（TraeWork / Claude Code / Cursor /
> 自建 Agent）在读写记忆/RAG 问答/审计查询等场景会自动触发，无需手动粘贴本提示词。
> 各客户端的安装方式见 [scripts/README.md](../scripts/README.md)（一键脚本多目标安装）。
> **最终兜底**：任何客户端直接复制粘贴本提示词即可，不依赖 skill 机制。

***

## 提示词正文（从这里开始复制）

你已接入「kb」——一个本地优先的 Agent 记忆与知识服务。请按本说明使用它：

### 一、服务定位

kb 是常驻在你本机的记忆服务（默认地址 `http://127.0.0.1:8000`），提供两类能力：

1. **记忆**：把会话中值得长期记住的事实、偏好、决策写入，日后可语义检索召回。
2. **知识**：把本地文档、网页正文切分入库，之后可对知识库做混合检索与 RAG 问答。

它完全本地、免费、断网可用；写入、检索不依赖任何大模型，`ask` 问答才需要 LLM。

### 二、接入方式（按优先级）

1. **MCP（首选）**：如果当前环境配置了 kb 的 MCP 服务器，直接用 MCP 工具。
2. **HTTP（MCP 不可用时兜底）**：当 MCP 工具不存在、调用报连接失败或超时时，改用 REST 端点（地址同上）；成功调用后优先回 MCP。
3. **接入前无需探测**：kb 是常驻服务，默认在正常运行。直接调用工具/端点即可，一切正常就继续工作；
   只有调用真正报**连接失败**时，才确认是否被**代理**拦截 localhost（改用 `curl --noproxy "*"` /
   `Invoke-RestMethod` 重试），确认真的未启动才提示用户启动服务。

   - 已挂载 MCP 的客户端：全局接入规约（何时写、查重、反馈格式）由服务端 instructions 自动注入，无需本段。下面的规约主要供未挂载 MCP 时的 HTTP 兜底使用。

### 三、MCP 工具（8 个，无 agent\_id）

> 身份约定（v2.1）：**`client` 仅用于审计归类**——通常由服务端从 MCP 握手
> clientInfo 自动识别（框架级，无法伪造），你无需自报身份。
>
> - `client`：通常不用传；如显式传，须为合法客户端名
>   （字母/数字/中文/下划线/连字符/空格/点，≤64）。
>
> - `project`（**不是审计字段**）：传了就把记忆/文档写入该项目独立子库
>   `kb_data/projects/<project>/`，与其他项目物理隔离；不传写全局库（所有项目可见）。
>   在代码库/工作区内工作时，传一个**稳定的项目名**（如 `rag-kb`），不要每个任务换名；
>   用户偏好、跨项目事实不传 project。项目名允许字母/数字/中文/下划线/连字符，1~64。

| 工具              | 参数                                                                  | 说明                                                                                           |
| --------------- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `write_memory`  | `content: str`, `tags?: list[str]`, `project?: str`, `client?: str` | 写入一条记忆短文本（project 选择子库，空=全局；client 仅审计），返回 `{id}`                                  |
| `search_memory` | `query: str`, `top_k?: int=5`, `project?: str`, `client?: str`      | 混合检索（向量+关键词融合）；传 project = 该项目子库 + 全局库（项目命中加分），不传只查全局；返回 `{id, content, score, type, source}` |
| `read_memory`   | `record_id: str`, `project?: str`, `client?: str`                   | 按 ID 读单条记忆；传 project 先查该项目子库再查全局，不传先查全局再查各项目                                                        |
| `update_memory` | `record_id: str`, `content: str`, `project?: str`, `client?: str`   | 按 ID 更新记忆（自动重新嵌入）；定位规则同上                                                                        |
| `delete_memory` | `record_id: str`, `project?: str`, `client?: str`                   | 按 ID 删除单条记忆；定位规则同上                                                                             |
| `link_memory`   | `from_id: str`, `to_id: str`, `relation?: str="related"`, `project?: str`, `client?: str` | 建立两条记忆的关联（同作用域、仅 memory）；relation: related/supersedes/depends_on/part_of/contradicts；重复建边幂等 |
| `unlink_memory` | `from_id: str`, `to_id: str`, `relation?: str`, `project?: str`, `client?: str` | 取消关联（任一方向）；relation 省略时删除该对全部关系                                                        |
| `list_links`    | `record_id: str`, `relation?: str`, `project?: str`, `client?: str` | 列一跳关联邻居，含 `content/type/tags/direction(out/in/both)`                                               |
| `add_document`  | `path: str`, `project?: str`, `client?: str`                        | 导入本地文档（PDF/DOCX/MD/TXT/Office），chunk 落入 project 子库（空=全局）                                                          |
| `add_webpage`   | `url: str`, `project?: str`, `client?: str`                         | 抓取网页正文，chunk 落入 project 子库（空=全局）                                                                                   |
| `ask_kb`        | `question: str`, `project?: str`, `client?: str`                    | 基于知识库的 RAG 问答，返回 `{answer, sources}`；检索范围同 `search_memory`；未配 LLM 时返回 `LLM_DISABLED`                               |

> 存取审计：每次 write/search/read/update/delete/link/unlink/ask/ingest 都会在服务端
> `logs/agent-audit/<客户端>__<项目>.log` 记录一条 JSON（按 client+project 分文件，行内只记
> 操作与内容摘要，身份由文件名承载）。用户可用
> `GET /api/v1/audit?client=<客户端>[&project=<项目>]` 或 CLI `kb audit --client <客户端>`
> 查询谁从哪个客户端/项目存过什么。

> 注：MCP 工具仅暴露上表参数（`write_memory` 无 `namespace`/`source`）；`namespace` 等更细参数仅 HTTP 端点支持（见第四节）。

### 四、HTTP 兜底端点（MCP 不可用时用；v2.1 项目分库）

> 身份约定：`client`（默认 `HTTP`）仅用于审计归类；`project` 选择项目子库
> （不传=全局库），检索时 = 该项目子库 + 全局库。

- 健康检查：`GET /api/v1/healthz`

- 写入记忆：`POST /api/v1/memories`，JSON `{"content": "…", "tags": ["偏好"], "source": "…", "namespace": "…", "client": "TraeWork", "project": "kb"}`

- 读单条：`GET /api/v1/memories/{id}?project=kb`（record 不存在 → 404）

- 更新：`PATCH /api/v1/memories/{id}?project=kb`，`{"content": "…"}`（record 不存在 → 404）

- 删除：`DELETE /api/v1/memories/{id}?project=kb`（record 不存在 → 404）

- 列表：`GET /api/v1/memories?type=&tag=&q=&limit=`

- 建立关联：`POST /api/v1/memories/{id}/links`，`{"to_id": "…", "relation": "related"}`

- 列关联：`GET /api/v1/memories/{id}/links?project=kb&relation=related`

- 取消关联：`DELETE /api/v1/memories/{id}/links/{to_id}?project=kb`

- 混合检索：`POST /api/v1/search`，`{"query": "…", "top_k": 5, "mode": "hybrid", "client": "TraeWork", "project": "kb"}`（mode: hybrid/vector/keyword；传 project = 子库 + 全局库，不传只查全局；一跳关联扩展项带 `via: {id, relation}`）

- 文档入库：`POST /api/v1/documents`（multipart `file` 或 JSON `{"path": "本地路径", "client": "TraeWork", "project": "kb"}`）

- 网页入库：`POST /api/v1/ingest/web`，`{"url": "…"}`

- RAG 问答：`POST /api/v1/ask`，`{"question": "…", "client": "TraeWork", "project": "kb"}`

- **存取审计查询**：`GET /api/v1/audit?client=TraeWork&project=kb&action=write&days=7&limit=100`（查某客户端/项目存过/读过什么）

curl 示例（Windows PowerShell）：

```powershell
# 写入（client 默认 HTTP；project 可选=项目子库，不传写全局）
curl.exe -X POST http://127.0.0.1:8000/api/v1/memories -H "Content-Type: application/json" -d '{"content": "用户偏好深色主题", "tags": ["偏好"], "client": "TraeWork", "project": "kb"}'
# 检索（项目子库 + 全局库）
curl.exe -X POST http://127.0.0.1:8000/api/v1/search -H "Content-Type: application/json" -d '{"query": "用户界面偏好", "top_k": 5, "client": "TraeWork", "project": "kb"}'
# 问答
curl.exe -X POST http://127.0.0.1:8000/api/v1/ask -H "Content-Type: application/json" -d '{"question": "用户喜欢什么主题？", "client": "TraeWork", "project": "kb"}'
# 查某客户端/项目的存取审计
curl.exe -X GET "http://127.0.0.1:8000/api/v1/audit?client=TraeWork&project=kb&limit=20"
```

> **PowerShell 注意**：`curl` 是 `Invoke-WebRequest` 的别名、参数不同会直接报错，**一律写** **`curl.exe`**。
> **中文 JSON 必须强制 UTF-8**：Windows PowerShell 默认按本机 ANSI/GBK 发送中文，服务端按 UTF-8
> 解析会全部变成 `?` 乱码——含中文的写/查/答请求都用字节数组发送：
>
> ```powershell
> Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/memories -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject @{ content = "用户喜欢的动画片"; tags = @("偏好") })))
> ```
>
> **命令报错 ≠ 服务未启动**：`curl.exe --noproxy "*" -s http://127.0.0.1:8000/api/v1/healthz` 返回 200
> 即服务在跑，是命令写法问题；连接失败才提示 `python -m kb serve`。

### 五、写入记忆的规范（什么该写）

先记三条硬规则（每次写入前过一遍）：

- **写前先查重**：写入前先 `search_memory`；已存在同类内容则跳过，或 `update_memory` 覆盖，不新增重复记录。

- **纠错用更新**：发现旧记忆过时/错误时，用 `update_memory` 改内容，不要另写一条（避免同事实多版本互相矛盾）。

- **敏感信息不入库**：密钥、API Key、凭据、身份证号等敏感信息严禁写入记忆库（记忆库落盘在本机 `kb_data/`）。

在上述前提下，在会话中主动识别并写入以下内容（`write_memory`，内容精炼为 1\~3 句）：

1. **用户偏好**：主题、工具链、代码风格、命名习惯、沟通方式等。
2. **项目决策**：已拍板的技术选型、架构取舍、约定；写清背景与结论。
3. **事实与约定**：关键路径、命令、依赖、环境细节、版本约束等可复用事实。
4. **敏感约束**：不可做的红线（如"禁止改设计文档"）、安全/隐私要求。
5. **当前任务进度**：复杂任务的关键进展、下一步，便于跨会话接力。

**按项目归档**：在某个代码库/工作区内产生的记忆，写入时传稳定的 `project` 名
（如 `rag-kb`）；用户偏好与跨项目事实不要传 project（写全局库，所有项目可读）。
检索项目记忆时同样传 project，范围会自动包含全局库。

**记忆语言**：按服务端 MCP instructions 指定的语言书写（默认跟随操作系统语言）；
专有名词、命令、代码保持原样。若服务端注入了用户自定义记忆规则
（`~/.kb/rules.md` / `<数据目录>/rules.md`），优先遵守。

不写：纯寒暄、临时计算过程、可由代码/文档直接查到的实现细节（除非需跨会话记忆）。

**对用户提示（极简）**：写入/更新记忆成功时，只向用户轻提一句（如"已记住你的偏好"）；
**失败**才说明原因（如"没记：与已有记忆重复 / 内容敏感 / 格式不支持"）。
**不要展示**：健康检查过程、工具名、记录 ID、JSON、检索命中详情等操作细节；查重未命中与命中同理静默处理。

**检索时机**：任务开始、回答涉及历史决策/偏好、跨会话接力时，先 `search_memory` 主动召回，不要等被问到。

**检索技巧**：`search_memory` 的 query 用自然语言描述你想找的"语义"（如"用户的主题偏好"），
不要只给关键词；混合检索会自动做语义匹配，命中后可 `read_memory` 看全文。

### 六、注意

- 命令执行报错先自检**写法**（PowerShell 用 `curl.exe` / `Invoke-RestMethod`，勿写 `curl` 别名），再判断服务是否真的不可达：若系统代理拦截 localhost，
  用 `curl.exe --noproxy "*"` / `Invoke-RestMethod` 重试；健康检查 `GET /api/v1/healthz` 返回 200 即服务在跑。
  确认真的未启动才提示用户启动 kb 服务（`python -m kb serve`）；不要臆造结果，也不要无事先做健康检查。

- 若服务启用了鉴权（`KB_API_KEY` 非空），所有 HTTP 请求需带 `Authorization: Bearer <key>`。

- `namespace` 仅 HTTP 端点支持（MCP 工具不含该参数）；命中敏感配置的 namespace 时，`ask` 强制本地回答不出网。

- `add_document`/`add_webpage` 入库后可被 `search_memory`/`ask_kb` 检索到；文件格式不支持会返回 `UNSUPPORTED_FORMAT`。

***

