# PROJECT.md — 项目接力文档

> **给任何接手的 AI 助手**：先读 [AGENTS.md](AGENTS.md)（工作规则与红线），再读本文档（项目状态），然后按第 5 节"接手指南"行动。两份文档读完即可接力，无需追问用户历史。
>
> 最后更新：2026-08-27 ｜ 维护者：文档/测试 AI（按 AGENTS.md 角色分工）｜ 本文件随每个里程碑/节点更新

## 1. 项目是什么（双子系统）

```
┌─────────────────────────────────────────────────┐
│  rag-kb 仓库（本仓库，Windows 本地优先）          │
│                                                 │
│  ① kb —— 本地 Agent 记忆与知识服务（核心产品）    │
│     python -m kb serve 常驻，REST + MCP 双协议    │
│     向 AI 助手提供记忆写入/混合检索/文档网页摄取    │
│                                                 │
│  ② orchestra —— 跨任务 AI 协作系统（实验系统）    │
│     kb 当共享任务板，协调者 AI 拆卡分发            │
│     worker AI（任意模型的其他 TraeWork 任务）领卡   │
└─────────────────────────────────────────────────┘
```

- **设计原则**：本地优先、完全免费、断网可用；显存预算 6GB（BGE-M3 + qwen3:4b 共 4.3GB）
- **唯一事实来源**：`docs/superpowers/specs/` 下的设计文档（见第 6 节索引）

## 2. 当前进度（看板）

### ① kb 记忆服务 — v1.0.1（生产可用，P1 收口）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1（N1-N6） | BGE-M3 嵌入、ChromaDB 存储、BM25+jieba、RRF 混合检索、CLI | ✅ 2026-08-23 |
| M2（N7-N8） | REST API（memories/search/documents/healthz）、设备四級检测 | ✅ |
| M3（N9-N12） | MCP 8 工具、/ask 智能路由（分类/压缩/缓存/隐私隔离/云端降级） | ✅ |
| M4（N13-N16） | 文档摄取（pdf/docx/md/txt）、网页抓取、目录监听、README | ✅ 2026-08-24 |
| v1.0.1 hotfix | top_k/mode/空内容校验 → 422、MCP 入口校验、SSE charset | ✅ 2026-08-24 |

- 测试：**271 项全绿**（`tests/`，含鉴权 9 项 + monitor 47 项 + A3 治理 60+ 项）
- 基准：混合检索 26ms、/ask 端到端 ~2s（本地 qwen3:4b）
- tag：`node-01`~`node-16`、`v1.0.0`、`v1.0.1`（均已推 Gitee）
- v1.0.2：JSON charset 已修复（TASK-0003/0038/0045 交付，含测试断言）
- **A2 鉴权已交付**（N19 ApiKeyMiddleware + N20 客户端自动带 key，TASK-0062/0064，空 key 不鉴权零摩擦）
- **monitor 纯文本模式默认 off**（TASK-0065）：本地无 LLM 完整可用（KB_MONITOR_LLM=off/auto）
- **A3 记忆治理全线交付**（2026-08-28，TASK-0066~0076，spec+双层实现）：
  - 规则层：访问频率衰减（λ=0.02）+ 语义去重（阈值 0.92，409 拦截）+ 新鲜度权重（β=0.05，上限 1.3×）——三者默认关，零行为变化
  - 维护面：`kb forget/dedup` CLI + `/api/v1/governance/stats|config` 端点 + 结构化审计日志（kb/audit.py）
  - 智能层：consolidation 基础框架（kb/consolidation.py + spec，置信度门槛 + human 兜底，默认关）

