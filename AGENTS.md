# AGENTS.md — kb 项目 AI 开发指南

> 本文件是所有 AI 编码助手（Trae / Claude Code / Cursor 等）在本仓库工作的第一入口。先读本文件，再读设计文档，然后才开始干活。

## 0. 强制规则

1. **开始任何编码工作前，先完整阅读设计文档**：`docs/superpowers/specs/2026-08-23-kb-memory-service-design.md`。它是本项目唯一的事实来源（single source of truth）。
2. 实现与设计文档冲突时：停下来向用户确认，先修订文档再改代码。
3. 仓库内除设计文档与本文件外的所有文档均已作废（见第 1 节），**禁止从中提取需求、架构或约定**。

## 1. 文档有效性声明

| 路径 | 状态 | 说明 |
|---|---|---|
| `docs/superpowers/specs/2026-08-23-kb-memory-service-design.md` | ✅ 唯一有效设计 | 需求、架构、API、里程碑全在这里（已实施完成，2026-08-24 收口） |
| `AGENTS.md`（本文件） | ✅ 有效 | AI 工作规则 |
| `PROJECT.md` | ✅ 有效 | **接力文档**（项目状态/进度看板/接手指南；新 AI 必读第 2 份，2026-08-24 新增） |
| `docs/USER_GUIDE.md` | ✅ 有效 | **用户使用手册**（人类用户入口：kb 使用 + MCP 挂载 + orchestra 协作流程，2026-08-24 新增） |
| `docs/superpowers/plans/2026-08-23-kb-dev-nodes.md` | ✅ 有效 | 节点计划（门禁与验收测试，N1-N16 全部完成） |
| `docs/superpowers/specs/2026-08-24-logging-design.md` | ✅ 有效 | P2-1 日志设计（N17-N18 实施依据） |
| `docs/superpowers/plans/2026-08-24-p2-roadmap.md` | ✅ 有效 | P2 路线图（日志→鉴权→遗忘→Web UI/CLI） |
| `README.md` | ✅ 有效 | 项目说明（N16 按新定位重写：快速开始 / MCP 挂载 / 端点速查） |
| `.mcp.json` | ✅ 有效 | 项目级 MCP 挂载配置（Claude Code 等在本目录启动即连 kb 服务） |
| `_archive/`（含旧 `README.md`、`ROADMAP.md`、`step_doc/`、`notes/`、`rag_kb/`、`app/`、`demo.py`、`test_py/`、`data/`） | ❌ 已归档（2026-08-23/24） | 旧学习项目全部内容；**禁止参考其架构与实现，不在其上续写** |

## 2. 项目定位（一句话）

本地优先、完全免费的 Agent 记忆与知识服务：Windows 单进程常驻（`python -m kb serve`），REST + MCP 双协议，向 Claude Code / Cursor / 自建 Agent 提供记忆写入、文档/网页入库与混合检索（向量 + BM25/RRF）；无 LLM 时存取与检索完整可用。

## 3. 硬件与资源约束（务必遵守）

- 用户机器：**GPU 6GB 显存 + 16GB 内存，Windows**。
- 本地 LLM 默认 `qwen3:4b`（2026-08-23 基准定型：实测 3.2GB 显存 @num_ctx 4096、80.2 tok/s、RAG 基准 4 项全过）；低配选项 `qwen3:1.7b`（~1.8GB、101.4 tok/s）。**两模型禁止同时加载**（合计 + embedding 超 6GB），路由/压缩/回答统一用当前配置的单个模型。
- 显存预算：BGE-M3 fp16（约 1.1GB）+ `qwen3:4b`（3.2GB）= 4.3GB，可共存（已实测验证）；OOM 时 `KB_DEVICE=cpu` 或切 `qwen3:1.7b`。
- `/ask` 智能路由（设计文档第 7 节）：本地守门（复杂度路由 / 上下文压缩 / 简单问题直答 / 隐私隔离 / 离线兜底），难题发云 `deepseek-v4-flash`；`KB_LLM_MODE=local|auto|cloud`，默认 auto。护栏参数硬编码默认值：`think:false`、`temperature 0.2`、`num_ctx 4096`、`max_tokens 800`、检索上下文 ≤2000 token 截断、强约束 system prompt。
- **禁止**建议 7B 及以上本地模型；**禁止**引入需要额外常驻服务的组件（Qdrant、独立向量库服务等）。

