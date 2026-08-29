# A3 智能层 Consolidation 实施设计（LLM 记忆归并）

- 日期：2026-08-27
- 状态：设计书（TASK-0074 产出，N23c 智能层详细设计），待人工评审
- 前置：A3 记忆治理 spec（`2026-08-27-a3-memory-governance-design.md`）§2.1 智能层（现有简要描述）、§5 N23（consolidation 可选）
- 依据：`kb/llm.py` LLMClient（本地 Ollama 原生 /api/chat，think=False，temperature 0.2，num_ctx 4096，max_tokens 800）；`kb/config.py` Settings（pydantic-settings，现有治理配置默认关模式）；评估报告记忆管理对比节（Mem0 等竞品无原生遗忘/归并）
- 定位：A3 智能层是规则层（衰减/去重/新鲜度，无 LLM）之上的可选增强，**不进首批**，N23 交付；无 LLM 时规则层完整可用，智能层仅在 LLM 可用且显式开启时生效

## 1. 需求

### 1.1 问题
规则层的语义去重（409 拦截，阈值 0.92）只处理高度重复。相似度 0.75-0.92 的"相关但不重复"记录存在三类问题：
- **矛盾信息并存**：同一属性不同值（如"用户偏好：X"与"用户偏好：Y"）
- **互补信息分散**：同一主题不同侧面（如"项目用 Python"与"项目用 FastAPI"）可合并为一条更完整的记忆
- **冗余近似重复**：低于 0.92 但实际表达同一事实（改写/缩写）

### 1.2 目标
- LLM 驱动的记忆归并（consolidation）：对候选记忆对做 merge / independent / human 三分支决策
- 冲突检测：同一属性不同值的预筛（启发式 + LLM 确认）
- 安全护栏：dry-run 默认、原记录保留可回滚、默认关闭
- 零新增依赖：复用现有 LLMClient（本地 qwen3:4b）
- 纯批量/维护操作：不在写入路径触发（与 409 去重不同），由 CLI/API 手动触发

### 1.3 非目标（YAGNI）
- 不做自动定时归并（必须人工/agent 显式触发）
- 不做跨 namespace 归并
- 不删除原记录（只标记 superseded，回滚可恢复）
- 不做实时写入路径归并（写入路径只有 409 去重）
- 不用云端 LLM（仅本地 qwen3:4b，避免敏感记忆外传）

## 2. 架构

### 2.1 流程

```
触发（CLI / API）
  → ① 候选扫描：相似度 0.75-0.92 的记录对（向量检索，排除已 superseded）
  → ② 冲突预筛：启发式提取 key-value，同 key 不同值 → 标记 conflict_candidate
  → ③ LLM 决策：对每对记录调 qwen3:4b，输出 merge/independent/human + merged_content
  → ④ dry-run 预览：返回决策列表，不写库
  → ⑤ apply（显式确认）：对 merge 决策创建新归并记录，原记录标记 superseded_by
  → ⑥ 回滚：按 batch_id 撤销（删除归并记录，取消原记录 superseded 标记）
```

### 2.2 数据模型扩展

Record metadata 新增字段（可选，向后兼容）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `superseded_by` | str | "" | 被哪条归并记录替代；空=有效记录 |
| `consolidation_batch` | str | "" | 归并批次 ID（apply 时写入，回滚依据） |
| `conflict_type` | str | "" | 冲突类型标签（attribute_conflict / temporal / none），预筛或 LLM 标注 |

- superseded 记录在检索时默认过滤（retriever 加 where `superseded_by == ""`），可通过参数 `include_superseded=true` 查看
- 归并记录的 `created_at` = apply 时间，`source` = "consolidation"，`tags` 合并原记录 tags 去重

### 2.3 与规则层的关系

| 维度 | 规则层（409 去重） | 智能层（consolidation） |
|---|---|---|
| 触发时机 | 写入路径实时 | 批量维护手动触发 |
| 相似度范围 | >0.92（高度重复） | 0.75-0.92（相关不重复） |
| 决策方式 | 余弦阈值（确定性） | LLM 判断（merge/independent/human） |
| 写库行为 | 不写库（409 拦截） | 创建归并记录 + 标记原记录 superseded |
| LLM 依赖 | 无 | 本地 qwen3:4b |
| 默认状态 | 关 | 关 |

## 3. 冲突检测

### 3.1 启发式预筛（无 LLM）

对候选记录对做 key-value 提取：

