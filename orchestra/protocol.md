# agent-orchestra 协议总纲

版本：v1.0（2026-08-24）｜ 依据：docs/superpowers/specs/2026-08-24-agent-orchestra-mvp-design.md

## 1. 角色

| 角色 | 载体 | 职责 |
|---|---|---|
| 用户 | 人 | 发起需求、开 worker 任务、终验 |
| 协调者 | 一个 TraeWork 任务 | 拆卡、分发、核验、打回 |
| worker | 其他 TraeWork 任务（任意模型） | 领卡、执行、回写 |

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

## 5. 查卡方式

- worker：`search_memory("TASK pending {我的名字}")` + `search_memory("TASK claimed {我的名字}")`（中断恢复优先续做 claimed）
- 协调者：`board.py status`（一行一卡，不读整卡）
