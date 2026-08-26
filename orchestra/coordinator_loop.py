#!/usr/bin/env python3
"""常驻协调者循环：自动核验 done 卡 → 合并 → 推送 → 清理。

寄生后台运行（python orchestra\coordinator_loop.py），每 60 秒一轮：
1. board.py status 解析 done/failed 卡
2. done 卡：检查 open FBK → 跑 pytest → 全绿 → verify --pass --docs-done
   → git merge --no-ff → push → worktree clean → 删分支
3. failed 卡：verify --reject 回 pending
4. 无 done/failed 时空转
5. 每轮结果写 comm:system（≤300 字符）

Ctrl+C 干净退出。
不拆卡（卡用完时 comm:system 通知用户来建新卡）。
"""
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = [str(REPO / "venv" / "Scripts" / "python.exe")]
BOARD = PY + [str(REPO / "orchestra" / "board.py")]
INTERVAL = 60  # 秒


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
