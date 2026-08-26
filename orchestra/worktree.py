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


def cmd_worktree_clean(task_id: str, repo=None) -> None:
    """删除任务卡 worktree，目录无残留（可再 setup 重建）。"""
    base = Path(repo) if repo else REPO_ROOT
    wt = _wt_dir(task_id, base)
    if not wt.exists():
        print(f"worktree 不存在：{wt}（无需清理）")
        return
    r = subprocess.run(["git", "-C", str(base), "worktree", "remove", "--force",
                        str(wt)], capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"worktree remove 失败：{r.stderr.strip()}")
    print(f"worktree 已清理：{wt}")
