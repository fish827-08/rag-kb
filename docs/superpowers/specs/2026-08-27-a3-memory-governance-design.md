# A3 记忆治理实施设计（遗忘 / 衰减 / 去重）

- 日期：2026-08-27
- 状态：设计书（TASK-0066 产出，N21 spec 立项），待人工评审
- 依据：ROADMAP.md A3 节（2026-08-27 战略调整：A3 为唯一差异化机会，下一主力）；`docs/superpowers/plans/2026-08-24-p2-roadmap.md` §3（P2-3 遗忘机制 N21-N23）；`kb/InputStream.py` Record 模型（现有 created_at/updated_at，无 access_count/last_accessed）；`kb/storage.py` ChromaStore（余弦空间、metadata 不支持 None）；`kb/retriever.py` RRF 融合检索
- 差异化定位：评估报告认定官方 server-memory 与 Mem0 均无原生遗忘机制，本项目 A3 是唯一差异化机会

## 1. 需求

### 1.1 问题
当前 kb 只会增不会忘：
- 记忆过时不知（长期未命中的记忆仍占检索位）
- 重复入库不去重（同一信息多次写入产生冗余）
- 矛盾信息并存（"用户偏好 X"与"用户偏好 Y"同时存在）
- 检索排序不考虑新鲜度（旧记忆与新记忆同权）

### 1.2 目标
- 双层设计：无 LLM 规则层（确定性，零依赖）+ 有 LLM 智能层（本地 qwen3:4b，可选）
- 首批三件套：访问频率衰减 / 语义去重 / 新鲜度权重
- **无 LLM 时完整可用**（硬性要求）：首批三件套全部公式化，不调 LLM
- 新功能默认关闭，可配置开启（不破坏现有用户）
- 零新增依赖（复用现有 ChromaDB + BGE-M3 嵌入）

### 1.3 非目标（YAGNI）
- 不做自动删除（衰减只降权不删，删除走人工维护命令 + dry-run）
- 不做 TTL 硬过期（记忆治理是软衰减，不是缓存）
- 不做跨 namespace 全局治理（按 namespace 独立治理）
- 智能层 consolidation 为 N23 可选，不进首批

## 2. 架构

### 2.1 双层设计

```
┌─────────────────────────────────────────────┐
│  有 LLM 智能层（N23 可选，本地 qwen3:4b）    │
│  - consolidation：归并矛盾记忆                │
│  - 智能遗忘建议：标记可删除候选               │
├─────────────────────────────────────────────┤
│  无 LLM 规则层（N21-N22，确定性，零依赖）     │
│  ① 访问频率衰减  ② 语义去重  ③ 新鲜度权重    │
├─────────────────────────────────────────────┤
│  存储层：ChromaDB（metadata 扩展 access_count/  │
│  last_accessed；余弦相似度；RRF 融合检索）     │
└─────────────────────────────────────────────┘
```

### 2.2 元数据扩展（Record 模型）

现有字段：`created_at`、`updated_at`（ISO 字符串）。新增两个可选字段：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `access_count` | int | 0 | 被检索命中次数（search/ask 命中时 +1） |
| `last_accessed` | str (ISO) | "" | 最近一次命中时间；空=从未命中 |

- 写入 Chroma 时走 `_clean_metadata`（None/空串过滤，兼容旧记录无此字段）
- 旧记录迁移：无需迁移，缺失字段按默认值 0/"" 处理（向后兼容）
- `access_count`/`last_accessed` 在检索命中时由 retriever 异步更新（不阻塞检索返回）

### 2.3 数据流

```
写入路径（POST /api/v1/memories）：
  嵌入 → [去重检查：query top_k 相似] → 超阈值→返回409拦截(不写库) / 未超→新增
  → 写 Chroma（含 access_count=0, last_accessed=""）

检索路径（POST /api/v1/search）：
  嵌入 → 向量检索(top_k) + BM25 → RRF 融合 → [新鲜度权重重排] → [衰减降权]
  → 命中记录异步更新 access_count+1 / last_accessed=now → 返回
```

## 3. 三件套公式与参数

### 3.1 访问频率衰减

**公式**（检索排序时对每条记录计算衰减因子）：

```
decay_factor = exp(-λ * days_since_last_accessed) * (1 + γ * log₂(1 + access_count))
final_score = rrf_score * decay_factor
```

| 参数 | 默认 | 依据 |
|---|---|---|
| `KB_DECAY_LAMBDA` (λ) | 0.02 / 天 | 半衰期 ≈ ln2/0.02 ≈ 35 天；30 天未命中衰减到 ~55%，90 天 ~16% |
| `KB_DECAY_GAMMA` (γ) | 0.3 | 高频访问记忆获得适度加权；access_count=10 → 1+0.3×3.32≈2.0，封顶效应避免极端 |
| `KB_DECAY_ENABLED` | false | 默认关闭 |

