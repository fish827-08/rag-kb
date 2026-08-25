# V2-2 交流窗命令化实施设计（board.py report / list-comm）

- 日期：2026-08-25
- 状态：设计书（TASK-0007 产出），待拆实现卡
- 依据：`orchestra/protocol.md` 第 7 节（交流窗 comm: 频道约定，v1.1）+ `orchestra/docs/superpowers/plans/2026-08-24-orchestra-v2-iteration.md` 第 3 节（V2-2）
- 目标节点：总线 B1.3；实现走 TDD，测试草案见第 4 节代码块

## 0. 背景与设计原则

协议 v1.1 已允许任何 agent 用 `write_memory` 写入 `comm:` 开头的标签记录作为交流窗（`comm:done` / `comm:issue` / `comm:test` / `comm:system`，结论级信息 ≤300 字符）。本节点把该能力便捷化为 board.py 子命令，让 worker 不必手拼 REST 请求，也让协调者/全员能按频道检索。

**对齐现有模式**（board.py）：复用 `_request()`（urllib REST 封装）、`cmd_status`/`cmd_list_pending` 的取卡方式、`main()` 的 argparse 子命令分发、`check_limits` 的超长校验风格。**不改动任何既有命令行为**。

## 1. 命令签名

```
board.py report --channel done|issue|test|system --from NAME --text "…"
# 写一条交流窗记录；text ≤300 字符（协议 §7 上限）

board.py list-comm [--channel done|issue|test|system] [--limit N]
# 按频道列交流窗记录，最新 N 条；不指定 --channel 则列出全部 comm:* 频道
# --limit 默认 10，须 ≥1
```

**参数与校验规则**：

| 命令 | 参数 | 校验 |
|---|---|---|
| report | `--channel` | 必填，∈ {done,issue,test,system}（argparse choices，非法即报错，退出码 1） |
| report | `--from` | 必填，非空（report 者身份） |
| report | `--text` | 必填，非空且 ≤300 字符（超限 ValueError，中文提示，退出码 1） |
| list-comm | `--channel` | 可选，非法值同 report 报错；缺省 = 全频道 |
| list-comm | `--limit` | 可选，int ≥1，默认 10 |

> 说明：text 的 300 上限是交流窗自身约定，不并入 `LIMITS["result"]`；建议新增常量 `COMM_CHANNELS = ("done","issue","test","system")` 与 `COMM_TEXT_LIMIT = 300`。

## 2. 数据流

### 2.1 report（写）

```
用户/agent 调用 board.py report --channel done --from worker-1 --text "TASK-0005 已回写"
  → 校验 channel / from / text（不合法：SystemExit(1)，不发请求）
  → POST /api/v1/memories
      body = {"content": "<text>", "tags": ["comm:<channel>"], "source": "<from>"}
  → 成功打印：已写 comm:<channel>（记录 <id>）
```

- 记录即协议第 7 节定义的交流窗消息：tag=`comm:<channel>`、source=report 者、content=text。
- 不额外加前缀/包装内容，保持与现有手写 comm 记录一致（可检索、可读）。

### 2.2 list-comm（读）

```
board.py list-comm [--channel X] [--limit N]
  ├─ 指定 X：GET /api/v1/memories?tag=comm:X&limit=1000，仅留该频道
  └─ 缺省  ：GET /api/v1/memories?limit=1000，过滤 tag 中以 "comm:" 开头的记录
  → 按 updated_at 降序，取前 N 条
  → 一行一条打印；无结果提示"无交流窗记录"
```

**输出格式**（与 board.py 一行一卡的简洁风格一致）：

```
HH:MM | comm:done | worker-1 | TASK-0005 已回写，分支哈希 abc1234
HH:MM | comm:issue | worker-2 | registry 冒烟数据待清理
```

- 时间取 `updated_at` 的 `HH:MM`（复用 `_fmt_time` 思路）；text 过长截断至 60 字符加 `…`，避免刷屏。

## 3. 测试清单（TDD 红灯基准，落 orchestra/tests/test_board.py）

实现卡需先行落测试（红灯）再实现转绿。清单：

1. report 写入带正确 tag：断言 POST body `tags=["comm:<channel>"]` 且 `source=<from>`
2. report 非法频道拒绝：`--channel info` 报错且不发请求
3. report 超长 text 拒绝：text 301 字符 → ValueError 且不发请求
4. report 空 from 拒绝：不发请求
5. list-comm 按频道过滤：mock 多条记录，指定 `--channel done` 只列 `comm:done`
6. list-comm 缺省列全频道：含多个 comm:* 均列出
7. list-comm 排序与 limit：按 updated_at 降序取前 N
8. main() 分发：report / list-comm 子命令已注册并分发到对应 cmd 函数
9. 真服务冒烟（验收用）：report 写一条 → list-comm 可见 → REST DELETE 清理，不留残留

**回归**：orchestra 全量测试（当前 28 项）保持全绿；不动已有命令行为。

## 4. 验收测试草案（设计书内代码块，不落 tests/ 实文件）

```python
# 草案：实现卡据此落 tests/test_board.py（mock_request fixture 复用现有 conftest）
import pytest

def test_report_写入带正确tag_and_source(mock_request):
    # 调用 cmd_report(channel="done", from_="worker-1", text="TASK-0005 已回写")
    # 断言 mock 收到 POST /memories，body.tags == ["comm:done"]，body.source == "worker-1"

def test_report_非法频道拒绝():
    # channel="info" → pytest.raises(ValueError/SystemExit)，且不发起请求

def test_report_超长text拒绝():
    # text="x"*301 → 报错（提示 ≤300），不发起请求

def test_report_空from拒绝():
    # from_="" → 报错，不发起请求

def test_list_comm_按频道过滤(mock_request):
    # mock 返回 [comm:done, comm:issue, comm:test] 三条，cmd_list_comm(channel="done")
    # 断言输出只含 comm:done 一条

def test_list_comm_缺省列全频道(mock_request):
    # 同上 mock，cmd_list_comm(channel=None) → 三条全列出

def test_list_comm_排序与limit(mock_request):
    # 三条不同 updated_at，limit=2 → 只输出最新的两条，顺序降序

def test_main_分发(mock_request, capsys):
    # main(["report", "--channel", "done", "--from", "worker-1", "--text", "hi"]) 正常退出 0
    # main(["list-comm"]) 正常退出 0
```

## 5. 边界与不做（YAGNI）

- 不做历史归档/分页：交流窗低频，limit 截断即可（归档窗口归 B3 成本管控）。
- 不校验 text 之外的字段注入风险：content 由 kb 原样存，行为与手写 write_memory 一致。
- 不改 protocol.md：命令只是便捷化，协议约定不变。
- 与 TASK-0006（registry）零文件交集：二者均只动 board.py 与 tests/test_board.py，**须串行排期**（TASK-0006 完成合入后再实现本卡，避免同文件冲突）。
