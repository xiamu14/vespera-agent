"""Paths and read-only access to Harness-owned task records."""
from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter
from pydantic_ai_harness.subagents import DelegationTask


def delegation_directory(database: Path, conversation_id: str) -> Path:
    return database.parent / "delegations" / sha256(conversation_id.encode()).hexdigest()


def saved_tasks(database: Path, conversation_id: str) -> list[DelegationTask]:
    directory = delegation_directory(database, conversation_id)
    adapter = TypeAdapter(DelegationTask)
    records = []
    for path in directory.glob("*.json"):
        record = adapter.validate_json(path.read_bytes())
        if path.name != f"{record.id}.json":
            raise ValueError("原生委派记录的文件名与任务 ID 不一致")
        records.append(record)
    return records
