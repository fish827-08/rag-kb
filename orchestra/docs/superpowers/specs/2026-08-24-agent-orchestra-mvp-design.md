# agent-orchestra MVP 设计文档

- 日期：2026-08-24
- 状态：已与需求方逐节确认，待实施
- 依赖：kb 服务 v1.0.1+（REST + MCP，`127.0.0.1:8000`，**零改动**，仅作为任务板使用）

## 1. 背景与定位

多 agent 协作工具目前都是单一产品内部的（Claude Code subagents、LangGraph、CrewAI），跨产品的编排是空位。本项目做一个轻量实验系统：**在 TraeWork 内实现跨任务的开发任务委派**——协调者 AI（一个 TraeWork 任务）把开发需求拆解为任务卡，写在共享任务板上；用户在其他 TraeWork 任务中开启任意模型的 AI 作为 worker，按协议领卡、执行、回写；协调者核验汇总。

核心洞察：LLM 无状态，"挂载等待"不消耗 token（不推理即零消耗），真正需要管理的是**每次唤醒的上下文大小**。因此本系统的本质是一套"任务卡协议 + 上下文硬上限"，而非进程管理。

## 2. 目标与非目标

**目标**：跑通一条完整链路——用户提需求 → 协调者拆卡 → worker（另一 TraeWork 任务、任意模型）领卡执行回写 → 协调者核验汇总 → 用户终验。全程任务卡与结果受字符硬上限约束。

**非目标（MVP 明确不做）**：任务依赖图（DAG）、自动重试、并行编排优化、讨论组/评论流、token 记账仪表盘、A2A 协议接入、TraeWork 之外的 CLI 编排（Claude Code/Codex 直连）、kb 代码改动。

## 3. 总体架构

```
用户（发起人）
 │ 总需求 / 开 worker 任务 / 粘贴引导
 ▼
协调者（一个 TraeWork 任务）──board.py──┐ 紧凑状态查询、卡片管理
 │                                      ▼
 │                          kb 服务（共享任务板，已有）
 │                                      ▲
 │ skill 唤醒 + 领卡执行                  │ MCP 读写任务卡
 ▼                                      │
worker-1/2/3（其他 TraeWork 任务，任意模型）
```

- **角色**：用户（发起人）、协调者（coordinator，拆卡/分发/核验）、worker（执行者，任意模型的 TraeWork 任务）
- **黑板**：kb 服务，任务卡即 kb 记录（tag 固定 `taskboard`）
- **资产仓**：rag-kb 仓 `orchestra/` 目录（独立分支 `orchestra` 开发，main 保持 kb 服务稳定线），存放协议文档、skill 源文件、board.py 及其测试

### 等待与唤醒机制

- **等待 = agent 回合结束**：TraeWork 中 agent 完成回复即停止推理，零 token 消耗，无需进程常驻
- **唤醒 = 用户在该任务发消息**：worker skill 定义每次唤醒的标准行为（查卡 → 有卡执行 / 无卡报告待命）
- 协调者同理：用户发指令时我查 `board.py status`（一行一卡，协调上下文最小化）

## 4. 任务卡设计

kb 记录 = 一张卡。首行是**可检索的状态行**（空格分词，保证 BM25/向量双路可命中），正文是结构化字段：

```
TASK-0003 pending worker-1 | 重构storage层异常处理
目标：把 kb/storage.py 的自定义异常统一为 StorageError
输入：kb/storage.py、tests/test_n04_storage.py
约束：不改对外接口签名；不引入新依赖
验收：全部现有测试通过；异常消息含文件与行号上下文
结果：
```

### 字段与字符上限（硬校验）

| 字段 | 上限（字符） | 说明 |
|---|---|---|
| 标题（状态行内） | 30 | 一行摘要 |
| 目标 | 300 | 做什么、为什么 |
| 输入 | 300 | 相关文件路径/上下文引用，不贴大段代码 |
| 约束 | 200 | 硬性要求（不改接口、不加依赖等） |
| 验收 | 200 | 可检查的完成标准 |
| 结果 | 1000 | worker 回写：做了什么/改了哪些文件/如何自查验收 |

换算依据：中文保守按 1 字符 ≈ 1 token，卡片创建态 ≤ 1500 字符（≈500 token 上限量级），含结果 ≤ 2500 字符。

### 状态机

```
pending → claimed → done → verified（终态）
                   → failed → verified（终态，协调者确认失败原因）
done/failed 均可被协调者 rejected → pending（打回重做）
```

- 状态只存于首行标记（MCP `update_memory` 仅改内容，tag 固定 `taskboard` 不变）
- 认领：worker 将首行 `pending` 改为 `claimed` 并署名（谁认领谁执行）
- 争抢防护：协调者建卡时**必须指定 assignee**（不支持 any），worker 只领自己的卡

### 检索约定

