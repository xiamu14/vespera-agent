"""Record deliveries at the tool boundary and project them into conversation cards."""

import asyncio
from contextvars import ContextVar
from datetime import UTC, datetime
from hashlib import sha256
import logging
import mimetypes
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
from urllib.parse import unquote, urlsplit
from tempfile import NamedTemporaryFile

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.mcp import ResourceLink
from pydantic_ai.messages import BinaryContent, ToolCallPart, ToolReturn, ToolReturnPart

from agent.permissions.rules import git_access
from context.sources import visible_name
from storage.worktrees import git


INLINE_MEDIA = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
    "audio/wav": ".wav", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
    "video/mp4": ".mp4", "video/webm": ".webm",
}


LOGGER = logging.getLogger(__name__)
TURN = ContextVar("delivery_turn", default=None)
FILE_TOOLS = {"write_file", "edit_file", "create_directory"}
MEDIA = {
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".gif", ".webp"), "image"),
    **dict.fromkeys((".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"), "audio"),
    **dict.fromkeys((".mp4", ".mov", ".m4v", ".webm", ".mkv"), "video"),
}


def snapshot(root):
    entries = {}
    scanned = 0
    def unreadable(error):
        if not isinstance(error, FileNotFoundError):
            raise error

    # shortcut: bounded metadata scan, use a workspace change journal for large trees.
    for directory, folders, files in os.walk(root, onerror=unreadable):
        scanned += 1
        if scanned > 4000:
            return None
        folders[:] = sorted(name for name in folders if visible_name(name))
        for name in sorted(files):
            if not visible_name(name):
                continue
            scanned += 1
            if scanned > 4000:
                return None
            path = Path(directory) / name
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                return None
            if stat.S_ISREG(info.st_mode):
                entries[str(path.relative_to(root))] = (
                    info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                )
    return entries


def safe_snapshot(root):
    try:
        return snapshot(root)
    except OSError:
        return None


def changes(before, after):
    if before is None or after is None:
        return
    removed = {path: before[path][:2] for path in before.keys() - after.keys()}
    added = after.keys() - before.keys()
    by_inode = {}
    for path in removed:
        by_inode.setdefault(removed[path], []).append(path)
    for path in sorted(added):
        candidates = by_inode.get(after[path][:2], [])
        if len(candidates) == 1 and candidates[0] in removed:
            old = candidates[0]
            del removed[old]
            yield path, "rename", old
        else:
            yield path, "create", None
    for path in sorted(removed):
        yield path, "delete", None
    for path in sorted(before.keys() & after.keys()):
        if before[path] != after[path]:
            yield path, "edit", None


