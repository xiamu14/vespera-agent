"""macOS process sandbox plus file-backend checks; never silently falls back."""
import asyncio
import json
from fnmatch import translate
import re
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import weakref

from pydantic_ai.capabilities import LocalWorkspace
from pydantic_ai.workspaces import LocalWorkspaceBackend
from pydantic_ai.workspaces.protocol import FileEntry

from agent.permissions.policy import CURRENT_GRANT
from agent.permissions.rules import git_access
from agent.permissions.secure_io import CheckedRead
from integrations.protected_paths import is_protected_path


async def _finish_mutation(operation):
    # Cancelling an await cannot stop a filesystem thread; keep the execution lease until it exits.
    task = asyncio.create_task(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class RestrictedBackend(LocalWorkspaceBackend):
    def __init__(self, root: Path, policy):
        self.root = root
        self.policy = policy
        self.temporary = tempfile.TemporaryDirectory(prefix="coder-exec-")
        weakref.finalize(self, self.temporary.cleanup)
        self.scratch = Path(self.temporary.name).resolve()
        super().__init__(root, env={"TMPDIR": str(self.scratch), "UV_CACHE_DIR": str(self.scratch / "uv-cache")})

    def close(self):
        self.temporary.cleanup()

    def check(self, path: str, *, write=False):
        if write and getattr(self.policy, "role_read_only", False):
            raise PermissionError("当前角色只读，不能修改工作区")
        target = Path(path).resolve()
        if any(target == denied or target.is_relative_to(denied) for denied in self.policy.forbidden):
            raise PermissionError("工具不能访问权限配置、凭据目录或运行存储")
        grant = CURRENT_GRANT.get()
        if grant and grant.target == target and (not write or grant.write):
            return
        if target.is_relative_to(self.scratch):
            return
        if target.is_relative_to(self.root):
            settings, _ = self.policy_settings()
            relative = target.relative_to(self.root).as_posix()
            if is_protected_path(relative, settings.protected_paths):
                raise PermissionError("受保护资源缺少本次调用授权")
            return
        if not write and any(target.is_relative_to(root) for root in self.policy.readonly):
            return
        raise PermissionError("工作目录之外的文件缺少本次调用授权")

    def policy_settings(self):
        from agent.permissions.config import load_settings
        return load_settings(self.policy.path)

    async def read_bytes(self, path):
        self.check(path)
        checked = CheckedRead(path)
        try:
            grant = CURRENT_GRANT.get()
            if grant and grant.target is not None and not checked.path.is_relative_to(grant.target):
                raise PermissionError("读取目标与本次调用授权不一致")
            self.check(str(checked.path))
            checked.verify()
            async with self._file_lock(str(checked.path)):
                # Keep both the descriptor and lock alive until the worker actually exits.
                return await _finish_mutation(asyncio.to_thread(checked.read))
        finally:
            checked.close()

    async def write_bytes(self, path, data):
        self.check(path, write=True)
        target = Path(path).resolve()
        grant = CURRENT_GRANT.get()
        if grant and grant.target is not None and not target.is_relative_to(grant.target):
            raise PermissionError("写入目标与本次调用授权不一致")
        self.check(str(target), write=True)
        checked = CheckedRead(str(target.parent), directory=True, create_directories=True, follow_links=False)
        try:
            mode = checked.bind_file(target.name)
            async with self._file_lock(str(target)):
                def write():
                    self.check(str(target), write=True)
                    checked.replace_bytes(target.name, data, mode)
                return await _finish_mutation(asyncio.to_thread(write))
        finally:
            checked.close()

    async def list_dir(self, path):
        self.check(path)
        checked = CheckedRead(path, directory=True)
        try:
            grant = CURRENT_GRANT.get()
            if grant and grant.target is not None and not checked.path.is_relative_to(grant.target):
                raise PermissionError("目录目标与本次调用授权不一致")
            self.check(str(checked.path))
            def listing():
                checked.verify()
                entries = []
                with os.scandir(checked.fd) as scan:
                    for child in sorted(scan, key=lambda item: item.name):
                        try:
                            info = child.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        is_dir = stat.S_ISDIR(info.st_mode)
                        entries.append(FileEntry(name=child.name, path=str(checked.path / child.name),
                                                 is_dir=is_dir, size=None if is_dir else info.st_size))
                checked.verify()
                return entries
            return await _finish_mutation(asyncio.to_thread(listing))
        finally:
            checked.close()

    async def stat(self, path):
        # FileSystem checks the parent directory of an individually approved external file.
        target = Path(path).resolve()
        if any(target == denied or target.is_relative_to(denied) for denied in self.policy.forbidden):
            raise PermissionError("禁止访问受信任配置或运行存储")
        return await super().stat(path)

    async def make_dir(self, path):
        self.check(path, write=True)
        return await _finish_mutation(super().make_dir(path))

    async def remove(self, path):
        self.check(path, write=True)
        return await _finish_mutation(super().remove(path))

    async def exists(self, path):
        self.check(path)
        return await super().exists(path)

    def profile(self, *, git_write=False, write_targets=None, git_scopes=()):
        def subpath(path):
            return f"(subpath {json.dumps(str(Path(path).resolve()))})"
        read = ["/System", "/usr", "/bin", "/sbin", "/opt/homebrew", "/Library", "/private/etc",
                Path(sys.prefix), Path.home() / ".local/share/uv", self.root, self.scratch, *self.policy.readonly]
        git_read = git_write or self.policy.allow_sandboxed_shell
        if git_read:
            read.extend(["/Applications/Xcode.app/Contents/Developer", Path.home() / ".gitconfig", Path.home() / ".config/git"])
            from storage.worktrees import git
            common = self.root / ".git"
            if common.is_file():
                common = Path(git(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
            read.append(common)
        metadata = tuple(dict.fromkeys(path for scope in git_scopes for path in (scope.git_dir, scope.common_dir)))
        read.extend(metadata)
        read_filters = " ".join(subpath(path) for path in [*read, "/dev"])
        jobs = self.root / ".pydantic-ai-harness" / "shell"
        if write_targets is not None and jobs.resolve() != jobs:
            raise PermissionError("Shell 运行目录包含符号链接")
        writable = [self.root] if write_targets is None else [jobs, *write_targets]
        write_filters = " ".join(f"(subpath {json.dumps(str(path))})" for path in [*writable, self.scratch, *metadata])
        lines = [
            "(version 1)", "(allow default)",
            f'(deny file-read-data (require-not (require-any (literal "/") {read_filters})))',
            f'(deny file-write* (require-not (require-any {write_filters} (literal "/dev/null"))))',
            "(deny network*)",
            "(deny appleevent-send)", "(deny lsopen)",
        ]
        if git_write and not git_scopes:
            lines.append(f'(allow file-write* {subpath(common)})')
        for path in self.policy.forbidden:
            lines.append(f"(deny file-read-data file-write* {subpath(path)})")
        # Apply the same configured protected globs to child processes.
        settings, _ = self.policy_settings()
        for pattern in settings.protected_paths:
            if git_write and not git_scopes and pattern in {".git", ".git/**"}:
                continue
            expression = translate(pattern)[4:-3].replace("(?>", "(").replace("(?:", "(")
            prefix = "^" + re.escape(str(self.root)) + "/"
            if "/" not in pattern:
                prefix += "(.*/)?"
            expression = prefix + "(" + expression + ")(/|$)"
            expression = expression.replace('"', chr(92) + '"')
            operations = "file-write*" if git_read and pattern in {".git", ".git/**"} else "file-read-data file-write*"
            match = f'(regex #"{expression}")'
            if git_scopes and pattern in {".git", ".git/**"}:
                exceptions = " ".join(subpath(path) for path in metadata)
                match = f'(require-all {match} (require-not (require-any {exceptions})))'
            lines.append(f'(deny {operations} {match})')
        return "\n".join(lines)

    async def run(self, command, *, shell=False, env=None, timeout=None):
        if getattr(self.policy, "role_read_only", False):
            raise PermissionError("当前角色不能执行命令")
        if isinstance(command, str) and not shell:
            raise TypeError("字符串命令必须指定 shell=True")
        if not isinstance(command, str) and shell:
            raise TypeError("argv 命令不能指定 shell=True")
        settings, _ = self.policy_settings()
        grant = CURRENT_GRANT.get()
        sandbox_only = bool(grant and grant.sandbox_only)
        operation = grant.shell_operation if grant else None
        if operation is not None and not operation.supported:
            raise PermissionError(operation.reason)
        if not settings.sandbox:
            raise PermissionError("命令需要可执行的沙箱约束")
        if sys.platform != "darwin" or shutil.which("sandbox-exec") is None:
            raise OSError("本机执行隔离不可用；拒绝执行。请配置可用的隔离后端。")
        elevated_command = bool(grant and grant.command and not sandbox_only)
        access = git_access(grant.subject) if grant and grant.command else None
        git_write = bool((elevated_command and access is not None)
                         or (self.policy.allow_sandboxed_shell and sandbox_only and access == "write"))
        write_targets = None
        git_scopes = ()
        if operation is not None:
            git_write = operation.git_write
            elevated_command = False
            write_targets = operation.targets if operation.action == "ask" else ()
            if git_write:
                from agent.permissions.git_scope import resolve_git_scopes
                git_scopes = resolve_git_scopes(self.root, operation.git_roots, operation.git_inits,
                                                settings.protected_paths, self.policy.forbidden)
                if not git_scopes or git_scopes != grant.git_scopes:
                    raise PermissionError("Git 仓库或元数据在授权后发生变化")
                if any(key.startswith("GIT_") for key in (env or {})):
                    raise PermissionError("Git 写入不能接受调用方环境覆盖")
                env = {**(env or {}), "GIT_CONFIG_COUNT": "3",
                       "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
                       "GIT_CONFIG_KEY_1": "commit.gpgSign", "GIT_CONFIG_VALUE_1": "false",
                       "GIT_CONFIG_KEY_2": "core.fsmonitor", "GIT_CONFIG_VALUE_2": "false"}
        if git_write or self.policy.allow_sandboxed_shell:
            # Resolve Apple's launcher before sandboxing; its SDK cache ignores TMPDIR.
            located = await super().run(["/usr/bin/xcrun", "--find", "git"], timeout=5)
            if located.exit_code != 0:
                raise OSError("无法定位系统 Git，拒绝运行")
            git_bin = Path(located.stdout.strip()).parent
            search_path = os.defpath if operation is not None and git_write else os.environ.get("PATH", os.defpath)
            env = {**(env or {}), "PATH": f"{git_bin}:{search_path}"}
        argv = ["/bin/sh", "-c", command] if isinstance(command, str) else list(command)
        invocation = ["/usr/bin/sandbox-exec", "-p", self.profile(git_write=git_write,
                                                                   write_targets=write_targets,
                                                                   git_scopes=git_scopes), *argv]
        return await super().run(invocation, env=env, timeout=timeout)


class RestrictedWorkspace(LocalWorkspace):
    def __init__(self, root: Path, policy):
        super().__init__(root)
        self.policy = policy

    def get_workspace(self, ctx, *, ref):
        self.policy.refresh_readonly()
        backend = RestrictedBackend(Path(self.working_dir), self.policy)
        if ref is not None and ref != backend.ref:
            shutil.rmtree(backend.scratch)
            return None
        return backend
