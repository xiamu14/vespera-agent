from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from itertools import islice
import json
from pathlib import Path
import sqlite3
import stat
from uuid import UUID

from pydantic_ai.conversation import Conversation


class ConversationNotFound(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    path: Path
    device: int
    inode: int

    @classmethod
    def from_path(cls, path: Path):
        path = path.expanduser().resolve(strict=True)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(str(path))
        return cls(path, info.st_dev, info.st_ino)

    def matches(self, info):
        return stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (self.device, self.inode)


class Sessions:
    def __init__(self, data: Path):
        self.data = data.resolve()
        self.database = self.data / "state" / "sessions.db"
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS vespera_sessions (
                conversation_id TEXT PRIMARY KEY, work_path TEXT, device INTEGER, inode INTEGER, created_at TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS vespera_user_waits (
                run_id TEXT NOT NULL, question_id INTEGER NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT,
                PRIMARY KEY (run_id, question_id)
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(vespera_sessions)")}
            if "created_at" not in columns:
                db.execute("ALTER TABLE vespera_sessions ADD COLUMN created_at TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS vespera_project_memory (
                conversation_id TEXT NOT NULL, old_path TEXT, new_path TEXT,
                reason TEXT NOT NULL, recorded_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS vespera_memory_conversation
                ON vespera_project_memory(conversation_id)""")

    def user_wait(self, run_id, question_id, *, finished=False):
        with closing(sqlite3.connect(self.database)) as db, db:
            now = datetime.now(UTC).isoformat()
            if finished:
                db.execute("UPDATE vespera_user_waits SET finished_at=? WHERE run_id=? AND question_id=?",
                           (now, run_id, question_id))
            else:
                db.execute("INSERT INTO vespera_user_waits VALUES (?, ?, ?, NULL)",
                           (run_id, question_id, now))

    def user_waits(self, run_id):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT started_at, finished_at FROM vespera_user_waits WHERE run_id=?",
                              (run_id,)).fetchall()

    def create(self, binding=None):
        conversation_id = Conversation().conversation_id
        created_at = datetime.now(UTC).isoformat()
        path = str(binding.path) if binding else None
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                """INSERT INTO vespera_sessions(conversation_id, created_at, work_path, device, inode)
                   VALUES (?, ?, ?, ?, ?)""",
                (conversation_id, created_at, path, binding.device if binding else None,
                 binding.inode if binding else None),
            )
            memory = self.record_binding(db, conversation_id, None, path, "bound", created_at) if binding else None
        return conversation_id, created_at, memory

    def list_bindings(self):
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute(
                "SELECT conversation_id, work_path, created_at FROM vespera_sessions ORDER BY rowid DESC",
            ).fetchall()
        return [(cid, path, created_at or self.creation_time_from_id(cid))
                for cid, path, created_at in rows]

    @staticmethod
    def creation_time_from_id(conversation_id):
        # Older rows lack created_at. Pydantic AI UUIDv7 IDs encode creation time in their first 48 bits.
        try:
            uid = UUID(conversation_id)
        except ValueError:
            return None
        if uid.version == 7:
            return datetime.fromtimestamp((uid.int >> 80) / 1000, UTC).isoformat()
        return None

    def binding(self, conversation_id):
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute(
                "SELECT work_path, device, inode FROM vespera_sessions WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise ConversationNotFound("Unknown conversation_id")
        path, device, inode = row
        return Binding(Path(path), device, inode) if path is not None else None

    def private_workspace(self, conversation_id, *, create=True):
        root = self.data / "workspaces" / sha256(conversation_id.encode()).hexdigest()
        if root.resolve() != root:
            raise ValueError("Private workspace cannot be a symbolic link")
        if create:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root

    def validate(self, binding):
        path = binding.path
        if self.data.is_relative_to(path) or path.is_relative_to(self.data):
            raise ValueError("Project cannot overlap the Agent Core data directory")
        return binding

    def change(self, conversation_id, old, new, reason):
        if old == new:
            return
        old_path = str(old.path) if old else None
        new_path = str(new.path) if new else None
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "UPDATE vespera_sessions SET work_path = ?, device = ?, inode = ? WHERE conversation_id = ?",
                (new_path, new.device if new else None, new.inode if new else None, conversation_id),
            )
            self.record_binding(db, conversation_id, old_path, new_path, reason, datetime.now(UTC).isoformat())

    @staticmethod
    def record_binding(db, conversation_id, old_path, new_path, reason, recorded_at):
        db.execute(
            "INSERT INTO vespera_project_memory VALUES (?, ?, ?, ?, ?)",
            (conversation_id, old_path, new_path, reason, recorded_at),
        )
        return {"old_path": old_path, "new_path": new_path, "reason": reason, "recorded_at": recorded_at}

    def memory(self, conversation_id):
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                """SELECT old_path, new_path, reason, recorded_at FROM vespera_project_memory
                   WHERE conversation_id = ? ORDER BY rowid DESC LIMIT 10""", (conversation_id,),
            )]

    def approvals(self, conversation_id, call_ids):
        recorded = {}
        try:
            with (self.database.parent / "permissions.jsonl").open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.endswith("\n"):
                        break  # The final record may still be getting appended.
                    entry = json.loads(line)
                    call_id = entry.get("tool_call_id")
                    if entry.get("conversation_id") != conversation_id or call_id not in call_ids:
                        continue
                    action = entry["action"]
                    if action == "ask":
                        recorded[call_id] = {"reason": entry["reason"], "action": action}
                    elif call_id in recorded and action in {"approved", "denied", "deny"}:
                        recorded[call_id]["action"] = action
        except FileNotFoundError:
            pass
        return recorded

    def resolve_binding(self, binding):
        if binding is None:
            return None, None
        try:
            current = binding.path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            current = None
        if current is not None and binding.matches(current):
            return binding, None
        if current is None:
            matches = self.renamed_candidates(binding)
            if len(matches) == 1:
                new = self.validate(Binding.from_path(matches[0]))
                if (new.device, new.inode) == (binding.device, binding.inode):
                    return new, "renamed"
        return None, "unavailable" if current is None else "replaced"

    @staticmethod
    def renamed_candidates(binding):
        # shortcut: only follow same-parent renames in small directories; use a host file identity resolver for broader moves.
        try:
            candidates = list(islice(binding.path.parent.iterdir(), 257))
        except (FileNotFoundError, NotADirectoryError):
            return []
        if len(candidates) > 256:
            return []
        matches = []
        for candidate in candidates:
            try:
                if binding.matches(candidate.lstat()):
                    matches.append(candidate)
            except FileNotFoundError:
                continue
        return matches