## 4. 开发约定

- Python 3.10+；包结构与文件职责严格遵循设计文档 4.2 节，不新增设计外的顶层模块。
- **禁止引入 LangChain**（唯一例外：独立小包 `langchain-text-splitters` 用于切分）。
- 一切配置走 `kb/config.py`（pydantic-settings）+ `.env`，禁止硬编码。
- 代码注释、docstring：中文。
- 每个里程碑配套 pytest 测试；先测试后重构。
- 依赖只加设计文档第 3 节列出的包；新增依赖需先向用户说明理由并更新设计文档。
- 遵循设计文档第 13 节迁移与范围：旧目录已归档至 `_archive/`（2026-08-23 人工指示，保留历史不删除），不保留兼容层。

## 5. 角色分工与开发流程（重要）

本项目由三类角色协作：

| 角色 | 职责 |
|---|---|
| **文档/测试 AI** | 维护设计文档与节点计划；编写节点验收测试（pytest）；执行自动化测试与性能基准；**不写业务实现代码** |
| **开发 AI** | 按节点计划实现代码（读设计文档 + 节点计划）；通过节点全部验收测试；**不修改设计文档**（发现冲突时提出，由文档 AI 修订） |
| **人工（用户）** | 核心功能验收：设计文档 2.3 节成功标准逐条验证、本地 vs 云端回答质量主观对比、Claude Code 实际挂载体验、GPU 加速交互选择流程、节点门禁终审 |

### 开发流程（节点门禁制）

1. 开发严格按节点计划串行推进：**`docs/superpowers/plans/2026-08-23-kb-dev-nodes.md`**。
2. 每个节点必须：全部验收测试通过 → git 提交（含测试）→ 打节点 tag（如 `node-01`）→ 人工确认后才能进入下一节点。**禁止并行开发多个节点，禁止跳过未通过门禁的节点**。
3. 节点验收测试由文档/测试 AI 提供或评审；开发 AI 也可补充单元测试，但验收测试不得自行修改（发现测试本身有误时提出，由文档 AI 修正）。
4. 红线：**任何 AI 不得代替人工声称"验收已完成"**；自动化测试全绿只是进入人工验收的前置条件。交付时明确说明"自动化已覆盖 / 待人工验证"两部分。

### 自动推进模式（2026-08-23 人工授权启用）

- 文档/测试 AI 通过子代理（subagent）驱动逐节点开发：子代理按 TDD 实现当前节点（先落验收测试→红→实现→绿→全量回归→提交+tag），文档/测试 AI 随后执行自动化核验（测试与计划逐字比对、全量回归、敏感数据扫描、契约检查）。
- **标准节点**：核验通过即自动进入下一节点，无需人工逐节点确认。
- **人工门禁点（必须停下等人工确认）**：N8（M2 收口）、N12（M3 收口）、N16（M4/终验收口）；以及任何测试失败、契约偏离、敏感数据风险、新增依赖的场景。
- 子代理红线：不得修改设计文档、节点计划、AGENTS.md 与既有验收测试；**不推送远程**（由文档/测试 AI 核验后统一推送）；提交信息格式 `节点NN: 简述`。
- 红线不变：任何 AI 不得代替人工声称"验收已完成"；里程碑收口节点照常出具"自动化已覆盖 / 待人工验证"两部分报告。

### Git 与敏感数据规范

