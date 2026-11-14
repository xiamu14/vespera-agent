"""Read-only Git evidence with fixed argv and bounded output."""
import asyncio
from pathlib import Path
import re

from pydantic_ai import RunContext, ModelRetry

from storage.worktrees import git


async def review_diff(ctx: RunContext, base_commit: str | None = None) -> str:
    """Read the current commit's changes, or compare against a full base commit ID."""
    root = Path(await ctx.workspace.working_dir())
    if base_commit is not None:
        if not re.fullmatch(r"[0-9a-f]{40,64}", base_commit):
            raise ModelRetry("base_commit 必须是完整 commit ID。")
        args = ("diff", "--no-ext-diff", "--no-textconv", base_commit, "HEAD", "--", ".")
    else:
        args = ("show", "--format=", "--no-ext-diff", "--no-textconv", "HEAD", "--", ".")
    try:
        value = await asyncio.to_thread(git, root, *args)
    except (OSError, ValueError) as exc:
        raise ModelRetry(f"无法读取 Git 差异：{exc}。可继续使用文件读取和搜索。") from exc
    return value[:24000] + ("\n[diff 超过 24000 字符，后续请按文件读取]" if len(value) > 24000 else "")