### ② agent-orchestra — B1/B2/B3 收口后转入冻结维护（2026-08-28）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| MVP（T1-T10） | board.py CLI 6 命令、协议三件套、orchestra-worker skill、20 项单测 | ✅ 2026-08-24 |
| 真机实验 | worker-1（另一 TraeWork 任务）TDD 实现 list-pending 子命令，全链路 pending→verified | ✅ 2026-08-24 |
| B1 三角色闭环 | 交流窗/批量模式（v1.2）/看板（watch+HTML+导航页）/监控Agent（按需）/worktree 隔离（v1.3）/registry 6 成员 | ✅ 2026-08-26 |
| 包化分层 | board.py 拆为 client/cards/registry/comm/worktree/watch/feedback 等模块（TASK-0028~0031） | ✅ 2026-08-26 |
| B2 反馈闭环 | FBK 卡三类型铁律 + 动态配额 + 超限仲裁 + comm:feedback 归档（协议 v1.4；FBK-0001~0006 六次实战） | ✅ 2026-08-26 |
| 调度监测架构 | DispatchAgent 四规则检测 + comm:dispatch 播报 + LLM 解耦降级 + 协调者循环常驻自动核验（协议 v1.5） | ✅ 2026-08-26 |
| B3 成本管控 | 协议 v1.6 §14 + b3.py（动态配额/rounds/summary 四标签）+ relation 关联窗口 + 中断恢复 + watch 轮次列 + 轮次告警（5/6，模型分级待拆） | 🚧 2026-08-27 |

- 测试：**207 项全绿**（`orchestra/tests/`，含 relation 15 + b3 22 + client auth 7 等）
- 任务板：TASK-0001~0065 共 65 卡（63 verified；0056/0063 重复卡作废记录）
- 协调者循环：**常驻自动核验运转中**（60 秒/轮：done 卡 merge→test→verify→push；open FBK 阻塞 + 新 FBK 自动广播）
- 协调者接力：**coordinator-prompt.md 已立唤醒提示词 + 接力状态节**（每次收口后必须更新），交接唯一入口
- skill 已装本机 `~\.trae-cn\skills\orchestra-worker\`（**硬链接**，git checkout 后可能断链需重装）

### ③ 发展总线 — v3（2026-08-27 战略调整：A 线唯一主线，B 线冻结维护）

项目总线见 **[ROADMAP.md](ROADMAP.md)**（树形路线+进度，人类入口）。战略调整后单主线：

| 主线 | 当前阶段 | 下一步 |
|---|---|---|
| **A：kb 记忆服务**（**唯一开发主线**，2026-08-27 战略调整） | A1+A2 交付；A2.5 生态合规进行中（LICENSE ✅ / GitHub 迁移待 key） | A3 记忆治理（遗忘/衰减/去重，唯一差异化）→ A3.5 检索质量（reranker/评测基准） |
| **B：orchestra 协作**（❄️ 维护模式） | B1/B2/B3/B3+ 全部收口冻结；B4 取消 | 仅修 bug + 测试绿 + 文档同步；自用照常（协调者循环/看板/交流窗）；A 线任务继续用它协作开发 |
| 支线 | — | 冻结（评估报告建议：不做 Web UI/多用户/商业化） |

- **战略调整（2026-08-27，依据《评估报告》双报告 + 用户决策）**：A 线 kb 为核心产品（"个人开发者的本地记忆 MCP server"），B 线 orchestra 冻结维护（自用脚手架）；远期若重启跨 Agent 方向，评估 A2A 协议兼容而非自建
- **LICENSE Apache-2.0 已补**（评估报告 P0 最高优先级项，法律合规闭环）
- 协议 **v1.6**（B 线冻结前最终版）：任务分支/worktree 隔离、交流窗、反馈节点（三类型/动态配额）、B3 成本管控纪律（§14）、调度监测
- 运行形态：**协调者循环常驻**（60 秒/轮，done 卡自动 merge→test→verify→push）；**monitor 纯文本模式默认 off**——本地无 LLM 完整可用

## 3. 目录导览

```
rag-kb/
├── AGENTS.md            # AI 工作规则（接力必读第 1 份）
├── PROJECT.md           # 本文档（接力必读第 2 份）
├── ROADMAP.md           # 项目发展总线设计书（人类入口，树形路线+进度）
├── README.md            # kb 产品说明（快速开始/MCP 挂载/端点速查）
├── kb/                  # ① kb 服务源码（8 模块，见设计文档 4.2）
├── tests/               # kb 验收测试（65 项）
├── orchestra/           # ② 协作系统（board.py + 协议 + skill + 24 项测试）
│   └── docs/superpowers/  # orchestra 设计文档与实施计划
├── docs/superpowers/    # kb 设计文档（specs）与节点计划（plans）
├── kb_data/             # kb 运行数据（ChromaDB + runtime.json，gitignore）
├── _archive/            # 旧学习项目归档（禁参考其架构）
└── .mcp.json            # 项目级 MCP 挂载（Claude Code/TraeWork 直连 kb）
```

## 4. 关键命令速查

```powershell
# 启动 kb 服务（常驻，先于一切 MCP 挂载）
python -m kb serve                    # 默认 127.0.0.1:8000

