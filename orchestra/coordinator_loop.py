#!/usr/bin/env python3
"""常驻协调者循环：自动核验 done 卡 → 合并 → 推送 → 清理。

寄生后台运行（python orchestra\coordinator_loop.py），每 60 秒一轮：
1. board.py status 解析 done/failed 卡
2. done 卡：检查 open FBK → 跑 pytest → 全绿 → verify --pass --docs-done
   → git merge --no-ff → push → worktree clean → 删分支
3. failed 卡：verify --reject 回 pending
4. 无 done/failed 时空转
5. 每轮结果写 comm:system（≤300 字符）
6. TASK-0061：每轮对比快照检测新 open FBK，写 comm:system 广播
   'FBK-NNNN open 待裁决（TASK-XXXX/类型）'（快照存 .kb_tmp/，勿入 git）

Ctrl+C 干净退出。
不拆卡（卡用完时 comm:system 通知用户来建新卡）。
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = [str(REPO / "venv" / "Scripts" / "python.exe")]
BOARD = PY + [str(REPO / "orchestra" / "board.py")]
INTERVAL = 60  # 秒
# open FBK 快照（TASK-0061）：记录上一轮见过的 open FBK-ID 集合
SNAPSHOT = REPO / ".kb_tmp" / "fbk_snapshot.json"


def _run(cmd, cwd=REPO, timeout=120):
    """跑命令，返回 (returncode, stdout+stderr)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", cwd=cwd, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "超时"
    except Exception as e:
        return -2, str(e)


def _parse_status():
    """解析 board.py status 输出，返回 done/failed 卡列表 [(task_id, assignee, title)]。"""
    rc, out = _run(BOARD + ["status"])
    if rc != 0:
        return [], [], out
    done, failed = [], []
    for line in out.splitlines():
        # 格式：TASK-0001 verified worker-1 12:30 标题
        m = re.match(r"(TASK-\d+)\s+(done|failed)\s+(\S+)\s+\S+\s+(.*)", line)
        if m:
            tid, status, assignee, title = m.groups()
            if status == "done":
                done.append((tid, assignee, title))
            elif status == "failed":
                failed.append((tid, assignee, title))
    return done, failed, out


def _has_open_fbk(task_id):
    """检查目标卡是否有 open 反馈卡（有则跳过核验，阻塞规则）。"""
    rc, out = _run(BOARD + ["feedback", "list"])
    if rc != 0:
        return False
    for line in out.splitlines():
        if task_id in line and " open " in line:
            return True
    return False


def _list_open_fbks():
    """解析 board feedback list 输出，返回 [(fbk_id, task_id, fb_type)]。

    行格式：FBK-0001 open TASK-0026 objection precheck 22:00 摘要
    仅取 open 状态行；命令失败/无输出返回空列表（广播缺轮，下轮补上）。
    """
    rc, out = _run(BOARD + ["feedback", "list"])
    if rc != 0:
        return []
    fbks = []
    for line in out.splitlines():
        m = re.match(r"(FBK-\d+)\s+open\s+(TASK-\d+)\s+(\S+)", line)
        if m:
            fbks.append(m.groups())
    return fbks


def _broadcast_new_fbks(snapshot_path=None):
    """对比上一轮快照，新出现的 open FBK 写 comm:system 广播（TASK-0061）。

    快照记录上一轮的 open FBK-ID 集合，每轮更新为当前集合：
    - 新增 → 逐条广播 'FBK-NNNN open 待裁决（TASK-XXXX/类型）'
    - 已见过的 → 不重复广播（避免刷屏）
    - 关闭的 → 移出快照（之后再 open 会视为新事件重新广播）
    返回本轮新广播的 [(fbk_id, task_id, fb_type)]。
    """
    path = Path(snapshot_path) if snapshot_path else SNAPSHOT
    current = _list_open_fbks()
    prev = set()
    if path.exists():
        try:
            prev = set(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            prev = set()  # 快照损坏时按空快照处理（宁可多广播一次）
    new = [f for f in current if f[0] not in prev]
    for fbk_id, task_id, fb_type in new:
        msg = f"FBK 广播：{fbk_id} open 待裁决（{task_id}/{fb_type}）"
        _comm_system(msg)
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")
    # 快照更新为当前 open 集合（目录不存在时自动创建）
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(f[0] for f in current),
                                    ensure_ascii=False),
                        encoding="utf-8")
    except OSError as e:
        print(f"[警告] 快照写入失败：{e}")
    return new


def _verify(task_id, action, note=""):
    """核验卡：pass 或 reject。"""
    flag = "--pass" if action == "pass" else "--reject"
    cmd = BOARD + ["verify", task_id, flag, "--note", note]
    # 卡内有"文档同步"行时加 --docs-done
    rc, out = _run(BOARD + ["show", task_id])
    if "文档同步：" in (out or ""):
        cmd.append("--docs-done")
    return _run(cmd)


