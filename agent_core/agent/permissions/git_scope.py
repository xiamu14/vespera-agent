"""Resolve local Git write capabilities without trusting repository environment overrides."""
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess

from agent.permissions.execution_scope import path_state
from integrations.git_push import git_environment
from integrations.protected_paths import is_protected_path


@dataclass(frozen=True)
class GitScope:
    worktree: Path
    git_dir: Path
    common_dir: Path
    identities: tuple


def _query(directory):
    executable = shutil.which("git", path=os.defpath)
    if executable is None:
        raise PermissionError("无法定位系统 Git")
    try:
        result = subprocess.run(
            [executable, "--no-optional-locks", "-c", "core.hooksPath=/dev/null", "-C", str(directory),
             "rev-parse", "--path-format=absolute", "--show-toplevel", "--absolute-git-dir", "--git-common-dir"],
            env=git_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=5,
        )
    except subprocess.TimeoutExpired:
        raise PermissionError("无法及时确认 Git 仓库边界") from None
    if result.returncode or len(result.stdout) > 16384:
        raise PermissionError("无法确认 Git 仓库边界")
    try:
        values = result.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise PermissionError("Git 仓库路径无法完整检查") from None
    if len(values) != 3 or any(not Path(value).is_absolute() for value in values):
        raise PermissionError("Git 仓库路径无效")
    return tuple(Path(value) for value in values)


def resolve_git_scopes(root, directories, initializations, protected, forbidden):
    scopes = []
    for directory in dict.fromkeys(directories):
        if directory.resolve() != directory or not directory.is_relative_to(root):
            raise PermissionError("Git 目标不在固定工作区内")
        relative = directory.relative_to(root).as_posix()
        if is_protected_path(relative, protected):
            raise PermissionError("Git 工作树位于受保护资源内")
        marker = directory / ".git"
        if directory in initializations and not marker.exists() and not marker.is_symlink():
            if not directory.is_dir():
                raise PermissionError("Git 初始化目录不存在")
            worktree, git_dir, common = directory, marker, marker
        else:
            worktree, git_dir, common = _query(directory)
            if worktree != directory or worktree.resolve() != worktree:
                raise PermissionError("Git 写入必须明确指定目标仓库根目录")
            if marker.is_symlink() or git_dir.resolve() != git_dir or common.resolve() != common:
                raise PermissionError("Git 元数据路径包含未授权链接")
            if marker.is_dir():
                if git_dir != marker or common != marker:
                    raise PermissionError("Git 元数据目录与目标仓库不一致")
            elif marker.is_file():
                if git_dir.parent != common / "worktrees" or not git_dir.is_dir():
                    raise PermissionError("仅支持已登记的 linked worktree Git 元数据")
                backlink = git_dir / "gitdir"
                if backlink.is_symlink() or backlink.read_text().strip() != str(marker):
                    raise PermissionError("Git worktree 反向绑定与当前工作区不一致")
            else:
                raise PermissionError("Git 仓库标识无效")
        for metadata in (git_dir, common):
            if any(metadata == item or metadata.is_relative_to(item) or item.is_relative_to(metadata)
                   for item in forbidden):
                raise PermissionError("Git 元数据与硬保护路径重叠")
        paths = tuple(dict.fromkeys((directory, marker, git_dir, common, git_dir / "gitdir", git_dir / "commondir",
                                     git_dir / "HEAD", git_dir / "index", common / "config")))
        identities = []
        for path in paths:
            state = path_state(path)
            if path.is_dir() and state[0][1] is not None:
                state[0] = (state[0][0], state[0][1][:3])
            identities.append(tuple(state))
        identities = tuple(identities)
        scopes.append(GitScope(worktree, git_dir, common, identities))
    return tuple(scopes)