- worker 查卡：`search_memory` 查询 `"TASK pending {WORKER_NAME}"`（状态行分词命中）；**中断恢复**：同时查自己的 `claimed` 卡——若存在（上次回合中断遗留），优先续做该卡而非领新卡
- 协调者查状态：`board.py status`（REST 解析首行，不取整卡正文）

## 5. 协议文档资产（orchestra/ 目录内）

| 文件 | 内容要点 |
|---|---|
| `worker-prompt.md` | worker 协议源文档：单卡单轮纪律（领卡→认领→执行→回写→停止）、禁止轮询、禁止超范围动作、结果格式模板 |
| `coordinator-prompt.md` | 协调者规约：拆卡原则（一卡一任务、五字段齐、assignee 明确）、核验标准（对照验收逐条查）、打回规则（格式坏/超长/未达验收 → rejected 回 pending） |
| `protocol.md` | 通信范式总纲：卡片格式、状态机、超时规则、token 上限表 |

## 6. orchestra-worker skill

让任意新 TraeWork 任务一句话成为 worker 的关键件。

- **源文件**：`orchestra/skills/orchestra-worker/SKILL.md`（版本化，随协议演进）
- **安装**：复制到 `~/.trae-cn\skills\orchestra-worker\`（实施时用 skill-creator 生成规范结构）
- **触发**：worker 任务内用户说"你是 worker-1，开始工作"或 /orchestra-worker，agent 加载协议
- **唤醒行为定义**（skill 核心内容）：
  1. 若本回合未声明身份 → 询问 worker 名字
  2. `search_memory` 查自己的 pending 卡（无卡 → 回复一句"无待办任务，待命中"即结束，不猜测不闲聊）
  3. 有卡 → `update_memory` 认领 → 按目标/约束/验收执行 → `update_memory` 回写（done 或 failed + 结果区）
  4. 回复执行摘要（≤200 字）后回合结束，等待下次唤醒
- **纪律**（写入 skill 硬约束）：单卡单轮、禁轮询、禁超范围、结果 ≤1000 字符、不读无关记忆

## 7. board.py 设计（协调者专用 CLI）

~150 行，Python 3.10+，**仅标准库**（urllib），走 kb REST。

```
board.py add --assignee NAME --title T --goal G --input I --constraints C --acceptance A
    创建卡；各字段字符上限校验（超限拒建并报错）；TASK 编号自动递增
    （实现：检索现有全部 taskboard 卡取最大编号 +1，四位数零填充）
board.py status
    每卡一行：TASK-0003 claimed worker-1 12:30 重构storage层异常处理
board.py show TASK-0003
    打印整卡（核验时用）
board.py verify TASK-0003 --pass | --reject [--note 原因]
    终态流转：--pass → verified；--reject → 回 pending（note 写入卡片备注行）
board.py new-worker NAME
    打印该 worker 的引导语（含 skill 触发指令），供用户复制到新任务
```

- kb 不在线：明确报错退出（提示先启动 `python -m kb serve`）
- 退出码：0 成功 / 1 参数或校验失败 / 2 服务不可达

## 8. 错误处理

| 场景 | 处理 |
|---|---|
| worker 失联（claimed 超 30 分钟无 done） | 协调者 `verify --reject` 回 pending，可改 assignee 重分配 |
| worker 写坏格式/结果超长 | 核验打回（rejected），skill 与模板已约束格式 |
| 两 worker 争抢 | assignee 预分配（无 any 卡），设计上排除 |
| kb 服务不在线 | board.py 退出码 2 并提示；worker 查卡失败应报告"任务板不可达"而非空转 |
| worker 模型能力不足做错方向 | 核验打回；卡片"约束"字段收窄范围 |

## 9. 测试与验收

1. **board.py 单测**（pytest，mock kb REST）：卡片格式生成、长度校验拒绝、status 解析、verify 流转、new-worker 输出
2. **端到端 dry-run**：协调者自己扮演 worker，用 MCP 工具按 skill 协议走完整链路（pending→claimed→done→verified），零额外 AI 消耗
3. **真机实验（MVP 终验，人工）**：用户提一个小型开发需求 → 协调者拆 2-3 张卡 → 用户开 1 个 worker 任务（任意模型）粘贴引导 → 全链路走通；全程卡片符合字符上限；协调者全程上下文增长受控（status 一行一卡）

## 10. 环境与约束

- Windows，Python 3.10+，仅标准库（board.py）
- kb 服务需在线（`http://127.0.0.1:8000`）
- 开发在 rag-kb 仓 `orchestra` 分支进行；`orchestra/` 目录最终是否合入 main 由 MVP 终验后决定
- 所有 agent（协调者与 worker）挂载同一 kb MCP
- TraeWork 订阅额度内运行；真实约束是速率限制与上下文质量，token 硬上限靠模板与校验双保险
- 敏感数据规范沿用 rag-kb 项目约定：密钥不落盘、不进提交
