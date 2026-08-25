# orchestra v2 迭代计划 — 总线 B1（P0 三角色闭环）载体

> **总线定位**：本计划是 [ROADMAP.md](../../../ROADMAP.md) 主线 B1 的实施载体（B1.1-B1.7 + V2-0 修复卡）。
> 制定：2026-08-24 ｜ 依据：用户 P0-P3 生产级演进规划 + MVP 真机实验结论
> 说明：P1 反馈闭环（B2）、P2 成本管控（B3）、P3 自适应（B4）待 B1 收口后另行立项。

## 0. 范围与顺序

| 节点 | 总线编号 | 内容 | 优先级 |
|---|---|---|---|
| V2-0 | A1.1 | watcher 缺失目录容错（kb 侧已知 bug） | 最高（已知缺陷） |
| V2-1 | B1.2 | worker registry（register/workers 命令） | 高（状态汇报基础） |
| V2-2 | B1.3 | 交流窗命令化（report/list-comm） | 高 |
| V2-3 | B1.4 | worker 批量模式（轮次保险丝） | 中 |
| V2-4 | B1.6 | 任务分支模式工具支持（建卡自动建分支） | 中（协议 v1.1 已定，工具化） |
| V2-5 | B1.1 | 设计者角色（协议+skill） | 中 |
| V2-6 | B1.7 | coordinator skill | 中 |
| V2-7 | — | 集成验证 + 文档同步 | 收口 |

关联窗口（≤5 张归档摘）属 B3 成本管控，**移出本计划**，B3 立项时实施。

## 1. V2-0：watcher 缺失目录容错（A1.1）

**问题**：`data/` 目录被归档删除后，`KB_WATCH_DIR` 指向不存在路径 → serve 启动直接异常退出（worker-1 在 TASK-0003 实际踩中）。

**设计**：`kb/watcher.py` 启动时检测目录存在性——缺失时**记警告日志并跳过监听**（服务其余功能正常），不 crash；运行中目录被删除同理静默处理。

**验收测试**：
```python
def test_监听目录缺失时服务正常启动():
    # KB_WATCH_DIR 指向不存在路径，serve 应启动成功且 healthz 200
def test_监听目录缺失时记警告日志():
    # 日志包含 warning 级别的目录缺失记录
```

## 2. V2-1：worker registry（B1.2）

**数据模型**：kb 记录，tag=`registry`，内容 JSON：
```json
{"worker": "worker-1", "model": "GLM-5.3", "client": "TraeWork",
 "registered_at": "2026-08-24T23:00", "last_seen": "2026-08-25T10:00", "status": "idle|busy"}
```

**命令**：
- `board.py register NAME --model X --client Y`：注册/刷新身份（last_seen 更新）
- `board.py workers`：一行一 worker（名字/模型/状态/最后活跃）

**协议变更**：worker 首次唤醒先 register（身份自报，模型名由其运行环境自知）；领卡时 status 置 busy，回写后置 idle。

**验收测试**：register 写入且 workers 可列出；重复 register 刷新 last_seen 不重复建卡；非法参数报错。

## 3. V2-2：交流窗命令化（B1.3）

协议 v1.1 已定 comm: 频道约定（用 write_memory 即可用）。本节点做 CLI 便捷化：

**命令**：
- `board.py report --channel done|issue|test|system --from NAME --text "..."`（≤300 字符校验）
- `board.py list-comm [--channel X]`：按频道列最新 N 条

**验收测试**：report 写入带正确 tag；list-comm 按频道过滤；超长 text 拒绝。

## 4. V2-3：worker 批量模式（B1.4）

**协议变更**（worker-prompt.md）：worker 唤醒后进入**批量循环**——领卡→执行→回写→写 comm:done→查下一张，直到无卡或触发保险丝。保险丝：**单轮累计最多 5 张卡或 30 分钟**（先到为准），到达即收尾待命。中断恢复（claimed 续做）逻辑不变。

**验收测试**：协议文档断言（保险丝数字 5/30 存在）；skill 文件同步更新。

## 5. V2-4：任务分支工具化（B1.6）

协议 v1.1 已定分支纪律。本节点 `board.py add` 增加 `--branch` 开关：建卡同时 `git branch task/TASK-NNNN`（卡号自动取），约束字段自动注入分支名；`verify --pass` 时提示协调者合并（合并动作仍手工执行，保持核验人在环）。

**验收测试**：`--branch` 建卡后 `git branch --list` 存在对应分支；卡内约束含分支名；不带 `--branch` 行为不变（兼容旧模式）。

## 6. V2-5：设计者角色（B1.1）

**协议变更**（protocol.md 角色表）：新增设计者——负责技术方案拆解与验收标准制定；协调者去技术化（只拆卡分发/进度/仲裁/合并/重启，不写验收内容）。设计者由用户另开一个 TraeWork 任务担任（同 worker 唤醒机制，身份=designer）。

**落地**：designer-prompt.md 新增（拆卡规范：从协调者收到需求→产出五字段卡→交协调者分发）；拆卡五字段中"目标/验收"由设计者产出，协调者只填 assignee/分支等调度字段。

**验收测试**：协议文档断言设计者职责存在；真机实验一轮（人→协调者→设计者拆卡→worker→设计者验收→协调者交付）。

## 7. V2-6：coordinator skill（B1.7）

新建 skill `orchestra-coordinator`（装 `~\.trae-cn\skills\`）：封装 coordinator-prompt.md 内容。任何 agent 装载该 skill 即可担任协调者，与"我"这个具体会话解耦——换模型、换客户端、开新任务都能接力协调者职责。

**验收测试**：skill 文件存在且内容与 coordinator-prompt.md 一致；SKILL.md 描述触发词（"你是协调者/orchestra-coordinator"）。

## 8. V2-7：集成验证与文档同步（收口）

- 真机实验：双 worker + 设计者三角色全链路（分支模式+交流窗+registry 联动）
- 文档同步：USER_GUIDE 4.4 节、PROJECT.md、ROADMAP.md 进度标记
- 全量回归：两套测试全绿

## 9. 门禁安排

全部标准节点门禁（测试红→绿→回归→提交→tag `v2-N`）；V2-7 收口为人工门禁点（出"自动化已覆盖/待人工验证"报告）。
