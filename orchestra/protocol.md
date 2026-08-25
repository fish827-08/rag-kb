# agent-orchestra 协议总纲

版本：v1.1（2026-08-24）｜ 依据：docs/superpowers/specs/2026-08-24-agent-orchestra-mvp-design.md + 总线 ROADMAP.md B1 规划

## 1. 角色

| 角色 | 载体 | 职责 |
|---|---|---|
| 用户 | 人 | 发起需求、开 worker 任务、终验 |
| 协调者 | 一个 TraeWork 任务 | 拆卡、分发、核验、打回、合并分支、重启服务 |
| 设计者 | 一个 TraeWork 任务 | 写设计书与验收测试草案、评审交付；**不写业务实现代码**（协议见 designer-prompt.md，2026-08-24 用户提前激活三角色编制） |
| worker | 其他 TraeWork 任务（任意模型） | 领卡、执行、回写 |

> 三角色编制（协调者→设计者→workers）2026-08-24 生效。设计者拆技术方案定验收，协调者只管调度/合并/重启/汇报。

## 2. 任务卡

kb 记录（tag=taskboard），首行状态行 + 六字段：

TASK-0003 pending worker-1 | 标题
目标：…（≤300 字符）
输入：…（≤300）
约束：…（≤200）
验收：…（≤200）
结果：…（≤1000，worker 回写）

## 3. 状态机

pending → claimed → done → verified（终态）
                  → failed → verified（终态）
done/failed 可被协调者打回 → pending

## 4. 硬纪律（全体 agent）

1. 单卡单轮：一次唤醒只处理一张卡
2. 禁止轮询：回合结束即待命，不主动重复查询
3. 禁止超范围：只做卡片"目标"内的事
4. 字符上限：结果 ≤1000，执行摘要 ≤200
5. 超时：claimed 超 30 分钟无 done → 协调者打回 pending

## 5. 任务分支模式（v1.1 新增）

- 协调者建卡时在"约束"字段注明分支名：`分支 task/TASK-NNNN`
- worker 在该分支上开发与提交（**禁止直接提交 main**）；卡内未指定分支时按旧模式工作、不提交，留给协调者
- worker 回写 done 时在"结果"中注明分支提交哈希
- 协调者核验通过后：`git merge --no-ff task/TASK-NNNN` 合入 main → verify → 推送 → 删分支
- 并行各卡分支零文件交集（拆卡时保证），合并冲突即打回

## 6. 服务重启管控（v1.1 新增，铁律）

**kb 服务（`python -m kb serve`）是全员共享记忆，重启窗口内所有人无法写库。**

1. worker **无权重启**服务：需重启（如改了 kb 代码需生效）时，在卡内"结果"写明 `申请重启：原因`，然后结束回合等待
2. 协调者收到重启申请：确认无其他 worker 处于 claimed 中途（有则等其回写）→ 停止服务 → 重启 → `healthz` 验证 → 在交流窗发 `comm:system 服务已重启（时间/原因）`
3. 重启期间所有 worker 不得写库，收到协调者通知后方可继续
4. worker 发现服务不可用（连接失败）：不自行拉起服务，回写 failed 说明后待命

## 7. 交流窗（comm: 频道，v1.1 新增）

worker 有权把**重要过程信息**写入 kb 供其他 agent 检索（用现有 write_memory，标签以 `comm:` 开头）：

| 频道标签 | 用途 |
|---|---|
| `comm:done` | 任务完成的关键结论（谁/哪张卡/产出物路径） |
| `comm:issue` | 遇到的问题与风险（阻塞点/影响面） |
| `comm:test` | 待测试项 / 测试结果 |
| `comm:system` | 系统级事件（重启/环境变更，协调者专用） |

纪律：交流窗只写**结论级**信息（≤300 字符），不写过程流水账；与任务卡重复的信息只进卡不进窗。

## 8. 查卡方式

- worker：`search_memory("TASK pending {我的名字}")` + `search_memory("TASK claimed {我的名字}")`（中断恢复优先续做 claimed）
- 协调者：`board.py status`（一行一卡，不读整卡）
- 全员状态了解：`search_memory("comm:")` 按频道检索