def commit_directory(root, command):
    """Recognize explicit commits, including Git -C and simple cd chains."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
        segments = [[]]
        for word in words:
            if word in {"&&", ";", "||"}:
                segments.append([])
            else:
                segments[-1].append(word)
        cwd = root
        for segment in segments:
            if len(segment) == 2 and segment[0] == "cd":
                cwd = (cwd / segment[1]).resolve()
            elif segment and Path(segment[0]).name == "git":
                directory = cwd
                index = 1
                while index + 1 < len(segment) and segment[index] in {"-C", "-c"}:
                    if segment[index] == "-C":
                        directory = (directory / segment[index + 1]).resolve()
                    index += 2
                if segment[index:index + 1] == ["commit"] and directory.is_relative_to(root):
                    repository = Path(git(directory, "rev-parse", "--show-toplevel")).resolve()
                    if repository.is_relative_to(root):
                        return repository
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def head(repository):
    try:
        return git(repository, "rev-parse", "--verify", "HEAD")
    except (OSError, ValueError):
        return None


def commit_record(repository, previous, result):
    current = head(repository)
    if not current or current == previous or not isinstance(result, str):
        return None
    # Git's commit summary confirms that the new HEAD was produced by this command.
    hashes = re.findall(r"^\[[^\n\]]+ ([0-9a-f]{7,64})\]", result, re.MULTILINE)
    if not any(current.startswith(value) for value in hashes):
        return None
    try:
        title = git(repository, "show", "-s", "--format=%s", current)
    except (OSError, ValueError):
        return None
    return {"kind": "commit", "action": "commit", "path": str(repository), "sha": current, "title": title}


def local_entry(backend, root, path, action, *, observed=False, previous=None):
    try:
        path = root / path
        if not path.is_relative_to(root) or path.resolve() != path:
            return None
        relative = path.relative_to(root)
        if not all(visible_name(name) for name in relative.parts):
            return None
        backend.check(str(path))
        kind = MEDIA.get(path.suffix.lower(), "file")
        if action not in {"delete", "create_directory"} and not path.is_file():
            return None
        info = root.stat()
    except (OSError, RuntimeError, ValueError):
        return None
    return {
        "path": str(relative), "kind": kind, "action": action, "observed": observed,
        "previous": previous, "root": str(root), "device": info.st_dev, "inode": info.st_ino,
        "mime": mimetypes.guess_type(path.name)[0] if kind != "file" else None,
    }


def local_media(sessions, entry):
    root = Path(entry["root"])
    path = root / entry["path"]
    try:
        info = root.stat()
        if (info.st_dev, info.st_ino) != (entry["device"], entry["inode"]):
            return None
        if path.resolve(strict=True) != path or not path.is_relative_to(root):
            return None
        if path.is_relative_to(sessions.database.parent) or any(
            path.is_relative_to(Path.home() / name) for name in (".ssh", ".aws")
        ):
            return None
        if MEDIA.get(path.suffix.lower()) != entry["kind"] or not path.is_file():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return str(path)


def media_outputs(result):
    if isinstance(result, ToolReturn):
        yield from media_outputs(result.return_value)
        yield from media_outputs(result.content)
    elif isinstance(result, (list, tuple)):
        for item in result:
            yield from media_outputs(item)
    elif isinstance(result, BinaryContent):
        yield result
    elif isinstance(result, dict):
        for key in ("path", "file_path", "uri", "artifacts", "files", "outputs"):
            if key in result:
                value = result[key]
                if key in {"path", "file_path"} and isinstance(value, str) and MEDIA.get(Path(value).suffix.lower()):
                    yield value
                else:
                    yield from media_outputs(value)
    elif isinstance(result, ResourceLink) or (isinstance(result, str) and result.startswith("file:")):
        try:
            uri = urlsplit(result.uri if isinstance(result, ResourceLink) else result)
        except ValueError:
            return
        if uri.scheme == "file" and uri.netloc in {"", "localhost"}:
            yield unquote(uri.path)


def save_inline(sessions, conversation_id, content):
    suffix = INLINE_MEDIA.get(content.media_type)
    if suffix is None or len(content.data) > 10 * 1024 * 1024:
        return None
    root = sessions.private_workspace(conversation_id) / ".delivery-media"
    root.mkdir(mode=0o700, exist_ok=True)
    if root.resolve() != root:
        return None
    digest = sha256(content.data).hexdigest()
    name = f"{digest}{suffix}"
    path = root / name
    if not path.is_file():
        with NamedTemporaryFile(dir=root, delete=False) as temporary:
            staged = Path(temporary.name)
            try:
                temporary.write(content.data)
                temporary.close()
                staged.replace(path)
            finally:
                staged.unlink(missing_ok=True)
    info = root.stat()
    return {"kind": MEDIA[suffix], "action": "generate", "path": name,
            "title": f"生成的{dict(image='图片', audio='音频', video='视频')[MEDIA[suffix]]} · {digest[:8]}",
            "root": str(root), "device": info.st_dev, "inode": info.st_ino, "mime": content.media_type}


class DeliveryCapture(AbstractCapability):
    def __init__(self, sessions):
        self.sessions = sessions

    async def wrap_run(self, ctx, *, handler):
        if TURN.get() is not None:
            return await handler()
        token = TURN.set((ctx.conversation_id, ctx.run_id, ctx.deps.title))
        try:
            return await handler()
        finally:
            TURN.reset(token)

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        turn = TURN.get()
        backend = ctx.workspace.backend
        if turn is None or not hasattr(backend, "policy") or ctx.workspace.read_only:
            return await handler(args)
        root = backend.root
        direct = call.tool_name in FILE_TOOLS and isinstance(args.get("path"), str)
        command = args.get("command", "")
        shell = call.tool_name in {"shell", "wait_command", "stop_command"}
        if call.tool_name == "shell" and git_access(command) == "read":
            return await handler(args)
        metadata = tool_def.metadata or {}
        mcp = "annotations" in metadata and not (metadata["annotations"] or {}).get("readOnlyHint")
        if not (direct or shell or mcp):
            return await handler(args)
        before = await asyncio.to_thread(safe_snapshot, root) if shell else None
        repository = await asyncio.to_thread(commit_directory, root, command) if call.tool_name == "shell" else None
        previous = await asyncio.to_thread(head, repository) if repository else None
        result = await handler(args)
        entries = []
        if direct:
            entry = local_entry(backend, root, args["path"], call.tool_name)
            if entry:
                entries.append(entry)
        elif shell:
            after = await asyncio.to_thread(safe_snapshot, root)
            for path, action, previous_path in changes(before, after):
                entry = local_entry(backend, root, path, action, observed=True, previous=previous_path)
                if entry:
                    entries.append(entry)
            if repository:
                entry = await asyncio.to_thread(commit_record, repository, previous, result)
                if entry:
                    entries.append(entry)
        else:
            seen = set()
            for output in media_outputs(result):
                if isinstance(output, BinaryContent):
                    try:
                        entry = await asyncio.to_thread(save_inline, self.sessions, turn[0], output)
                    except (OSError, RuntimeError, ValueError):
                        LOGGER.warning("Could not save delivery media", exc_info=True)
                        continue
                else:
                    entry = local_entry(backend, root, output, "generate")
                if entry and entry["kind"] in MEDIA.values() and entry["path"] not in seen:
                    seen.add(entry["path"])
                    entries.append(entry)
        if entries:
            try:
                await asyncio.to_thread(
                    self.sessions.save_delivery, *turn, ctx.run_id, call.tool_call_id,
                    datetime.now(UTC).isoformat(), entries,
                )
            except sqlite3.Error:
                # The original tool may already have changed files. Never retry it for a delivery failure.
                LOGGER.warning("Could not record delivery", exc_info=True)
        return result


def delivery_groups(sessions, conversation_id, history, runs):
    groups = {}
    captured = set()
    metadata = {run.run_id: run for run in runs}
    for run_id, source, call_id, title, timestamp, entries in sessions.deliveries(conversation_id):
        captured.add((source, call_id))
        group = groups.setdefault(run_id, {"title": title, "timestamp": timestamp, "items": []})
        for entry in entries:
            if entry["kind"] in MEDIA.values() and entry["action"] != "delete":
                entry["local_path"] = local_media(sessions, entry)
            group["items"].append(entry)
    calls = {}
    for message in history:
        for part in message.parts:
            if isinstance(part, ToolCallPart) and part.tool_name in FILE_TOOLS:
                calls[part.tool_call_id] = (message.run_id, part)
            elif isinstance(part, ToolReturnPart) and part.outcome == "success":
                pending = calls.pop(part.tool_call_id, None)
                if pending is None:
                    continue
                source, call = pending
                if (source, part.tool_call_id) in captured:
                    continue
                path = call.args_as_dict().get("path")
                if not isinstance(path, str):
                    continue
                group_id = source or message.run_id
                record = metadata.get(group_id)
                while record and record.parent_run_id and record.parent_run_id in metadata:
                    group_id = record.parent_run_id
                    record = metadata[group_id]
                group = groups.setdefault(group_id, {
                    "title": record.metadata.get("title") if record else None,
                    "timestamp": part.timestamp.isoformat(), "items": [],
                })
                group["items"].append({"path": path, "kind": "file", "action": call.tool_name})
    return sorted(groups.values(), key=lambda group: group["timestamp"], reverse=True)