# kb 测试 / orchestra 测试（venv 内）
venv\Scripts\python.exe -m pytest tests/ -q            # 65 项
venv\Scripts\python.exe -m pytest orchestra/tests/ -q  # 24 项

# orchestra 任务板（协调者工具）
venv\Scripts\python.exe orchestra\board.py status        # 一行一卡看板
venv\Scripts\python.exe orchestra\board.py add --assignee worker-1 --title ... --goal ... --input ... --constraints ... --acceptance ...
venv\Scripts\python.exe orchestra\board.py verify TASK-0001 --pass|--reject --note 原因
venv\Scripts\python.exe orchestra\board.py new-worker worker-1   # 生成 worker 引导语
```

## 5. 接手指南（新 AI 按此行动）

1. **读规则**：AGENTS.md（角色分工、红线、节点门禁制）
2. **读状态**：本文档 + 当前正在实施的设计文档（见第 6 节索引）
3. **验证环境**：`python -m kb serve` 能启动、两套测试全绿、git 状态干净
4. **确认角色**：你是开发 AI（写实现）还是文档/测试 AI（写验收/核验）？按 AGENTS.md 第 5 节职责行动
5. **协作开发**（orchestra 流程）：
   - 协调者任务：用户提需求 → 你拆卡（board.py add，五字段齐+assignee）→ 用户开 worker 任务粘贴 new-worker 引导语 → worker 领卡执行 → 你核验（diff+测试+真服务复验）→ verify 流转 → 统一提交
   - 你当 worker（被粘贴引导语时）：加载 orchestra-worker skill → 查卡 → 单卡单轮 → 回写 → 停止
6. **接续总线**：读 `ROADMAP.md` 确认当前位置（当前活跃：A1 稳定性 + B1 P0），按对应计划节点走 TDD + 节点门禁制

## 6. 文档索引（唯一有效集）

| 文档 | 内容 |
|---|---|
| `ROADMAP.md` | **项目发展总线设计书**（人类入口：树形路线+进度+优先级） |
| `docs/USER_GUIDE.md` | **用户使用手册**（人类用户入口：安装/挂载/协作流程/FAQ） |
| `docs/superpowers/specs/2026-08-23-kb-memory-service-design.md` | kb 唯一设计（需求/架构/API/基准） |
| `docs/superpowers/plans/2026-08-23-kb-dev-nodes.md` | kb 节点计划 N1-N16（全部 ✅） |
| `orchestra/docs/superpowers/specs/2026-08-24-agent-orchestra-mvp-design.md` | orchestra 设计 |
| `orchestra/docs/superpowers/plans/2026-08-24-agent-orchestra-mvp.md` | orchestra 实施计划（全部 ✅） |
| `docs/superpowers/specs/2026-08-24-logging-design.md` | 日志设计（总线 A1.2，N17-N18） |
| `docs/superpowers/plans/2026-08-24-p2-roadmap.md` | kb 功能路线（总线 A2-A4） |
| `orchestra/docs/superpowers/plans/2026-08-24-orchestra-v2-iteration.md` | orchestra v2 迭代计划（总线 B1 载体） |
| `orchestra/protocol.md` + `worker-prompt.md` + `coordinator-prompt.md` | 协作协议三件套（v1.1：分支/重启管控/交流窗） |
| `orchestra/EXPERIMENT.md` | 真机实验指引（可复用作下次实验模板） |

## 7. 环境备忘（详见 AGENTS.md 第 7 节）

- torch 2.11.0+cu128（阿里云镜像装的）；HF 走 hf-mirror.com；Ollama 模型在 `D:\ollama_models`
- qwen3 本地必须 `think:false`（Ollama 原生 /api/chat，非 SDK）
- Ollama 必须从开始菜单/托盘启动（沙箱终端拉起会数据库读写失败）
- BGE-M3 与 Ollama 模型均已离线缓存，断网可完整运行（验收标准）