1. **属性模式匹配**：正则提取 `关键词[:：]\s*值` 模式（如"用户偏好：X"、"语言：Python"、"框架：FastAPI"）
2. **同 key 检测**：两条记录提取到相同 key 但值不同 → `conflict_type=attribute_conflict`
3. **时间矛盾检测**：同一主题（关键词重叠）+ updated_at 差距 >30 天 + 陈述语气（含"是/为/用"等断定词）→ `conflict_type=temporal`
4. 无匹配 → `conflict_type=none`（仍送 LLM 判断是否互补可合并）

- 预筛只做标记，不做决策；最终决策由 LLM 做出
- 预筛结果作为 LLM 输入的上下文（提示 LLM 关注冲突点）

### 3.2 LLM 确认

LLM 在决策时验证预筛标记：
- 若预筛标记 attribute_conflict 但 LLM 判断两值实际兼容（如"语言：Python"与"语言：Python 3.10"）→ 可 merge，conflict_type 修正为 none
- 若预筛标记 none 但 LLM 发现隐含矛盾 → 标记 conflict_type 并建议 human

## 4. LLM 提示词设计

### 4.1 系统提示词

```
你是记忆归并代理（Memory Consolidation Agent）。
任务：给定 2-5 条记忆记录，判断它们是否应合并为一条更完整的记忆，或保持独立，或需要人工确认。

决策规则：
- merge：记录表达同一事实/同一主题互补信息/同一属性新值明确替代旧值 → 合并为一条
- independent：记录主题不同/角度不同且各自有独立检索价值 → 保持独立
- human：信息矛盾且无法判断哪个正确/合并可能丢失重要信息/置信度低 → 人工确认

合并要求：
- merged_content 保留所有有价值信息，去重，语言简洁
- 若同一属性有新旧值，保留较新值（updated_at 较晚者），除非旧值明显更准确
- 不添加记录中不存在的信息
- 不删除可能有独立检索价值的细节

输出严格 JSON，不输出任何其他文字：
{"decision":"merge|independent|human","reason":"简短原因","merged_content":"合并后内容或null","conflict_type":"attribute_conflict|temporal|none","confidence":0.0-1.0}
```

### 4.2 用户提示词模板

```
记忆记录列表：
{records}
其中每条格式：[id={id}] created_at={created_at} updated_at={updated_at} tags={tags} source={source}
内容：{content}

预筛冲突标记：{conflict_type}（若为 none 表示未检测到明显冲突）

请按系统提示规则决策，输出严格 JSON。
```

### 4.3 输出格式约束与校验

- LLM 输出必须是可解析 JSON，字段齐全
- 校验失败（JSON 解析错误 / decision 不在枚举 / merge 时 merged_content 为空）→ 降级为 `human` 决策，记 WARNING 日志
- `confidence < 0.6` → 强制升级为 `human`（即使 LLM 输出 merge/independent）
- max_tokens=800 足够（单对记录决策输出 <200 token）

## 5. 合并策略（决策树）

```
候选对（相似度 0.75-0.92）
  │
  ├─ 预筛 conflict_type=attribute_conflict
  │    ├─ 新值 updated_at 明显更晚（>旧值 7 天）且主题一致 → LLM 倾向 merge（新值替代）
  │    ├─ 两值时间接近或无法判断 → LLM 倾向 human
  │    └─ LLM 判断两值实际兼容（如版本号细化）→ merge（合并表述）
  │
  ├─ 预筛 conflict_type=temporal
  │    ├─ LLM 判断旧陈述已过时 → merge（保留新陈述）
  │    └─ LLM 无法判断 → human
  │
  ├─ 预筛 conflict_type=none
  │    ├─ LLM 判断互补信息（同主题不同侧面）→ merge（合并）
  │    ├─ LLM 判断近似重复（改写/缩写）→ merge（取更完整表述）
  │    └─ LLM 判断主题不同/各自独立有价值 → independent
  │
  └─ LLM confidence < 0.6 或输出校验失败 → human（强制）
```

### 5.1 人工确认机制

- `human` 决策的记录对不自动 apply，在预览结果中标记 `pending_human=true`
- 调用方（agent/人工）可：
  - 强制 merge：`--force-merge <pair_id>`（覆盖 human 决策）
  - 强制 independent：`--force-independent <pair_id>`
  - 跳过：不处理该对
- apply 时只处理 decision=merge 且非 pending_human 的对；human 对需显式 `--include-human` + 强制决策

## 6. 本地 qwen3:4b 调用方式

