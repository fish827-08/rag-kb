"""git worktree 隔离模块（TASK-0031 包化⑤：自 board.py 机械搬移）。

含 worktree 相关：
- REPO_ROOT / WORKTREES_DIR：仓库根与隔离目录常量
- _wt_dir：任务卡 worktree 目录路径
- cmd_worktree_setup / enter / clean：建/进/清隔离目录（TASK-0025 治本分支串扰）

仅标准库 subprocess/pathlib，不依赖 kb 服务与 client.py；
board.py 仅做 CLI 调度，通过 import 复用本模块。
"""
import subprocess
from pathlib import Path

# 仓库根（worktree.py 位于 orchestra/ 下，仓库根为其上一级）
REPO_ROOT = Path(__file__).resolve().parent.parent
WORKTREES_DIR = REPO_ROOT / ".worktrees"


def _wt_dir(task_id: str, repo=None) -> Path:
    """返回任务卡 worktree 目录路径；repo 为测试注入用（默认 REPO_ROOT）。"""
    base = Path(repo) if repo else REPO_ROOT
    return base / ".worktrees" / task_id


def cmd_worktree_setup(task_id: str, repo=None) -> None:
    """为任务卡建立隔离 worktree（TASK-0025 治本分支串扰）。

    在 <repo>/.worktrees/TASK-NNNN 检出 task/TASK-NNNN 分支（非 detached，
    worktree 独占该分支，主工作区无法再 checkout → 并发天然隔离）。
    已注册 worktree / 脏目录（非空）拒绝；空残留目录自动清掉。
    """
    base = Path(repo) if repo else REPO_ROOT
    branch = f"task/{task_id}"
    wt = _wt_dir(task_id, base)
    if wt.exists():
        listed = subprocess.run(
            ["git", "-C", str(base), "worktree", "list", "--porcelain"],
            capture_output=True, text=True).stdout
        if f"worktree {wt}" in listed:
            raise ValueError(f"worktree 已存在：{wt}（可 clean 后重建或直接使用）")
        if any(wt.iterdir()):
            raise ValueError(f"目标目录非空（脏目录）：{wt}")
        wt.rmdir()  # 空残留目录，清掉后重建
    check = subprocess.run(["git", "-C", str(base), "rev-parse", "--verify",
                            branch], capture_output=True)
    if check.returncode != 0:
        raise ValueError(f"分支 {branch} 不存在（协调者应预建该分支）")
    r = subprocess.run(["git", "-C", str(base), "worktree", "add",
                        str(wt), branch], capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"worktree add 失败：{r.stderr.strip()}")
    print(f"worktree 就绪：{wt}（分支 {branch}）")


def cmd_worktree_enter(task_id: str, repo=None) -> None:
    """打印进入该任务卡 worktree 的目录（worker 在其内开发/提交）。"""
    base = Path(repo) if repo else REPO_ROOT
    wt = _wt_dir(task_id, base)
    if not wt.exists():
        raise ValueError(f"worktree 不存在：{wt}（先 setup）")
    print(str(wt))


def cmd_worktree_clean(task_id: str = "", repo=None, all_: bool = False) -> None:
    """删除任务卡 worktree，目录无残留（可再 setup 重建）。

    --all 模式（TASK-0042）：扫描 .worktrees/ 下所有目录批量清理，
    逐个 git worktree remove --force，非原子（一个失败不影响其他），
    清理前安全提示列出将清理的目录；无目录时明确提示。
    无 --all 时保持单卡清理行为（需指定 task_id）。
    """
    base = Path(repo) if repo else REPO_ROOT
    wt_root = base / ".worktrees"
    if all_:
        if not wt_root.exists() or not any(wt_root.iterdir()):
            print("无 worktree 目录可清理")
            return
        targets = sorted([d for d in wt_root.iterdir() if d.is_dir()])
        print(f"将批量清理 {len(targets)} 个 worktree：")
        for t in targets:
            print(f"  - {t}")
        success = 0
        failed = 0
        for t in targets:
            r = subprocess.run(
                ["git", "-C", str(base), "worktree", "remove", "--force", str(t)],
                capture_output=True, text=True)
            if r.returncode == 0:
                print(f"已清理：{t}")
                success += 1
            else:
                print(f"清理失败（跳过）：{t} — {r.stderr.strip()}")
                failed += 1
            # 兜底：非 git worktree 的脏目录残留时 rmdir 清掉
            if t.exists():
                try:
                    t.rmdir()
                except OSError:
                    pass
        print(f"批量清理完成：成功 {success}，失败 {failed}")
        return
    # 单卡模式
    if not task_id:
        raise ValueError("单卡清理需指定 task_id，或使用 --all 批量清理")
    wt = _wt_dir(task_id, base)
    if not wt.exists():
        print(f"worktree 不存在：{wt}（无需清理）")
        return
    r = subprocess.run(["git", "-C", str(base), "worktree", "remove", "--force",
                        str(wt)], capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"worktree remove 失败：{r.stderr.strip()}")
    print(f"worktree 已清理：{wt}")
