import asyncio
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from pydantic_ai.messages import ModelMessage, UserPromptPart, repair_messages
from pydantic_ai_harness.conversation_search import SnapshotHistorySource
from pydantic_ai_harness.step_persistence import SqliteStepStore
from storage.delegations import saved_tasks, delegation_directory
from storage.execution_lock import delegation_lease

SESSION_DATABASE = Path(__file__).resolve().parents[1] / "workspace" / "sessions.db"


def session_database() -> Path:
    return SESSION_DATABASE


async def session_workspace(database: Path, session_id: str | None, path: str | None) -> Path:
    if session_id is not None:
        path = None
    saved = None
    if session_id and database.exists():
        registered = await asyncio.to_thread(_session_rows, database, session_id)
        if registered:
            value = registered[0][1]
            if not value or not Path(value).is_absolute():
                raise ValueError(f"会话 {session_id} 的工作目录记录无效")
            saved = Path(value).resolve()
        store = SqliteStepStore(database=str(database))
        runs = [] if saved is not None else [run for run in await store.list_runs(conversation_id=session_id) if run.parent_run_id is None]
        for run in reversed(runs):
            if "workspace" in run.metadata:
                value = run.metadata["workspace"]
                if not isinstance(value, str) or not value or not Path(value).is_absolute():
                    raise ValueError(f"会话 {session_id} 的工作目录记录无效")
                saved = Path(value).resolve()
                break
        if runs and saved is None:
            # 旧会话的工具一直在数据库所在的 workspace 目录执行。
            saved = database.parent.resolve()
    if path is not None and not path.strip():
        raise ValueError("--path 不能为空")
    requested = Path(path).expanduser().resolve() if path is not None else None
    workspace = saved or requested or Path.cwd().resolve()
    if not workspace.is_dir():
        raise ValueError(f"工作目录不存在或不是目录：{workspace}")
    return workspace


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    title: str
    workspace: str = ""


async def save_session(database: Path, session_id: str, workspace: Path) -> None:
    await asyncio.to_thread(_save_session, database, session_id, workspace)


def _save_session(database: Path, session_id: str, workspace: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS sessions (
                conversation_id TEXT PRIMARY KEY, workspace TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            connection.execute(
                "INSERT OR IGNORE INTO sessions (conversation_id, workspace, created_at) VALUES (?, ?, ?)",
                (session_id, str(workspace), datetime.now(UTC).isoformat()),
            )


def _session_rows(database: Path, session_id: str | None = None) -> list[tuple[str, str, str]]:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone():
            return []
        sql = "SELECT conversation_id, workspace, created_at FROM sessions"
        if session_id is None:
            return connection.execute(sql).fetchall()
        return connection.execute(sql + " WHERE conversation_id = ?", (session_id,)).fetchall()


def session_title(text: str) -> str:
    return " ".join("".join(c for c in text if c.isprintable() or c.isspace()).split())[:80]


async def list_sessions(database: Path) -> list[SessionInfo]:
    if not database.exists():
        return []
    store = SqliteStepStore(database=str(database))
    source = SnapshotHistorySource(store)
    registered = await asyncio.to_thread(_session_rows, database)
    registered_workspaces = {session_id: workspace for session_id, workspace, _ in registered}
    sessions = {session_id: SessionInfo(session_id, "", workspace) for session_id, workspace in registered_workspaces.items()}
    activity = {session_id: datetime.fromisoformat(created_at) for session_id, _, created_at in registered}
    for run in await store.list_runs():
        session_id = run.conversation_id
        if not session_id or run.parent_run_id is not None:
            continue
        if run.agent_name in {"reviewer", "architect", "executor"} and session_id not in registered_workspaces:
            continue
        title = session_title(run.metadata.get("title", ""))
        previous = sessions.get(session_id)
        workspace = registered_workspaces.get(
            session_id, run.metadata.get("workspace", previous.workspace if previous else str(database.parent.resolve()))
        )
        if not title and previous is not None:
            title = previous.title
        if not title:
            messages = await source.run_history(run_id=run.run_id)
            if not messages:
                snapshot = await store.latest_snapshot(run_id=run.run_id, include_interrupted=True)
                messages = snapshot.messages if snapshot else []
            for message in messages:
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                        title = session_title(part.content)
                        if title:
                            break
                if title:
                    break
        sessions[session_id] = SessionInfo(session_id, title, workspace)
        activity[session_id] = max(activity.get(session_id, run.started_at), run.started_at)
    ordered = sorted(sessions.values(), key=lambda session: activity[session.session_id], reverse=True)
    return [SessionInfo(s.session_id, s.title or "Untitled session", s.workspace) for s in ordered]


async def load_session_history(database: Path, session_id: str) -> list[ModelMessage]:
    if not database.exists():
        return []
    run_id = await asyncio.to_thread(_latest_history_run, database, session_id)
    if run_id is None:
        return []
    store = SqliteStepStore(database=str(database))
    snapshot = await store.latest_snapshot(run_id=run_id, include_interrupted=True)
    if snapshot is None:
        raise ValueError(f"会话 {session_id} 的运行缺少消息快照，无法恢复")
    return repair_messages(snapshot.messages)


def _latest_history_run(database: Path, session_id: str) -> str | None:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone():
            return None
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runs_top_conversation_started
            ON runs(conversation_id, started_at)
            WHERE parent_run_id IS NULL
            """
        )
        row = connection.execute(
            """
            SELECT r.run_id FROM runs AS r
            WHERE r.conversation_id = ? AND r.parent_run_id IS NULL
              AND (
                  EXISTS (SELECT 1 FROM snapshots AS s WHERE s.run_id = r.run_id)
                  OR EXISTS (
                      SELECT 1 FROM events AS e
                      WHERE e.run_id = r.run_id AND e.kind = 'run_completed'
                  )
              )
            ORDER BY r.started_at DESC, r.rowid DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    return row[0] if row is not None else None


async def delete_session(database: Path, session_id: str) -> None:
    if not database.exists():
        return
    with delegation_lease(database, session_id):
        await asyncio.to_thread(_delete_session, database, session_id)


def _delete_session(database: Path, session_id: str) -> None:
    delegates = saved_tasks(database, session_id)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "runs" in tables:
                conversation_ids = [session_id, *(record.id for record in delegates)]
                placeholders = ",".join("?" for _ in conversation_ids)
                connection.execute(
                    f"""
                    CREATE TEMP TABLE deleting_runs AS
                    WITH RECURSIVE selected(run_id) AS (
                        SELECT run_id FROM runs WHERE conversation_id IN ({placeholders})
                        UNION
                        SELECT r.run_id FROM runs AS r JOIN selected AS s ON r.parent_run_id = s.run_id
                    )
                    SELECT run_id FROM selected
                    """,
                    conversation_ids,
                )
                # Snapshot deletion triggers release message references and unshared message bodies.
                for table in ("events", "snapshots", "snapshot_idempotency_keys", "tool_effects", "runs"):
                    connection.execute(f"DELETE FROM {table} WHERE run_id IN (SELECT run_id FROM deleting_runs)")
            if "sessions" in tables:
                connection.execute("DELETE FROM sessions WHERE conversation_id = ?", (session_id,))
            if "plan_items" in tables:
                connection.execute("DELETE FROM plan_items WHERE session = ?", (session_id,))
                if "runs" in tables:
                    connection.execute("DELETE FROM plan_items WHERE session IN (SELECT run_id FROM deleting_runs)")
    directory = delegation_directory(database, session_id)
    for record in delegates:
        (directory / f"{record.id}.json").unlink(missing_ok=True)