- 复用 `kb/llm.py` 的 `LLMClient.chat(messages)` 接口，不新建客户端
- 模式限制：仅 `llm_mode=local` 或 `auto`（auto 时优先本地）；**禁止 cloud 模式**（敏感记忆不外传）；若本地不可用 → consolidation 不可用，返回明确错误
- 护栏参数沿用 LLMClient 默认：think=False、temperature 0.2、num_ctx 4096、max_tokens 800
- 批量处理：每次 LLM 调用最多 5 条记录（上下文控制）；超过则分批
- 调用失败（超时/LLM 不可用/输出校验失败）→ 该对降级为 human，不阻塞其他对
- 限流：连续 LLM 调用间隔 ≥1s（避免 Ollama 过载），可配置

## 7. API 设计（REST）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/v1/governance/consolidate/scan` | POST | 扫描候选对（相似度 0.75-0.92，排除 superseded）；参数 `limit`（默认 20）、`namespace`；返回 `[{pair_id, record_a, record_b, similarity, conflict_type}]` |
| `/api/v1/governance/consolidate/preview` | POST | 对指定 pair_id 列表调 LLM 决策（dry-run，不写库）；参数 `pair_ids`；返回 `[{pair_id, decision, reason, merged_content, conflict_type, confidence, pending_human}]` |
| `/api/v1/governance/consolidate/apply` | POST | 应用 preview 结果中的 merge 决策；参数 `preview_results`、`force_merge_pairs`（可选）；返回 `batch_id`、`merged_count`、`skipped_count` |
| `/api/v1/governance/consolidate/rollback` | POST | 按 batch_id 回滚；参数 `batch_id`；返回 `restored_count`、`deleted_consolidated_count` |
| `/api/v1/governance/consolidate/batches` | GET | 列出归并批次历史（batch_id、created_at、merged_count、状态） |

- 所有端点需 `KB_CONSOLIDATION_ENABLED=true`，否则返回 503 + 明确提示
- scan/preview 只读无副作用；apply/rollback 写库，记日志

## 8. CLI 设计

```
kb consolidate --scan [--limit N] [--namespace NS]      # 扫描候选对
kb consolidate --preview --pairs id1,id2 [--pairs ...]   # LLM 决策预览（dry-run）
kb consolidate --apply --preview-file <json> [--force-merge id1,id2]  # 应用归并
kb consolidate --rollback --batch <batch_id>               # 回滚批次
kb consolidate --batches                                    # 列出批次历史
```

- `--preview` 输出 JSON 到 stdout，可重定向到文件供 `--apply --preview-file` 使用
- `--apply` 默认只处理 merge 且非 pending_human 的对；`--include-human` 需配合 `--force-merge` 或 `--force-independent`
- 所有写操作（apply/rollback）前打印摘要并要求 `--yes` 确认（非交互场景）

## 9. 安全护栏

| 护栏 | 说明 |
|---|---|
| 默认关闭 | `KB_CONSOLIDATION_ENABLED=false`，开启需显式配置 |
| dry-run 默认 | preview 不写库；apply 需显式调用 + `--yes` |
| 原记录保留 | merge 不删除原记录，只标记 `superseded_by`；检索默认过滤 superseded |
| 可回滚 | 每次 apply 生成 batch_id，rollback 按批次完全撤销（删除归并记录 + 取消 superseded 标记） |
| 本地 LLM  only | 禁止 cloud 模式，敏感记忆不外传 |
| 置信度门槛 | confidence<0.6 强制 human，不自动合并 |
| 输出校验 | LLM 输出 JSON 校验失败 → human，不盲目 apply |
| 人工确认 | 矛盾/低置信度对标记 pending_human，需显式强制决策才 apply |
| 批量上限 | 单次 apply 最多 50 对（防止大规模误操作），超过需分批 |
| 审计日志 | 每次 apply/rollback 记日志（batch_id、操作人、merge 列表、时间） |

## 10. 配置项

| 配置项 | 默认 | 说明 |
|---|---|---|
| `KB_CONSOLIDATION_ENABLED` | false | 智能层归并总开关 |
| `KB_CONSOLIDATION_SIM_MIN` | 0.75 | 候选扫描相似度下限 |
| `KB_CONSOLIDATION_SIM_MAX` | 0.92 | 候选扫描相似度上限（>0.92 由 409 去重处理） |
| `KB_CONSOLIDATION_CONFIDENCE_THRESHOLD` | 0.6 | LLM 置信度门槛，低于此强制 human |
| `KB_CONSOLIDATION_MAX_RECORDS_PER_CALL` | 5 | 单次 LLM 调用最多记录数 |
| `KB_CONSOLIDATION_MAX_PAIRS_PER_APPLY` | 50 | 单次 apply 最多对数 |
| `KB_CONSOLIDATION_LLM_INTERVAL_MS` | 1000 | 连续 LLM 调用间隔（毫秒） |

