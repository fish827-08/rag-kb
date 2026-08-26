# orchestra 包化分层设计（v3.0 结构重构）

> 日期：2026-08-26 | 状态：已确认 | 决策：用户拍板方案一（包化分层 + 文档同步清单化）

## 1. 背景与动机

board.py 已 617 行单文件，承载 HTTP 客户端 / 卡片纯函数 / registry / 交流窗 / worktree / watch 看板 / CLI 调度七块职责。用户三项动机（多选确认）：

1. **协作冲突**：多 worker 并发改同一文件已引发多次撞车（0019/0021 api.py 冲突、0024 共享区竞态被迫用隔离索引法补救）
2. **后续扩展**：dispatcher 发卡员、难度字段、进度统计即将开工（v3 设想已存 kb），继续塞单文件会失控
3. **可读性/维护**：617 行读着费劲

配套痛点：每次 git 提交需人工记忆"要更新哪些文档"，常遗漏。用户选定**核验清单化**方案（建卡时声明文档同步义务，核验时硬门禁），并要求"适当精简"文档。

## 2. 目标结构

```
orchestra/
├── __init__.py          # 空（包标记）
├── board.py             # 仅 CLI 入口：argparse 子命令调度（~80 行）
├── client.py            # kb REST HTTP 客户端：get/post/delete 封装（~60 行）
├── cards.py             # 卡片纯函数：格式化/解析/校验/状态行渲染（~120 行）
├── registry.py          # 成员注册与查询（~60 行）
├── comm.py              # 交流窗读写（report/list-comm）（~60 行）
├── worktree.py          # git worktree 隔离 setup/enter/clean（~80 行）
├── watch.py             # 终端看板 watch 命令（~50 行）
├── dashboard/           # HTML 看板（不动）
├── tests/               # 测试同步拆分：
│   ├── test_client.py       # 原 test_board.py 中 HTTP 客户端用例
│   ├── test_cards.py        # 卡片纯函数用例
│   ├── test_registry.py     # registry 用例
│   ├── test_comm.py         # 交流窗用例
│   ├── test_worktree.py     # 已有，迁 import 路径
│   └── test_watch.py        # watch 用例
└── docs/ protocol.md 等    # 协议文档仅改一处：模块 import 说明
```

**不变式**：
- 对外命令接口零变化：`python orchestra\board.py <子命令>` 照旧（board.py 内 import 各模块）
- 协议三件套（protocol/worker-prompt/coordinator-prompt）不改行为约定，仅补一节"代码结构"
- 175 项测试（拆分后同数量）全绿为迁移完成标准

## 3. 迁移策略：纯机械搬移

- **只剪切粘贴，不改逻辑**：函数原样移动到目标模块，board.py 留 import 与 CLI 调度
- 依赖方向单向：`board.py → 各模块 → client.py`（底层只有 client 依赖 requests/urllib）
- 每搬一个模块跑对应测试，红了立即停（区别于重构：无行为变化）
- 搬移顺序（依赖从底向上）：client → cards → registry → comm → worktree → watch → board 收口

## 4. 文档同步清单化（配套机制）

任务卡新增 `docs` 字段：

```
TASK-XXXX pending worker-N 标题
文档同步: USER_GUIDE.md(端点速查节) | PROJECT.md(进度行)
```

执行链：
1. **建卡时**：协调者按改动面声明需同步的文档及小节
2. **提交前**：worker 按清单逐项更新（卡内容有明确指引）
3. **核验时**：清单项为硬门禁——文档未同步 = 核验不通过打回

文档精简（"适当精简"落点）：
- PROJECT.md 删除与看板重复的逐卡进度行，只保留里程碑级状态（"当前批次/下一里程碑"）
- 其余文档不合并（各有人群：AGENTS 给 AI、USER_GUIDE 给人、ROADMAP 给路线）

## 5. 派工方案（分层首战即验证分层价值）

| 卡 | 模块 | worker | 文件面 |
|---|---|---|---|
| 1 | client.py + test_client.py | worker-2 | 2 个新文件 |
| 2 | cards.py + test_cards.py | worker-3 | 2 个新文件 |
| 3 | registry+comm + 测试 | worker-4 | 4 个新文件 |
| 4 | worktree+watch 迁移 + board.py 收口 | 协调者 | 收口性改动 |

**串行约束**：卡 1、2 可并行（零交集）；卡 3 依赖卡 1 完成（registry/comm 调 client）；卡 4 最后收口（board.py 瘦身 + 全量回归）。worktree 隔离命令在本次派工中启用（每个 worker 用 `.worktrees/TASK-NNNN` 独立目录，彻底告别共享区竞态）。

## 6. 验收标准

1. 拆分后 `python orchestra\board.py status/add/claim/show/verify/new-worker/register/workers/report/list-comm/watch/worktree` 全部子命令行为与拆分前一致
2. 测试数量不减少（175 项全绿，允许因 import 路径调整的等价迁移）
3. board.py ≤100 行；各模块单一职责
4. 文档同步清单机制落地：add 命令支持 `--docs` 参数，verify 核验时检查
5. PROJECT.md 已精简（无逐卡进度行）
