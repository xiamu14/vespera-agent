"""Read-only projection of Agent Core state for clients."""

import asyncio
from datetime import datetime
from itertools import islice

from pydantic_ai.messages import ModelResponse, UserPromptPart
from pydantic_ai.workspaces import LocalWorkspaceBackend
from pydantic_ai_harness.memory import FileStore
from pydantic_ai_harness.step_persistence import SqliteStepStore

from context.sources import visible_name
from agent.roles import ROLES
from integrations.provider import load_provider_config
from storage.sessions import load_session_history
from .config import daily_budget_usd
from .delivery import delivery_groups


def user_messages(history):
    for message in history:
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                content = part.content
                text = content if isinstance(content, str) else " ".join(
                    item for item in content if isinstance(item, str)
                )
                if text.strip():
                    yield part.timestamp, text


def directory_tree(root):
    entries = [{"name": root.name, "path": "", "depth": 0, "folder": True}]
    truncated = False

    def visit(directory, depth):
        nonlocal truncated
        try:
            paths = list(islice(directory.iterdir(), 161))
        except (FileNotFoundError, NotADirectoryError):
            return
        if len(paths) > 160:
            truncated = True
        visible = [path for path in paths[:160] if visible_name(path.name) and not path.is_symlink()]
        for path in sorted(visible, key=lambda path: (not path.is_dir(), path.name.casefold())):
            if len(entries) >= 100:
                truncated = True
                return
            folder = path.is_dir()
            entries.append({"name": path.name, "path": str(path.relative_to(root)), "depth": depth, "folder": folder})
            if folder and depth < 2:
                visit(path, depth + 1)

    visit(root, 1)
    return entries, truncated


def configured_model(core):
    if core.model is not None:
        return core.model.model_name, core.model.context_window
    config = load_provider_config()
    name = config.model_for_tier(ROLES["super"].model_tier)
    model = config.models[name]
    return model.model_name or name, model.context_window


def context_used(response, run, fallback_limit=None):
    if response is None:
        return 0.0
    limit = run.metadata.get("context_window", "") if run else ""
    limit = int(limit) if limit.isdigit() else fallback_limit
    if not limit or limit <= 0 or response.usage.unmeasured_requests:
        return None
    usage = response.usage
    tokens = usage.input_tokens + usage.output_tokens
    return tokens / limit if tokens else None


async def usage_cost(store, runs, date=None):
    total = 0
    priced = False
    active_today = False
    for run in runs:
        if date is not None and run.started_at.astimezone().date() < date:
            continue
        active_today = True
        snapshot = await store.latest_snapshot(run_id=run.run_id, include_interrupted=True)
        if snapshot is None:
            continue
        for response in snapshot.messages:
            if not isinstance(response, ModelResponse) or response.run_id != run.run_id:
                continue
            if date is not None and response.timestamp.astimezone().date() != date:
                continue
            if not response.model_name:
                continue
            try:
                total += response.cost().total_price
            except (ValueError, LookupError):
                continue
            priced = True
    return float(total) if priced else None if active_today else 0.0


async def memory_text(root):
    if not root.is_dir() or root.resolve() != root:
        return ""
    if not (root / ".agent-memory" / "main" / "MEMORY.md").resolve().is_relative_to(root):
        return ""
    store = FileStore(".agent-memory", workspace=LocalWorkspaceBackend(root))
    note = await store.read("main/MEMORY.md", max_chars=8192)
    return note.content if note else ""


async def inspection(core, conversation_id):
    sessions = core.sessions
    binding = sessions.binding(conversation_id)
    work_path = binding.path if binding else None
    if binding:
        try:
            if not binding.matches(work_path.lstat()):
                work_path = None
        except (FileNotFoundError, NotADirectoryError):
            work_path = None
    history = await load_session_history(sessions.database, conversation_id)
    store = SqliteStepStore(database=str(sessions.database))
    runs = await store.list_runs()
    model, limit = configured_model(core)
    latest = next((part for part in reversed(history) if isinstance(part, ModelResponse)), None)
    run = next((run for run in reversed(runs) if latest and run.run_id == latest.run_id), None)
    tree, truncated = await asyncio.to_thread(directory_tree, work_path) if work_path else ([], False)
    root = work_path or sessions.private_workspace(conversation_id, create=False)
    memory = await memory_text(root)
    artifact_history = list(history)
    children = {}
    roots = []
    for record in runs:
        if record.parent_run_id:
            children.setdefault(record.parent_run_id, []).append(record.run_id)
        elif record.conversation_id == conversation_id:
            roots.append(record.run_id)
    pending = [child for root_id in roots for child in children.get(root_id, ())]
    seen = set(roots)
    while pending:
        child = pending.pop()
        if child in seen:
            continue
        seen.add(child)
        pending.extend(children.get(child, ()))
        snapshot = await store.latest_snapshot(run_id=child, include_interrupted=True)
        if snapshot:
            artifact_history.extend(message for message in snapshot.messages if message.run_id == child)
    return {
        "timeline": [{"time": time.isoformat(), "text": text} for time, text in user_messages(history)],
        "directory": tree, "directory_truncated": truncated,
        "model": latest.model_name if latest and latest.model_name else model,
        "context_used": context_used(latest, run, limit if latest is None or latest.model_name == model else None),
        "today_cost": await usage_cost(store, runs, datetime.now().astimezone().date()),
        "total_cost": await usage_cost(store, runs),
        "daily_budget_usd": daily_budget_usd(),
        "deliveries": await asyncio.to_thread(delivery_groups, sessions, conversation_id, artifact_history, runs),
        "user_memory": memory if work_path is None else "",
        "project_memory": memory if work_path else "",
        "binding_history": sessions.memory(conversation_id),
    }


def list_directory(core, conversation_id, relative):
    binding = core.sessions.binding(conversation_id)
    if binding is None or not binding.matches(binding.path.lstat()):
        raise ValueError("Project directory is unavailable")
    root = binding.path.resolve(strict=True)
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("Directory is outside the project")
    candidates = list(islice(path.iterdir(), 161))
    if len(candidates) > 160:
        raise ValueError("Directory exceeds the 160-entry budget")
    depth = len(path.relative_to(root).parts) + 1
    return [{"name": item.name, "path": str(item.relative_to(root)), "folder": item.is_dir(), "depth": depth}
            for item in sorted(candidates, key=lambda item: (not item.is_dir(), item.name.casefold()))
            if visible_name(item.name) and not item.is_symlink()]