- 全部走 `kb/config.py`（pydantic-settings）+ `.env`，禁止硬编码
- `.env.example` 补键名与默认值

## 11. 里程碑拆分（N23a-N23c，可独立交付）

| 子节点 | 内容 | 门禁 |
|---|---|---|
| **N23a** | 冲突检测启发式（key-value 提取 + 同 key 不同值 + 时间矛盾）+ scan 端点 + Record metadata 扩展（superseded_by/consolidation_batch/conflict_type）+ 检索过滤 superseded + 单元测试 | 标准 |
| **N23b** | LLM 提示词（系统+用户+输出校验）+ preview 端点 + 决策树逻辑（merge/independent/human + 置信度门槛）+ mock LLMClient 单元测试 | 标准 |
| **N23c** | apply（创建归并记录 + 标记 superseded + batch_id）+ rollback + batches 列表 + CLI 五个子命令 + 审计日志 + 集成测试（scan→preview→apply→rollback 全链路，mock LLM） | 标准 |

- N23a 交付后可扫描候选对（无 LLM）；N23b 交付后可预览决策（dry-run）；N23c 交付后可 apply/rollback 完整闭环
- 每节点 TDD：先写验收测试→红→实现→绿→全量回归

## 12. 测试策略

### 12.1 单元测试（N23a）
```python
def test_conflict_detect_attribute_conflict():
    # "用户偏好：X" vs "用户偏好：Y" → attribute_conflict

def test_conflict_detect_compatible_values():
    # "语言：Python" vs "语言：Python 3.10" → none（兼容，非冲突）

def test_conflict_detect_temporal():
    # 同主题 + updated_at 差>30天 + 断定词 → temporal

def test_superseded_filtered_in_retrieval():
    # superseded_by 非空的记录默认不出现在检索结果中

def test_metadata_backward_compatible():
    # 旧记录无 superseded_by 字段 → from_chroma 默认空串，不报错
```

### 12.2 单元测试（N23b，mock LLMClient）
```python
def test_llm_decision_merge():
    # mock LLM 返回 merge + merged_content → 决策正确解析

def test_llm_decision_independent():
    # mock 返回 independent → merged_content=null

def test_llm_low_confidence_forces_human():
    # mock 返回 merge 但 confidence=0.4 → 强制 human

def test_llm_invalid_json_falls_back_to_human():
    # mock 返回非 JSON → 校验失败 → human + WARNING 日志

def test_prompt_contains_records_and_conflict_tag():
    # 验证发给 LLM 的 messages 包含记录内容和预筛 conflict_type
```

### 12.3 集成测试（N23c，mock LLM）
```python
def test_full_flow_scan_preview_apply_rollback():
    # 写入两条相关记录 → scan 找到候选 → preview 返回 merge → apply 创建归并+标记superseded
    # → 检索不含原记录含归并记录 → rollback 恢复原记录+删除归并记录

def test_apply_skips_pending_human():
    # preview 含 human 决策对 → apply 默认跳过，返回 skipped_count

def test_rollback_restores_originals():
    # apply 后 rollback → 原记录 superseded_by 清空，归并记录删除

def test_cloud_mode_rejected():
    # llm_mode=cloud → consolidation 返回错误，不调用云端
```

### 12.4 回归与性能
- 全量既有测试保持绿（consolidation 默认关闭时零行为变化）
- CI 中不调真实 LLM（全部 mock LLMClient）
- 性能：scan 是一次向量检索（top_k 可调）；preview 每对一次 LLM 调用（~1-2s/qwen3:4b），50 对约 1-2 分钟；apply/rollback 是批量元数据更新，<1s
- 显存：复用已加载的 qwen3:4b，无新增模型

## 13. 风险与避坑

| 风险 | 规避 |
|---|---|
| LLM 误合并丢失信息 | confidence 门槛 + human 分支 + 原记录保留 superseded + 可回滚 |
| LLM 输出格式不稳定 | 严格 JSON 校验 + 失败降级 human + 系统提示强约束输出格式 |
| 批量误操作 | 单次 apply 上限 50 + dry-run 默认 + --yes 确认 + 可回滚 |
| 敏感记忆外传 | 仅本地 LLM，cloud 模式拒绝 |
| superseded 记录污染检索 | retriever 默认过滤 superseded，include_superseded 参数可选查看 |
| 旧记录无新字段 | from_chroma .get(key, "") 兼容，缺失=空串 |
| Ollama 过载 | 连续调用间隔 ≥1s + 单次最多 5 条记录 + 失败降级不阻塞 |
