"""Choices from the CLI catalog and the current conversation's user messages."""
from hashlib import sha256
import json
from pathlib import Path
import re

from pydantic_ai import BinaryContent
from pydantic_ai.messages import TextContent, UserPromptPart
from pydantic_ai.workspaces import LocalWorkspaceBackend

from agent.roles import ROLES

FILE_REFERENCE = re.compile(r'［文件引用：("(?:[^"\\]|\\.)*")］|\[文件引用：("(?:[^"\\]|\\.)*")\]')
ATTACHMENT_REFERENCE = re.compile(r'［附件引用："([0-9a-f]{64})"］')
SLASH_COMMAND = re.compile(rf'{FILE_REFERENCE.pattern}|(?<!\S)/(?P<name>[^\s/]+)')


def literal_triggers(text):
    return re.sub(r"\\([/@])", r"\1", text)


async def skill_catalog(runtime, root):
    catalog = await runtime.skills.load(LocalWorkspaceBackend(root))
    return {skill.name: skill for skill in catalog.skills
            if skill.user_invocable and skill.name not in ROLES}


async def slash_invocation(prompt, runtime, root):
    skills = None
    for match in SLASH_COMMAND.finditer(prompt):
        name = match["name"]
        if name is None:
            continue
        arguments = " ".join(part for text in (prompt[:match.start()], prompt[match.end():])
                             if (part := text.strip()))
        if name in ROLES:
            if name != "super":
                return f"/{name} {arguments}", None, arguments
            continue
        if skills is None:
            skills = await skill_catalog(runtime, root)
        if name in skills:
            return None, skills[name], arguments
    return None, None, ""


def history_references(history):
    files, attachments = {}, {}
    for message in history:
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                continue
            content = [part.content] if isinstance(part.content, str) else part.content
            filename = None
            for item in content:
                if isinstance(item, TextContent) and isinstance(item.metadata, dict):
                    if item.metadata.get("origin") == "ani-upload":
                        filename = item.metadata.get("filename")
                        continue
                if isinstance(item, BinaryContent):
                    key = sha256(item.data).hexdigest()
                    attachments.setdefault(key, (filename or f"{item.media_type} · {key[:8]}", item))
                elif isinstance(item, str):
                    for match in FILE_REFERENCE.finditer(item):
                        try:
                            path = json.loads(match[1] or match[2])
                            if Path(path).is_absolute():
                                files[path] = {"path": path, "label": path, "folder": False}
                        except (ValueError, OSError):
                            continue
                filename = None
    return files, attachments


def reference_choices(history, query):
    files, attachments = history_references(history)
    rows = [{"path": key, "label": name, "folder": False, "attachment": True}
            for key, (name, _) in reversed(attachments.items())]
    rows.extend(reversed(files.values()))
    return [row for row in rows if query.casefold() in row["label"].casefold()][:10]


def prompt_attachments(prompt, paths, uploads, history):
    _, saved = history_references(history) if ATTACHMENT_REFERENCE.search(prompt) else ({}, {})
    reused = []
    for key in dict.fromkeys(ATTACHMENT_REFERENCE.findall(prompt)):
        if key not in saved:
            raise ValueError("附件已不在当前会话记录中")
        reused.append(saved[key][1])
    if len(uploads) + len(reused) > 4 or sum(len(item.data) for item in [*uploads, *reused]) > 10 * 1024 * 1024:
        raise ValueError("每条消息最多引用 4 个附件，总大小不超过 10 MiB")
    content = []
    for path, upload in zip(paths, uploads, strict=True):
        name = Path(path).name
        content.extend((TextContent(f"附件：{name}", metadata={"origin": "ani-upload", "filename": name}), upload))
    return [*content, *reused]
