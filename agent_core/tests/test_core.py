import asyncio
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from vespera_agent import AgentCore


RESOURCES = Path(__file__).resolve().parents[3] / "coder-cli"


class AgentCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.requests = []

        def model(messages, info):
            self.requests.append((messages, info))
            part = next((part for part in messages[-1].parts if isinstance(part, ToolReturnPart)), None)
            return ModelResponse(parts=[TextPart(str(part.content))] if part else
                                 [ToolCallPart("read_file", {"path": "marker.txt"})])

        async def stream(messages, info):
            from pydantic_ai.models.function import DeltaToolCall
            response = model(messages, info)
            part = response.parts[0]
            if isinstance(part, ToolCallPart):
                yield {0: DeltaToolCall(name=part.tool_name, json_args=part.args_as_json_str())}
            else:
                yield part.content

        self.core = AgentCore(self.root / "data", RESOURCES, model=FunctionModel(model, stream_function=stream))
        self.conversation_id = (await self.core.open())["conversation_id"]
        self.addAsyncCleanup(self.core.close, self.conversation_id)

    async def test_unbound_rename_and_delete_keep_history(self):
        core, cid = self.core, self.conversation_id
        private = core.sessions.private_workspace(cid)
        (private / "marker.txt").write_text("private marker")
        events = []
        first = await core.send(cid, "read private", events.append)
        self.assertIn("private marker", first["output"])
        self.assertTrue(events)
        agent = core.runtime.agent
        old = self.root / "old"
        old.mkdir()
        (old / "marker.txt").write_text("project marker")
        await core.set_work_path(cid, str(old))
        new = self.root / "new"
        old.rename(new)
        moved = await core.send(cid, "read renamed project")
        self.assertIn("project marker", moved["output"])
        self.assertIn(str(new), self.requests[-1][1].instructions)
        self.assertEqual((await core.open(cid))["project_memory"][0]["reason"], "renamed")
        shutil.rmtree(new)
        opened = await core.set_work_path(cid, None, deleted=True)
        self.assertIsNone(opened["work_path"])
        self.assertEqual(opened["project_memory"][0]["reason"], "deleted")
        self.assertTrue(opened["history"])
        last = await core.send(cid, "read private again")
        self.assertIn("private marker", last["output"])
        self.assertIn('"reason": "deleted"', self.requests[-1][1].instructions)
        self.assertEqual({first["conversation_id"], moved["conversation_id"], last["conversation_id"]}, {cid})
        self.assertEqual(len({first["run_id"], moved["run_id"], last["run_id"]}), 3)
        self.assertIs(core.runtime.agent, agent)
        reopened = AgentCore(self.root / "data", RESOURCES)
        saved = await reopened.open(cid)
        self.assertIsNone(saved["work_path"])
        self.assertEqual(saved["project_memory"][0]["reason"], "deleted")
        self.assertTrue(saved["history"])

    async def test_replacement_does_not_inherit_binding(self):
        project = self.root / "project"
        project.mkdir()
        await self.core.set_work_path(self.conversation_id, str(project))
        project.rename(self.root / "moved")
        project.mkdir()
        state = await self.core.open(self.conversation_id)
        self.assertIsNone(state["work_path"])
        self.assertEqual(state["project_memory"][0]["reason"], "replaced")

    async def test_manual_change_and_external_removal(self):
        old, new = self.root / "old", self.root / "new"
        for path in (old, new):
            path.mkdir()
            (path / "marker.txt").write_text(path.name)
        core, cid = self.core, self.conversation_id
        await core.set_work_path(cid, str(old))
        await core.send(cid, "read old")
        changed = await core.set_work_path(cid, str(new))
        self.assertEqual(changed["project_memory"][0]["reason"], "changed")
        result = await core.send(cid, "read new")
        self.assertIn("new", result["output"])
        shutil.rmtree(new)
        state = await core.open(cid)
        self.assertIsNone(state["work_path"])
        self.assertEqual(state["project_memory"][0]["reason"], "unavailable")
        self.assertTrue(state["history"])
        reopened = await core.open(cid)
        self.assertEqual(reopened["project_memory"], state["project_memory"])


    async def test_switch_cancels_active_run_and_preserves_frontier(self):
        started = asyncio.Event()

        async def wait_for_cancel(messages, info):
            started.set()
            await asyncio.Future()
            yield "unreachable"

        core = AgentCore(self.root / "cancel-data", RESOURCES,
                         model=FunctionModel(stream_function=wait_for_cancel))
        cid = (await core.open())["conversation_id"]
        self.addAsyncCleanup(core.close, cid)
        task = asyncio.create_task(core.send(cid, "keep this interrupted request"))
        try:
            await asyncio.wait_for(started.wait(), 5)
            path = self.root / "next"
            path.mkdir()
            changed = await core.set_work_path(cid, str(path))
            self.assertEqual(changed["work_path"], str(path))
            self.assertIn("keep this interrupted request", str(changed["history"]))
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            await core.cancel(cid)

    async def test_list_empty_untitled_and_saved_conversation(self):
        from contextlib import closing
        from datetime import datetime
        import sqlite3

        from vespera_agent.sessions import ConversationNotFound

        core = AgentCore(self.root / "listing-data", RESOURCES, model=self.core.model)
        self.assertEqual(await core.list_conversations(), [])
        with self.assertRaises(ConversationNotFound):
            await core.open("missing")

        untitled = core.create()
        cid = untitled["conversation_id"]
        self.addAsyncCleanup(core.close, cid)
        rows = await core.list_conversations()
        self.assertEqual([row["conversation_id"] for row in rows], [cid])
        self.assertIsNone(rows[0]["title"])
        self.assertEqual(rows[0]["created_at"], untitled["updated_at"])
        self.assertEqual(rows[0]["updated_at"], rows[0]["created_at"])
        self.assertEqual((await core.open(cid))["history"], [])
        with closing(sqlite3.connect(core.sessions.database)) as db, db:
            db.execute("UPDATE vespera_sessions SET created_at = NULL WHERE conversation_id = ?", (cid,))
        legacy = (await core.list_conversations())[0]["created_at"]
        self.assertLess(abs(datetime.fromisoformat(legacy) - datetime.fromisoformat(untitled["updated_at"])).total_seconds(), 1)

        project = self.root / "listing-project"
        project.mkdir()
        (project / "marker.txt").write_text("marker")
        await core.set_work_path(cid, str(project))
        await core.send(cid, "  Read   the project marker  ")
        await core.send(cid, "Title should stay the same")
        other = core.create()["conversation_id"]
        rows = await core.list_conversations()
        self.assertEqual([row["conversation_id"] for row in rows], [other, cid])
        self.assertEqual(rows[1]["title"], "Read the project marker")
        self.assertEqual(rows[1]["work_path"], str(project))
        reopened = await core.open(cid)
        self.assertTrue(reopened["history"])
        self.assertEqual(reopened["title"], rows[1]["title"])

    async def test_create_with_optional_workspace_is_atomic(self):
        from contextlib import closing
        import sqlite3

        core = self.core
        project = self.root / "工作 项目"
        project.mkdir()
        unbound = core.create()
        self.assertIsNone(unbound["work_path"])
        bound = core.create(str(project))
        self.assertEqual(bound["work_path"], str(project))
        self.assertEqual(bound["project_memory"][0]["reason"], "bound")
        reopened = await core.open(bound["conversation_id"])
        self.assertEqual(reopened["project_memory"], bound["project_memory"])
        self.assertEqual(reopened["history"], [])

        with self.assertRaises(FileNotFoundError):
            core.create(str(project / "missing"))
        with closing(sqlite3.connect(core.sessions.database)) as db, db:
            db.execute("""CREATE TRIGGER reject_binding BEFORE INSERT ON vespera_project_memory
                          BEGIN SELECT RAISE(ABORT, 'binding write failed'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            core.create(str(project))
        self.assertEqual(len(await core.list_conversations()), 3)