def _auto_verify_done(task_id, assignee, title):
    """自动核验一张 done 卡：merge → 测试 → verify → push → clean。

    顺序：先 merge --no-ff（自动提交）→ pytest 全绿才 push，失败则 reset 撤销。
    """
    steps = []

    # 1. open FBK 检查（阻塞规则：有 open 反馈卡不核验）
    if _has_open_fbk(task_id):
        return f"{task_id} 跳过（有 open FBK，阻塞核验）"

    # 2. 检查分支是否存在
    branch = f"task/{task_id}"
    rc, out = _run(["git", "rev-parse", "--verify", branch])
    if rc != 0:
        # 无分支卡（直接在主工作区改的）：跑测试 → 补提交 → verify
        # 漏 commit 修复（15f879d 前车之鉴）：worker 共享区改动必须落 main
        rc, out = _run(PY + ["-m", "pytest", "orchestra/tests/", "-q"],
                       timeout=180)
        if rc != 0:
            _verify(task_id, "reject", f"自动核验：测试失败（{rc}）")
            return f"{task_id} 打回（测试失败）"
        rc, out = _run(["git", "status", "--porcelain"])
        if out:  # 有未提交改动才补提交
            _run(["git", "add", "-A"])
            rc, out = _run(["git", "commit", "-m",
                            f"orchestra: {task_id} {title}（自动核验补提交）"])
            if rc != 0:
                return f"{task_id} 补提交失败：{out}"
            _run(["git", "push", "origin", "main"], timeout=60)
            steps.append("已补提交")
        _verify(task_id, "pass", "自动核验：测试全绿")
        return f"{task_id} ✅ 测试全绿/verified{'/'.join('/' + s for s in steps)}（无分支）"

    # 3. merge --no-ff（自动提交到 main）
    rc, out = _run(["git", "merge", "--no-ff", branch, "-m",
                    f"合并: {task_id} {title}（自动核验）"])
    if rc != 0:
        return f"{task_id} 合并冲突：{out}（需人工处理）"
    steps.append("已合并")

    # 4. 跑测试（在 merged main 上）
    rc, out = _run(PY + ["-m", "pytest", "orchestra/tests/", "-q"],
                   timeout=180)
    if rc != 0:
        # 测试失败：撤销 merge，打回
        _run(["git", "reset", "--hard", "HEAD~1"])
        _verify(task_id, "reject", f"自动核验：合并后测试失败（{rc}）")
        last = out.splitlines()[-1] if out else "?"
        return f"{task_id} 打回（合并后测试失败：{last}）"
    steps.append("测试全绿")

    # 5. verify --pass
    _verify(task_id, "pass", "自动核验：合并+测试全绿")
    steps.append("verified")

    # 6. push
    rc, out = _run(["git", "push", "origin", "main"], timeout=60)
    if rc != 0:
        return f"{task_id} 推送失败：{out}"
    steps.append("已推送")

    # 7. worktree clean + 删分支
    _run(BOARD + ["worktree", "clean", task_id])
    _run(["git", "branch", "-d", branch])
    steps.append("已清理")

    return f"{task_id} ✅ {'/'.join(steps)}"


def _comm_system(text):
    """写 comm:system 交流窗（≤300 字符）。"""
    if len(text) > 300:
        text = text[:297] + "..."
    _run(BOARD + ["report", "--channel", "system", "--from", "coordinator",
                  "--text", text])


def _loop_once():
    """一轮自动核验。"""
    done, failed, raw = _parse_status()
    if not done and not failed:
        return None  # 空转

    results = []

    for tid, assignee, title in done:
        msg = _auto_verify_done(tid, assignee, title)
        results.append(msg)
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    for tid, assignee, title in failed:
        rc, out = _verify(tid, "reject", "自动核验：worker 回写 failed")
        results.append(f"{tid} 打回（worker failed）")
        print(f"[{time.strftime('%H:%M:%S')}] {tid} 打回（failed）")

    # 汇总写交流窗
    summary = " | ".join(results)
    _comm_system(f"自动核验：{summary}")

    return results


def main():
    print("=" * 60)
    print("常驻协调者循环启动（每 60 秒一轮，Ctrl+C 退出）")
    print(f"仓库：{REPO}")
    print("=" * 60)

    try:
        while True:
            t = time.strftime("%H:%M:%S")
            # TASK-0061：每轮先检测新 open FBK 并广播（空转轮也检测）
            _broadcast_new_fbks()
            done, failed, raw = _parse_status()
            if not done and not failed:
                # 空转时只打印一行心跳
                pending = sum(1 for l in raw.splitlines()
                            if re.match(r"TASK-\d+\s+pending", l))
                claimed = sum(1 for l in raw.splitlines()
                            if re.match(r"TASK-\d+\s+claimed", l))
                print(f"[{t}] 心跳：待核验0 待办{pending} 进行中{claimed}")
            else:
                print(f"[{t}] 发现 done:{len(done)} failed:{len(failed)}，开始核验...")
                _loop_once()

            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\n协调者循环已退出")
        _comm_system("协调者循环已停止（Ctrl+C）")


if __name__ == "__main__":
    main()
