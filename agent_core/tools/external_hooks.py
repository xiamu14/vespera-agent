"""Declarative result hooks. Configuration cannot supply executable code."""

import asyncio
from contextlib import closing
from pathlib import Path
import os
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai.capabilities import Hooks

from agent.permissions.config import digest, redact
from integrations.config_home import config_home

PREVIEW = 4096
EXTRA_LIMIT = 4096


class ResultHook(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tool: Literal["shell", "web_fetch"]
    remove_prefix: str = Field(min_length=1, max_length=128, pattern=r"^[^\r\n]+$")
    extra_chars: int = Field(default=0, ge=0, le=EXTRA_LIMIT)


class HookSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    hooks: list[ResultHook] = Field(max_length=2)


def hook_config_path():
    configured = os.getenv("CODER_HOOK_CONFIG")
    if configured is None:
        default = config_home() / "hooks.json"
        return default.resolve() if default.exists() else None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ValueError("Hook 配置必须使用绝对路径")
    return path.resolve()


def load_hook_settings(path):
    if path is None:
        return HookSettings(hooks=[])
    with path.open("rb") as source:
        data = source.read(16385)
    if len(data) > 16384:
        raise ValueError("拒绝加载 Hook：配置超过 16 KiB")
    try:
        settings = HookSettings.model_validate_json(data)
    except ValidationError as exc:
        locations = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        raise ValueError(f"拒绝加载 Hook：配置含非法字段或操作（{locations}）") from None
    if len({hook.tool for hook in settings.hooks}) != len(settings.hooks):
        raise ValueError("每个工具最多配置一个外部 Hook")
    return settings


def hook_grant(database, fingerprint, *, save=False):
    with closing(sqlite3.connect(database)) as connection, connection:
        if save:
            connection.execute("CREATE TABLE IF NOT EXISTS hook_grants (fingerprint TEXT PRIMARY KEY)")
            connection.execute("INSERT OR IGNORE INTO hook_grants VALUES (?)", (fingerprint,))
            return True
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'hook_grants'"
        ).fetchone()
        return bool(table and connection.execute(
            "SELECT 1 FROM hook_grants WHERE fingerprint = ?", (fingerprint,)
        ).fetchone())


def external_result_hooks(settings, interaction, *, database, workspace, config_path):
    hooks = Hooks(id="external_result_hooks")
    by_tool = {hook.tool: hook for hook in settings.hooks}

    if by_tool:
        @hooks.on.after_tool_execute(tools=list(by_tool))
        async def filter_result(ctx, *, call, tool_def, args, result):
            if not isinstance(result, str):
                return result
            if call.tool_name == "web_fetch" and result.lstrip().startswith(("{", "[", "<")):
                return result
            rule = by_tool[call.tool_name]
            end = PREVIEW
            if rule.extra_chars and len(result) > PREVIEW:
                limit = min(len(result), PREVIEW + rule.extra_chars)
                fingerprint = digest([str(workspace), str(config_path), PREVIEW, rule.model_dump()])
                approved = await asyncio.to_thread(hook_grant, database, fingerprint)
                if not approved:
                    approved, remember = await interaction.approve(
                        f"外部 Hook：{call.tool_name} 结果过滤\n"
                        f"工作目录：{workspace}\n配置来源：{config_path}\n"
                        f"本次申请字符区间 [{PREVIEW}, {limit})\n"
                        f"长期允许范围：此工作目录的相同规则，每次最多额外 {rule.extra_chars} 字符\n"
                        f"删除行前缀：{redact(rule.remove_prefix)}",
                        rememberable=True, header="Hook 审批", remember_label="以后都允许",
                        prompt="选择本次允许、以后都允许，或拒绝开放额外过滤范围。",
                    )
                    if remember:
                        await asyncio.to_thread(hook_grant, database, fingerprint, save=True)
                if approved:
                    end = limit

            # Retain the boundary line and everything outside the granted range.
            if end < len(result):
                end = result.rfind("\n", 0, end) + 1
            lines = result[:end].splitlines(keepends=True)
            removed = 0
            kept = []
            for line in lines:
                if line.startswith(rule.remove_prefix) and not line.lstrip().startswith(
                    ("ERROR", "WARN", "Traceback", "[exit code:", "PID:", "Output:", "Status:", "{", "[")
                ):
                    removed += 1
                else:
                    kept.append(line)
            if not removed:
                return result
            compact = f"[外部 Hook 已省略 {removed} 行]\n" + "".join(kept) + result[end:]
            return compact if len(compact) < len(result) else result

    return hooks
