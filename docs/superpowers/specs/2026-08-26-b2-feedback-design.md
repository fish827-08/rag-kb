# B2 双向反馈闭环实施设计（P1 预审 + 结构化反馈）

- 日期：2026-08-26
- 状态：设计书（TASK-0026 产出），待拆实现卡
- 依据：ROADMAP.md B2 节（三反馈节点/结构化三类型/分节点轮次配额/反馈记录可追溯）；protocol.md v1.2（轮次硬限制 §4、批量模式 §9）；交流窗命令 `board.py report / list-comm`（TASK-0009）；三次串扰事故的反馈类教训
- 目标：反馈"能推动方案优化、不超配额、可追溯"，杜绝"反馈泛滥变无限扯皮"（ROADMAP §6 B2 风险）

## 1. 数据模型（反馈卡字段）

反馈卡 = kb 记录（tag=`feedback`，关联目标卡 `TASK-NNNN`），首行状态行 + 必附字段：

```
FBK-0001 feedback TASK-0026 | objection precheck
提出者：designer-1            ← 谁提的（coordinator/designer/worker）
目标卡：TASK-0026
类型：objection|risk|clarify
节点：precheck|milestone|review
摘要：≤100 字符
替代方案：…                   ← objection 必填（无替代方案直接驳回）
阻塞点/影响面：…              ← risk 必填
澄清问题：…                   ← clarify 必填
结果：open → accepted / rejected
```

- 状态机：`open → accepted / rejected`（accepted=采纳进入方案修订，rejected=打回）
- 每张反馈卡独立可检索（tag=feedback），关联卡可回溯

## 2. 流程图（文字版）

```
执行前预审（precheck）：协调者拆卡 → 设计者预审（设计书/验收是否完备）
   → 有 objection/risk → 写反馈卡 open → 拆卡方修订 → 最多 2 轮 → 超限仲裁（协调者裁决）
执行中里程碑（milestone）：worker 执行到里程碑点（回写前）→ 可发 feedback
   → 阻塞型 risk 停止当前节点等裁决；异议型附带替代方案 → 最多 2 轮
完成后复盘（review）：worker done → 设计者复盘验收 → feedback（沉淀性，不占配额）
反馈归档：accepted 反馈的结论级信息写交流窗 comm:feedback（谁/对哪张卡/类型/结论）
```

- 三节点覆盖全链路：开工前（预审）→ 执行中（里程碑）→ 交付后（复盘）
- 反馈卡 open 期间目标卡不进入 verified（阻塞验收）

## 3. 轮次配额表（分节点配额）

| 节点 | 配额 | 超限处理 |
|---|---|---|
| 执行前预审（precheck） | **2 轮** | 协调者仲裁（ROADMAP B2"超限仲裁"） |
| 执行中里程碑（milestone） | **2 轮** | 协调者仲裁 |
| 完成后复盘（review） | 不计配额（沉淀性） | 单条反馈 ≤300 字符入归档 |
| **单任务总上限** | **5 轮** | 强制截断转仲裁 |

- 说明：precheck(2) + milestone(2) = 4，第 5 轮为仲裁前最终修订轮；总上限 5 轮封顶
- 仲裁 = 协调者对照"无替代方案的异议直接驳回"铁律裁决，裁决结论写 feedback 卡结果字段
- 与 protocol v1.2 §4.2 轮次硬限制衔接：反馈轮次计入任务总轮次

## 4. 三类结构化反馈规范（反馈类型表）

| 类型 | 标签 | 必附字段 | 判定规则 |
|---|---|---|---|
| 方案异议 | `objection` | 摘要 + **替代方案**（必填） | 无替代方案 → 直接驳回（铁律） |
| 风险阻塞 | `risk` | 摘要 + 阻塞点 + 影响面 | 阻塞当前节点，等裁决后继续 |
| 需求澄清 | `clarify` | 摘要 + 澄清问题 | 拆卡方/协调者回答后继续，不算异议 |

- 判定规则杜绝"反馈泛滥"：异议必须自带替代方案，否则不算有效反馈

## 5. 反馈归档入交流窗

- 反馈卡 accepted/rejected 后，结论级写 `comm:feedback`（谁/对哪张卡/类型/结论，≤300 字符）
- 查看方式：`board.py list-comm feedback`（复用 TASK-0009 交流窗命令）；统计/检索用 `search_memory("comm:feedback")`
- 归档纪律：只写结论不写过程流水（与交流窗纪律一致）

## 6. 验收标准（含 TDD 红灯基准草案）

**阶段验收**（对照 ROADMAP §5 B2）：
1. worker 反馈能推动方案优化：accepted 反馈与方案变更一一可追溯（feedback 卡→修订提交）
2. 全程不超配额：任何任务反馈轮次 ≤5，超限有仲裁记录
3. 反馈记录可追溯：反馈卡（tag=feedback）+ comm:feedback 双份留痕

**验收测试草案**（落 `orchestra/tests/`，协议文档断言 + 冒烟）：
```python
def test_b2设计书含反馈类型表():
    # docs/superpowers/specs/2026-08-26-b2-feedback-design.md 含 objection/risk/clarify 三类型与必附字段

def test_b2设计书含配额表():
    # 含 2/2/5 配额数字与仲裁表述

def test_反馈卡写入与检索():
    # 冒烟：写一张 feedback 卡（tag=feedback）→ search_memory 可检索；冒烟后清理
```

## 7. 串扰事故教训纳入（反馈类）

三次分支串扰/评审事故沉淀为反馈纪律：
1. 提交前 `git branch --show-current` 自检（已入 v1.2 §9，反馈场景同样适用）
2. 异议必须附替代方案（本次设计的 object 必填字段，杜绝"只否定不建设"）
3. 结论级写入、≤300 字符、不贴过程流水（与交流窗纪律一致）
4. 反馈记录与卡内结果重复信息只进卡不进窗

## 8. 不做（YAGNI）

- 不做自动化反馈路由（反馈由人工/角色按节点发起，不引入状态机自动触发）
- 不做 B3 的动态配额/滚动窗口（B2 固定 2/2/5，动态化留 B3）
- 不改 protocol.md 现有版本（B2 独立设计书；协议改动待实现卡评审后按需入 v1.3）