- `days_since_last_accessed`：last_accessed 为空时用 created_at 替代（从未命中=创建时间）
- 衰减只降权不删除；降权到接近 0 的记忆仍可被精确匹配检索到（BM25 路径不受衰减影响，仅向量/RRF 排序受影响）
- 无 LLM：纯指数+对数公式，确定性

### 3.2 语义去重

**机制**：写入前对新内容做向量检索，取 top_k=3 最相似记录；若最高余弦相似度 > 阈值，则不写库，返回 409 拦截响应（与 TASK-0069 实现对齐：`kb/service.py` 抛 `DuplicateError`，`kb/api.py` 捕获转 409）。

**409 拦截响应字段**（HTTP 409，JSON）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `error` | str | 固定 `"DUPLICATE"` |
| `message` | str | 人类可读说明，如"检测到语义重复，已拦截写入" |
| `duplicate_of` | str | 命中的最相似旧记录 ID |
| `similarity` | float | 余弦相似度（0-1），供调用方判断 |

- 调用方（agent/CLI）收到 409 后可选择：忽略（不写入）、或改用 `update_memory` 更新已有记录（更新路径不走去重拦截）
- 409 拦截不修改任何已有记录（只读检测，不写库）

| 参数 | 默认 | 依据 |
|---|---|---|
| `KB_DEDUP_THRESHOLD` | 0.92 | BGE-M3 余弦相似度；0.92 以上为高度语义重复（实测同句改写 ~0.94-0.98，相关但不同 ~0.75-0.88）；阈值可调，过高漏去重、过低误拦截 |
| `KB_DEDUP_TOPK` | 3 | 只比对最相似的 3 条，控制开销 |
| `KB_DEDUP_ENABLED` | false | 默认关闭 |

- 无 LLM：纯余弦阈值判定，确定性
- 边界：相似度恰好等于阈值时不拦截（严格 > 阈值才返回 409，避免边界抖动）
- 去重检查失败（如检索异常）时降级为直接新增（不阻塞写入），记 WARNING 日志

### 3.3 新鲜度权重

**公式**（检索排序时对 RRF 融合分加新鲜度因子）：

```
freshness = exp(-β * days_since_updated)
final_score = rrf_score * (1 + α * freshness)
```

| 参数 | 默认 | 依据 |
|---|---|---|
| `KB_FRESHNESS_BETA` (β) | 0.05 / 天 | 半衰期 ≈ 14 天；7 天新鲜度 ~0.70，30 天 ~0.22；比衰减更敏感，突出近期记忆 |
| `KB_FRESHNESS_ALPHA` (α) | 0.3 | 新鲜度最多带来 +30% 排序加权；避免完全压倒相关度 |
| `KB_FRESHNESS_ENABLED` | false | 默认关闭 |

- `days_since_updated`：用 updated_at（更新过的记忆刷新新鲜度）
- 与衰减的区别：新鲜度看 updated_at（内容新旧），衰减看 last_accessed（访问冷热）；两者独立可叠加
- 无 LLM：纯指数公式，确定性
- 总开关 `KB_GOVERNANCE_ENABLED`：false 时三件套全部不生效（等同当前行为）

## 4. API 设计（REST + MCP 双协议）

### 4.1 现有端点改动

| 端点 | 改动 |
|---|---|
| `POST /api/v1/memories` | 写入前走语义去重检查（KB_DEDUP_ENABLED 时）；命中重复返回 409（`error=DUPLICATE`/`message`/`duplicate_of`/`similarity`），不写库；未命中正常写入返回 201 |
| `POST /api/v1/search` | 检索结果走新鲜度权重 + 衰减降权（对应开关开启时）；命中记录异步更新 access_count/last_accessed |
| `POST /api/v1/ask` | 同 search（ask 内部走检索） |

### 4.2 新增端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/v1/governance/stats` | GET | 返回治理统计：总记录数、平均 access_count、超 90 天未命中数、去重候选数（>0.85 未拦截）；只读无副作用 |
| `/api/v1/governance/decay` | POST | 手动触发全量衰减评分重算（dry_run=true 时只返回候选不写库）；用于维护命令 |
| `/api/v1/governance/dedup` | POST | 手动扫描全库重复对（dry_run=true 返回候选对列表含相似度，false 返回拦截统计报告）；只读扫描不自动删除，供人工决策 |

### 4.3 MCP 协议

- 现有 MCP `add_memory` 工具：内部复用 REST 写入路径，自动享受去重；工具描述补"开启去重时重复内容返回 409 不写入，调用方可改用 update_memory"
- 现有 MCP `search_memory` 工具：复用检索路径，自动享受新鲜度+衰减
- 新增 MCP 工具 `get_governance_stats`：返回治理统计（供 agent 评估记忆健康度）
- MCP 工具参数与 REST 对齐，不另设协议

## 5. 里程碑拆分（N21-N23，每个可独立交付验收）

