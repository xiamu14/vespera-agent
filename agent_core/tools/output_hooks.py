"""Compact known tool results before they enter model context."""

from itertools import groupby
import json
import re

from pydantic_ai.capabilities import Hooks


output_hooks = Hooks()
_JSON_TEXT_OR_SPACE = re.compile(r'"(?:[^"\\]|\\.)*"|[ \t\r\n]+')


@output_hooks.on.after_tool_execute(tools=["shell", "web_fetch"])
async def compact_tool_output(ctx, *, call, tool_def, args, result):
    if not isinstance(result, str):
        return result

    if call.tool_name == "shell":
        lines = []
        for line, repeats in groupby(result.splitlines(keepends=True)):
            count = sum(1 for _ in repeats)
            if count >= 3 and line.strip():
                lines.append(f"{line}[上一行共出现 {count} 次]\n")
            else:
                lines.append(line * count)
        compact = "".join(lines)
    else:
        if not result.lstrip().startswith(("{", "[")):
            return result
        try:
            json.loads(result, parse_int=str, parse_float=str)
        except (json.JSONDecodeError, RecursionError):
            return result
        compact = _JSON_TEXT_OR_SPACE.sub(
            lambda match: match[0] if match[0].startswith('"') else "", result,
        )

    return compact if len(compact) < len(result) else result
