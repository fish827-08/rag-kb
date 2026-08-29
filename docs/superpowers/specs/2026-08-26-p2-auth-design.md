# P2-2 API Key 鉴权实施设计（N19-N20）

- 日期：2026-08-26
- 状态：设计书（TASK-0047 产出），N19 spec 需人工评审后实施
- 依据：`docs/superpowers/plans/2026-08-24-p2-roadmap.md` §2（A2 方向草案）；`kb/config.py`（Settings，env_prefix=KB_）；`kb/api.py`（create_app 工厂 + 现有 ASGI 中间件模式）；AGENTS.md 本地优先/零常驻约束
- 动机：日志（P2-1）落地后审计链已有；鉴权补"谁能操作"，是多 agent 并行与局域网/手机挂 MCP 前的安全前置

## 0. 设计原则

- **本地优先、零摩擦默认**：`KB_API_KEY` 为空 = 不鉴权，单机回环行为完全不变
- **零新依赖、零常驻**：纯 ASGI 中间件 + 标准库 `hmac.compare_digest`，不引入 auth 库/JWT 服务
- **REST 与 MCP 同栈覆盖**：一个中间件拦截全部路由（含 `/mcp` 子应用、`/dashboard`）
- **先文档后代码**：N19 先过人工评审，再实现

## 1. 配置项（kb/config.py Settings）

新增一个字段（pydantic-settings，env_prefix=KB_ 自动映射 `KB_API_KEY`）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `api_key` | str | `""` | 空=不鉴权；非空=启用，要求请求携带匹配的 key |

- `.env` 增 `KB_API_KEY=`（空值模板，与 `.env.example` 同步）
- 不新增 key 轮转/多 key/过期机制（YAGNI，本地单用户）
- key 比较用 `hmac.compare_digest` 防时序攻击

## 2. 中间件设计

新增 ASGI 中间件 `ApiKeyMiddleware`（与现有 `_wrap_sse_charset`、请求访问日志中间件同模式，纯 ASGI 不依赖 FastAPI 依赖注入，确保覆盖 mount 的 `/mcp` 子应用）：

```
请求进入
  → 访问日志中间件（最外层，401 也记录，审计需要）
  → ApiKeyMiddleware：
      1) settings.api_key 为空 → 直接放行（降级模式）
      2) 请求 path 在白名单（§3）→ 放行
      3) 取 key：Authorization: Bearer <key> 优先，其次 X-API-Key: <key>
      4) 缺失/不匹配 → 返回 401 JSON：{"error":"UNAUTHORIZED","message":"missing or invalid api key"}
      5) 匹配（compare_digest）→ 放行
  → 路由处理
```

- 401 响应统一 JSON 格式（与现有 `{"error","message"}` 一致），`Content-Type: application/json; charset=utf-8`
- 中间件在 `create_app` 内注册，顺序：访问日志最外层 → 鉴权 → 业务；鉴权失败不进业务、不触发 LLM/检索
- MCP 子应用挂载在 `/mcp`，ASGI 中间件对主 app 生效即覆盖 `/mcp`（与访问日志中间件同原理，已有注释佐证）

## 3. 白名单

启用鉴权时仍放行的端点：

| 路径 | 理由 |
|---|---|
| `GET /api/v1/healthz` | 存活探针，协调者/监控/看板需无 key 探活；本身无敏感数据 |

- 其余全部需 key：`/api/v1/*`（memories/search/documents/ingest/ask/monitor/logs/config）、`/mcp/*`、`/dashboard/*`、`/docs`
- `/dashboard` 静态页本身不含数据，但其前端会调 `/api/v1/*`；启用 key 后看板需带 key（见 §5），故不单独放行
- 白名单只认方法+路径前缀，不认 query/body

## 4. 降级策略

| 场景 | 行为 |
|---|---|
| `KB_API_KEY` 未设/空串 | 中间件直通，等同 v1.x 行为（本地回环零摩擦） |
| 设了 key，请求无 Authorization/X-API-Key | 401 |
| 设了 key，key 错误 | 401（不区分缺失与错误，防探测） |
| 设了 key，healthz | 200 放行 |
| 服务启动时 key 非空 | 日志记一行"鉴权已启用"（不回显 key）；空则记"鉴权未启用（本地模式）" |

