"""Project file provenance onto the outbound request without changing local history."""
from dataclasses import replace
from integrations.protected_paths import is_protected_path
import json

from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from integrations.network import NetworkDenied
from integrations.network_data import check_data, payload_limit


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON value")


def check_model_payload(payload: bytes) -> None:
    """Scan all model data in stable units so unchanged messages reuse digest evidence."""
    if len(payload) > payload_limit("model"):
        raise NetworkDenied("模型请求超出外发检查预算")
    check_data(b"", destination="model", category="conversation")
    if not payload:
        return
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise NetworkDenied("模型请求 JSON 无法完整检查") from None
    groups = []
    if isinstance(value, dict):
        for key in ("messages", "input", "system", "tools"):
            if isinstance(value.get(key), list):
                groups.append(value[key])
                value[key] = []
        groups.insert(0, [value])
    elif isinstance(value, list):
        groups.append(value)
    else:
        raise NetworkDenied("模型请求需要 JSON 对象或消息数组")
    for group in groups:
        for unit in group:
            # Retain the JSON container: opaque IDs inside messages are not standalone uploads.
            try:
                content = json.dumps([unit], ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")
            except (ValueError, RecursionError):
                raise NetworkDenied("模型请求 JSON 无法完整检查") from None
            check_data(content, destination="model", category="conversation")


def model_messages(messages, root, protected):
    restricted = set()
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart) and part.tool_name in {"read_file", "grep", "search_files"}:
                path = (root / part.args_as_dict().get("path", ".")).resolve()
                relative = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
                if is_protected_path(relative, protected):
                    restricted.add(part.tool_call_id)
    output = []
    for message in messages:
        parts = [replace(part, content="隐私规则禁止将受保护文件的内容发送给模型。")
                 if isinstance(part, ToolReturnPart) and part.tool_call_id in restricted else part
                 for part in message.parts]
        output.append(replace(message, parts=parts))
    return output, restricted