- 远程仓库：`https://gitee.com/little-fishy/rag-kb.git`（仓库地址可出现在文档中）。
- **访问凭据（gitee key）、API Key、任何密钥严禁写入任何项目文件、代码、文档、提交记录**；推送凭据通过本机 git 凭据管理器或临时 URL 传递，用后不落盘。
- `.env` 只存本地，已被 `.gitignore` 排除；提供 `.env.example` 作为模板（只含键名与空值）。
- 提交信息用中文，格式：`节点NN: 简述` 或 `文档: 简述`；每节点一个 tag。

## 6. 实施顺序

> **状态：✅ 已全部完成（2026-08-24 收口）**。N1-N16 按计划串行推进完毕，M1-M4 四个人工门禁点均通过，tag `node-01` ~ `node-16` 及版本 tag `v1.0.0` 均已推送远程。

P1（N1-N16，kb v1.0.1）已收口；agent-orchestra MVP 已合入 main（`orchestra/`，见 PROJECT.md）。当前进入 **P2**：按 `docs/superpowers/plans/2026-08-24-p2-roadmap.md` 顺序推进——**P2-1 日志（N17-N18）→ P2-2 鉴权 → P2-3 遗忘机制 → P2-4 Web UI/CLI（CLI 优先）**；Qdrant 已砍掉（违反零常驻约束）。不跳步；各功能 spec 定稿后仍走节点门禁制。后续新需求（P2）先修订设计文档再立项，不在本节点计划上续写。

## 7. 环境备忘

- CUDA 版 PyTorch：本机 Python 3.10 对应 **torch 2.11.0+cu128**（已装好并验证，2026-08-23；cu124 轮子在新版已停发，勿照搬旧教程）。官方源 `download.pytorch.org` 国内仅 ~0.6MB/s，**下载走阿里云镜像** `https://mirrors.aliyun.com/pytorch-wheels/cu128/`（实测 ~6.5MB/s，curl 需带 `-A "Mozilla/5.0"`，否则 403）；轮子文件名含 `+`，经终端传路径会被转义，安装时用 PowerShell 变量构造路径再传给 pip。
- HuggingFace 模型下载走镜像：`HF_ENDPOINT=https://hf-mirror.com`。
- BGE-M3（约 2GB）与 Ollama 模型均已预下载缓存；模型缓存与 ChromaDB 数据均落本地目录，**断网可完整运行是验收标准之一**。
- Ollama 国内加速：安装包走 GitHub Releases 加速代理（可用前缀按序尝试 `gh-proxy.com` / `ghproxy.net` / `ghfast.top` / `ghproxy.cn`，旧域名 ghproxy.com 已停服，完整可用列表见设计文档第 12 节）；模型用魔搭前缀拉取（如 `ollama pull modelscope.cn/Qwen/Qwen3-4B-GGUF`），拉完 `ollama cp` 改名。实际模型名以 `ollama list` 为准，若与服务配置的模型名不一致，先提醒用户改名而不是改代码。
- Ollama 模型目录：已迁移至 **`D:\ollama_models`**（用户级环境变量 `OLLAMA_MODELS`，2026-08-23）。注意：**从 AI 沙箱终端启动的 Ollama 会继承文件限制导致数据库读写失败**——若发现 Ollama 异常，提醒用户从开始菜单/托盘正常启动，而不是在沙箱终端里拉起。
- qwen3 系列默认开启思考模式，RAG 场景必须禁用：本地走 Ollama 原生 `/api/chat` 并传 `"think": false`（openai SDK 不透传该参数，故本地不用 SDK）；云端走 openai SDK。2026-08-23 基准：关思考后 4b 80.2 tok/s、1.7b 101.4 tok/s，均远超 8 tok/s 达标线；无约束交互下的幻觉/会话污染在 RAG 护栏下未复现。
- 用户环境准备状态见设计文档第 12 节；开发前如发现某项未就绪（如 torch 为 CPU 版），先提醒用户而不是绕过。