- 不做"启动时校验 key 强度"（本地用户自选，不强制长度/字符集，仅在文档建议 ≥32 随机字符）
- 不做按端点分级权限（读写同一把 key，YAGNI）

## 5. 客户端适配（N20）

- **board.py / orchestra 客户端**（`orchestra/client.py` 的 `_request`）：启动时从 `.env`/环境读 `KB_API_KEY`（复用 kb 的 Settings 或独立最小加载，避免 import kb 依赖）；非空则在所有请求加 `X-API-Key: <key>` 头；为空则不带（向后兼容）
- **kb CLI**：`serve` 不涉及（它是服务端）；若 CLI 有作为客户端调自身 API 的场景，同一套读 key 逻辑
- **MCP 客户端**（Claude Code/Cursor 等）：在 MCP 连接配置的 headers 里加 `Authorization: Bearer <key>` 或 `X-API-Key`；`.mcp.json` 模板补注释说明（不把真实 key 写进提交的 `.mcp.json`，走环境变量/本地覆盖）
- **看板**（`/dashboard`）：启用 key 后，前端 fetch 需带 key；方案：从 `localStorage` 读用户填入的 key，或由 serve 在本地注入一个短期 token（YAGNI，本期 N20 只做"前端提示需配置 key"+ 手动填，自动注入留后续）
- 401 时客户端给出明确提示"未配置或 API Key 不匹配，检查 KB_API_KEY"

## 6. N19-N20 节点划分

| 节点 | 内容 | 门禁 |
|---|---|---|
| N19 | 本 spec 人工评审 → `config.py` 加 `api_key` 字段 + `ApiKeyMiddleware` + 白名单/降级 + 单元测试 | spec 需人工评审（卡内红线） |
| N20 | `orchestra/client.py` 自动带 key；`.mcp.json`/文档更新；端到端验证（无 key 401 / 带 key 全端点过 / MCP 过 / 空 key 行为不变） | 标准门禁 |

## 7. 测试方案（TDD 红灯基准）

N19 单元测试（落 `tests/test_api_auth.py`，用 FastAPI TestClient）：
```python
def test_空key_不鉴权_所有端点放行():
    # settings.api_key="" → /api/v1/memories 等不带 key 返回 200/404 而非 401

def test_有key_无凭证_返回401():
    # settings.api_key="secret" → GET /api/v1/memories 无 header → 401 JSON

def test_有key_错误key_返回401():
    # X-API-Key: wrong → 401

def test_有key_Bearer正确_放行():
    # Authorization: Bearer secret → 200

def test_有key_XAPIKey正确_放行():
    # X-API-Key: secret → 200

def test_healthz_白名单_有key也放行():
    # settings.api_key="secret" → GET /api/v1/healthz 无 header → 200

def test_mcp端点_有key无凭证_401():
    # /mcp/ 无 key → 401（验证同栈覆盖）

def test_401响应格式():
    # 体为 {"error":"UNAUTHORIZED","message":...}，Content-Type 含 charset=utf-8

def test_key比较用compare_digest():
    # 代码审查/断言使用 hmac.compare_digest（不写 == 明文比较）
```

N20 端到端：
- 真服务：空 key 时 board.py status 正常；设 key 后不带 key 的 board.py 报 401 提示，带 key 正常
- MCP 客户端带 key 可连通
- 全量回归绿（既有测试在空 key 模式下全部不受影响）

## 8. YAGNI（明确不做）

- 不做 JWT/OAuth2/多租户/角色权限（本地单用户，一把 key 足够）
- 不做多 key/key 轮转/过期/吊销（轮换靠改 `.env` 重启）
- 不做用户体系/登录页（无浏览器登录态）
- 不做 TLS/HTTPS 终止（本地回环/局域网由反代或 ssh 隧道负责，kb 不内置证书）
- 不做按 IP 白名单（绑定 `api_host=127.0.0.1` 已是默认网络隔离；需局域网时用户自行配 key）
- 不做请求体签名/防重放（本地网络威胁模型不涉及）
