# 安全策略（Security Policy）

> English: [SECURITY_EN.md](SECURITY_EN.md)

> kb 是本地优先的 Agent 记忆与知识服务，默认面向**单机、单用户**场景设计。
> 本文档说明默认安全边界、已知风险面与加固方式。

## 默认安全边界

| 层面 | 默认行为 | 说明 |
|---|---|---|
| 网络监听 | `127.0.0.1:8000`（仅本机回环） | `KB_API_HOST` 可改；非回环 + 未鉴权时启动会打印醒目警告并记录 warning 日志（N29） |
| 鉴权 | `KB_API_KEY` 为空 = 不鉴权（本地零摩擦） | 非空 = 启用 Bearer / X-API-Key 鉴权（N19），`/healthz` 白名单除外 |
| 数据落盘 | 全部本地（ChromaDB + 日志），不上传 | 断网完整可用；无遥测 |
| 敏感命名空间 | `KB_SENSITIVE_NAMESPACES` 命中强制本地 | 命中该名单的 namespace 不走云端 LLM |

## 加固建议

1. **对外监听必设 key**：如需 `KB_API_HOST=0.0.0.0`（局域网/容器访问），务必设置 `KB_API_KEY`，并以 HTTPS 反向代理（如 nginx/caddy）终结 TLS。
2. **key 管理**：key 只写入本机 `.env`（已被 `.gitignore` 排除），禁止入库、禁止写入代码或提交记录。
3. **网页入库**：`/api/v1/webpages` 会抓取任意 URL，请勿对不可信来源开放调用权限。
4. **敏感数据**：将含隐私的 namespace 加入 `KB_SENSITIVE_NAMESPACES`，确保相关问答走本地模型。
5. **目录监听**：`KB_WATCH_DIR` 指向的目录会被自动摄取，避免指向含系统敏感文件的目录。

## 已知限制（诚实披露）

- 单 API Key，无用户级权限/多租户隔离（本地单用户定位）。
- 无内建 TLS，需外部反代终结。
- 无速率限制与请求审计（本地回环场景下影响有限）。
- MCP 工具描述与 REST 同栈：鉴权同样由 `KB_API_KEY` 覆盖。

## 报告漏洞

请通过 GitHub 仓库的私有渠道（Security Advisories / 私信维护者）报告，勿直接开公开 issue。
修复将在验证后尽快发布，并在发布说明中致谢报告者。
