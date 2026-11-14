"""Application-owned, detached worktrees; retain results until explicitly removed."""
import asyncio
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile


def git(root: Path, *args: str) -> str:
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                stdout=output, stderr=subprocess.PIPE, text=True, timeout=30,
            )
            output.seek(0)
            value = output.read(65537)
    except subprocess.TimeoutExpired as exc:
        raise OSError("Git 操作超时；请检查工作区状态。") from exc
    if result.returncode:
        raise ValueError(f"Git 操作失败：{result.stderr.strip()}")
    text = value[:65536].decode("utf-8", errors="replace").strip()
    return text + ("\n[Git output truncated at 64 KiB]" if len(value) > 65536 else "")


@dataclass(frozen=True)
class Worktree:
    source: str
    path: str
    baseline: str


class Worktrees:
    def __init__(self, directory: Path, source: Path):
        self.directory = directory.resolve()
        self.source = source.resolve()

    async def allocate(self, key: str, revision: str = "HEAD") -> Worktree:
        # A cancelled thread may still create Git metadata: wait until it settles.
        from agent.permissions.workspace import _finish_mutation
        return await _finish_mutation(asyncio.to_thread(self._allocate, key, revision))

    def _allocate(self, key, revision):
        root = Path(git(self.source, "rev-parse", "--show-toplevel")).resolve()
        if root != self.source:
            raise ValueError("独立角色任务目前要求工作目录为 Git 仓库根目录。")
        import re
        if revision != "HEAD" and not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise ValueError("基线必须是完整 commit ID。")
        baseline = git(root, "rev-parse", "--verify", f"{revision}^{{commit}}")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / sha256(key.encode()).hexdigest()
        manifest = path.with_suffix(".json")
        if manifest.exists():
            return self.validate(str(path))
        if path.exists():
            raise ValueError(f"未登记的 worktree 需要检查：{path}")
        git(root, "worktree", "add", "--detach", str(path), baseline)
        record = Worktree(str(root), str(path), baseline)
        manifest.write_text(json.dumps(asdict(record)), encoding="utf-8")
        return record

    def validate(self, value: str) -> Worktree:
        path = Path(value).resolve()
        if path.parent != self.directory:
            raise ValueError("角色任务只能使用应用登记的 worktree。")
        record = Worktree(**json.loads(path.with_suffix(".json").read_text(encoding="utf-8")))
        if record.path != str(path) or record.source != str(self.source):
            raise ValueError("worktree 登记与当前项目不匹配。")
        if not path.is_dir():
            raise ValueError(f"worktree 已丢失：{path}")
        common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common != git(self.source, "rev-parse", "--path-format=absolute", "--git-common-dir"):
            raise ValueError("worktree 所属仓库发生变化。")
        git(path, "merge-base", "--is-ancestor", record.baseline, "HEAD")
        if Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path:
            raise ValueError("worktree 根目录记录无效。")
        return record