| 节点 | 内容 | 门禁 |
|---|---|---|
| **N21** | spec 评审通过 + Record 元数据扩展（access_count/last_accessed）+ 衰减评分公式实现 + 检索命中异步更新计数 + 单元测试 | spec 需人工评审（本卡） |
| **N22** | 语义去重（写入前相似度检查 + 409 拦截响应，DuplicateError→409）+ 新鲜度权重（检索重排）+ 新增 /governance/stats 端点 + 集成测试 | 标准 |
| **N23** | 维护 CLI（`kb forget --stale --days 90 --dry-run` / `kb dedup --dry-run`）+ 日志审计闭环（每次 409 拦截/降权记日志）+ 智能层 consolidation（可选，本地 qwen3:4b 智能归并矛盾记忆） | 标准 |

- N21 交付后衰减可独立开启验证；N22 交付后去重+新鲜度可独立开启；N23 交付后维护工具+智能层可用
- 每节点走 TDD：先写验收测试→红→实现→绿→全量回归→提交

## 6. 测试策略

### 6.1 单元测试（N21）
```python
def test_record_metadata_access_count_default():
    # 新记录 access_count=0, last_accessed=""

def test_decay_factor_never_accessed_uses_created_at():
    # last_accessed 为空时用 created_at 计算

def test_decay_factor_high_access_boost():
    # access_count 大 → decay_factor 提升（γ 项生效）

def test_decay_factor_old_unaccessed_decays():
    # 90 天未命中 → decay_factor < 0.2

def test_chroma_metadata_skips_none():
    # _clean_metadata 不过滤 access_count=0（0 是有效值），只过滤 None/""
```

### 6.2 集成测试（N22）
```python
def test_dedup_above_threshold_returns_409():
    # 写入两条相似度>0.92 的记录 → 第二条返回 409(error=DUPLICATE/duplicate_of/similarity)，不写库，总记录数=1

def test_dedup_below_threshold_adds_new():
    # 相似度<0.92 → 新增，总记录数=2

def test_dedup_409_response_fields():
    # 409 响应含 error="DUPLICATE"/message/duplicate_of(旧记录ID)/similarity(float)，字段与 TASK-0069 实现对齐

def test_freshness_new_record_ranks_higher():
    # 两条相关度相同记录，新的排前面（freshness 权重生效）

def test_governance_disabled_equals_current_behavior():
    # KB_GOVERNANCE_ENABLED=false 时，检索/写入与当前完全一致（回归）

def test_dedup_failure_degrades_to_add():
    # 去重检查异常时降级为直接新增，不阻塞写入
```

### 6.3 回归与性能
- 全量既有测试保持绿（governance 默认关闭时零行为变化）
- 性能：去重检查增加一次向量检索（top_k=3），写入延迟增加 <50ms（本地 Chroma）；异步更新计数不阻塞检索返回
- 显存：无新增模型（复用 BGE-M3 嵌入）

## 7. 配置项清单

| 配置项 | 默认 | 说明 |
|---|---|---|
| `KB_GOVERNANCE_ENABLED` | false | 总开关；false 时三件套全部不生效 |
| `KB_DECAY_ENABLED` | false | 访问频率衰减开关 |
| `KB_DECAY_LAMBDA` | 0.02 | 衰减速率（/天） |
| `KB_DECAY_GAMMA` | 0.3 | 高频访问加权系数 |
| `KB_DEDUP_ENABLED` | false | 语义去重开关 |
| `KB_DEDUP_THRESHOLD` | 0.92 | 去重余弦相似度阈值 |
| `KB_DEDUP_TOPK` | 3 | 去重比对 top_k |
| `KB_FRESHNESS_ENABLED` | false | 新鲜度权重开关 |
| `KB_FRESHNESS_ALPHA` | 0.3 | 新鲜度加权上限系数 |
| `KB_FRESHNESS_BETA` | 0.05 | 新鲜度衰减速率（/天） |

- 全部走 `kb/config.py`（pydantic-settings）+ `.env`，禁止硬编码
- `.env.example` 补键名与空值/默认值
- 默认全关：现有用户升级后零行为变化

## 8. 风险与避坑

| 风险 | 规避 |
|---|---|
| 去重误拦截（语义相关但不同的信息被 409 拦截） | 阈值 0.92 偏高（只拦截高度重复）；409 返回 duplicate_of 可追溯，调用方可改用 update_memory；提供 dry-run 扫描让人工审核候选 |
| 衰减导致重要旧记忆被埋没 | 衰减只影响向量/RRF 排序，BM25 精确匹配不受影响；access_count 高频项获得 γ 加权对抗衰减；提供 governance/stats 让 agent 评估 |
| 异步更新计数丢失 | 写入失败记 WARNING 不阻塞；计数是软信号，丢失不影响正确性 |
| Chroma metadata 类型限制 | access_count 用 int（Chroma 支持），last_accessed 用 str（ISO）；None/空串走 _clean_metadata 过滤 |
| 旧记录无新字段 | from_chroma 用 .get(key, default) 兼容，缺失=0/"" |
