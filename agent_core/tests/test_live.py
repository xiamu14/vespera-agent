import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel, DeltaToolCall

from vespera_agent import AgentCore
from vespera_agent.attachments import load_attachments
from vespera_agent.core import first_sentence


RESOURCES = Path(__file__).resolve().parents[1]


class LiveTests(unittest.IsolatedAsyncioTestCase):
    def test_directory_expansion_is_bounded_and_stays_inside_project(self):
        from vespera_agent.inspector import directory_tree, list_directory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            deep = project / "first" / "second" / "third"
            deep.mkdir(parents=True)
            (deep / "file.txt").write_text("hidden contents")
            (project / "outside").symlink_to(root, target_is_directory=True)
            core = AgentCore(root / "data", RESOURCES)
            cid = core.create(str(project))["conversation_id"]
            entries, truncated = directory_tree(project)
            self.assertFalse(truncated)
            self.assertEqual([item["path"] for item in entries], ["", "first", "first/second"])
            children = list_directory(core, cid, "first/second")
            self.assertEqual(children, [{"name": "third", "path": "first/second/third",
                                         "folder": True, "depth": 3}])
            for path in ("..", "outside"):
                with self.assertRaises(ValueError):
                    list_directory(core, cid, path)
            for index in range(161):
                (deep / str(index)).touch()
            with self.assertRaisesRegex(ValueError, "budget"):
                list_directory(core, cid, "first/second/third")

    async def test_question_stream_and_inspector(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            question = {"header": "范围", "question": "选择范围", "multi_select": True,
                        "options": [{"label": "A"}, {"label": "B"}]}
            def model(messages, info):
                returned = any(isinstance(part, ToolReturnPart) and part.tool_name == "ask_user_question"
                               for message in messages for part in message.parts)
                return ModelResponse(parts=[TextPart("已收到回答")] if returned else [
                    ToolCallPart("ask_user_question", {"questions": [question]})])
            async def stream(messages, info):
                part = model(messages, info).parts[0]
                if isinstance(part, ToolCallPart):
                    yield {0: DeltaToolCall(name=part.tool_name, json_args=part.args_as_json_str())}
                else:
                    yield part.content
            core = AgentCore(root / "data", RESOURCES, model=FunctionModel(model, stream_function=stream))
            cid = core.create()["conversation_id"]
            events = []
            def emit(event):
                events.append(event)
                if event["type"] == "question":
                    self.assertTrue(core.answer_question(cid, event["question_id"], ["A", "B"], None))
            try:
                result = await asyncio.wait_for(core.send(cid, "整理笔记，后面再说。", emit), 10)
                self.assertEqual(result["output"], "已收到回答")
                self.assertEqual((await core.list_conversations())[0]["title"], "整理笔记")
                detail = await core.inspect(cid)
                self.assertEqual(detail["directory"], [])
                self.assertEqual(detail["timeline"][0]["text"], "整理笔记，后面再说。")
                self.assertIsNone(detail["today_cost"])
                self.assertTrue(any(event["type"] == "agent" for event in events))
                self.assertTrue(any(event.get("event", {}).get("event_kind") == "model_request_started"
                                    for event in events))
                opened = await core.open(cid)
                self.assertIn(result["run_id"], opened["run_seconds"])
            finally:
                await core.close(cid)

    def test_sentence_and_attachment_validation(self):
        for punctuation in (",", "，", "。", ".", "!", "？", ";", "；", "\n"):
            self.assertEqual(first_sentence(f"第一句{punctuation}后续"), "第一句")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "document.pdf"
            path.write_bytes(b"%PDF-1.4\n")
            self.assertEqual(load_attachments([str(path)])[0].media_type, "application/pdf")
            path.write_bytes(b"not an attachment")
            with self.assertRaises(ValueError):
                load_attachments([str(path)])


    async def test_inspector_reads_written_artifact_and_memory(self):
        from pydantic_ai_harness.memory import FileStore
        from pydantic_ai.workspaces import LocalWorkspaceBackend

        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            def model(messages, info):
                returned = any(isinstance(part, ToolReturnPart) and part.tool_name == "write_file"
                               for message in messages for part in message.parts)
                return ModelResponse(parts=[TextPart("saved")] if returned else [
                    ToolCallPart("write_file", {"path":"result.txt","content":"proof"})])
            async def stream(messages, info):
                part = model(messages, info).parts[0]
                if isinstance(part, ToolCallPart):
                    yield {0: DeltaToolCall(name=part.tool_name, json_args=part.args_as_json_str())}
                else:
                    yield part.content
            core = AgentCore(root / "data", RESOURCES, model=FunctionModel(model, stream_function=stream))
            cid = core.create(str(project))["conversation_id"]
            try:
                def emit(event):
                    if event["type"] == "question":
                        core.answer_question(cid, event["question_id"], ["本次允许"], None)
                await asyncio.wait_for(core.send(cid, "保存文件。后续", emit), 10)
                store = FileStore(".agent-memory", workspace=LocalWorkspaceBackend(project))
                await store.write("main/MEMORY.md", "project memory", expected_version=None)
                detail = await core.inspect(cid)
                self.assertEqual(detail["deliveries"][0]["items"][0]["path"], "result.txt")
                self.assertTrue(any(entry["name"] == "result.txt" for entry in detail["directory"]))
                self.assertIn("project memory", detail["project_memory"])
            finally:
                await core.close(cid)


    async def test_inspector_shows_configured_model_before_first_run(self):
        with TemporaryDirectory() as directory:
            model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("ok")]))
            core = AgentCore(Path(directory), RESOURCES, model=model)
            cid = core.create()["conversation_id"]
            detail = await core.inspect(cid)
            self.assertEqual(detail["model"], model.model_name)
            self.assertEqual(detail["context_used"], 0.0)
            self.assertEqual(detail["today_cost"], 0.0)
            self.assertEqual(detail["total_cost"], 0.0)
            self.assertIsNone(core._runtime)

    async def test_delivery_survives_reopen_with_shell_changes_commit_and_media(self):
        import subprocess

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            def git(*args):
                subprocess.run(["/usr/bin/git", "-C", str(project), *args], check=True, capture_output=True)
            git("init", "-q")
            git("config", "user.name", "Delivery Test")
            git("config", "user.email", "delivery@example.invalid")
            git("config", "commit.gpgsign", "false")
            (project / "notes.txt").write_text("original")
            git("add", "notes.txt")
            git("commit", "-qm", "baseline")
            (project / "notes.txt").write_text("preexisting dirty change")

            commands = [
                "printf updated > notes.txt && printf media > demo.wav",
                "git add notes.txt && git commit -m 'Update notes'",
            ]
            async def stream(messages, info):
                count = sum(isinstance(part, ToolReturnPart) and part.tool_name == "shell"
                            for message in messages for part in message.parts)
                if count < len(commands):
                    yield {0: DeltaToolCall(name="shell", json_args=json.dumps({"command": commands[count]}))}
                else:
                    yield "Done"
            model = FunctionModel(stream_function=stream)
            core = AgentCore(root / "data", RESOURCES, model=model)
            cid = core.create(str(project))["conversation_id"]
            def emit(event):
                if event["type"] == "question":
                    core.answer_question(cid, event["question_id"], ["本次允许"], None)
            try:
                result = await asyncio.wait_for(core.send(cid, "整理笔记并提交", emit), 25)
                self.assertEqual(result["output"], "Done")
            finally:
                await core.close(cid)
            reopened = AgentCore(root / "data", RESOURCES, model=model)
            groups = (await reopened.inspect(cid))["deliveries"]
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["title"], "整理笔记并提交")
            items = groups[0]["items"]
            self.assertTrue(any(item["path"] == "notes.txt" and item["action"] == "edit" and item["observed"]
                                for item in items), items)
            self.assertTrue(any(item["kind"] == "commit" and item["title"] == "Update notes"
                                for item in items), items)
            media = next(item for item in items if item["kind"] == "audio")
            self.assertEqual(media["local_path"], str(project / "demo.wav"))
            (project / "demo.wav").unlink()
            (project / "demo.wav").symlink_to(root / "external.wav")
            media = next(item for item in (await reopened.inspect(cid))["deliveries"][0]["items"]
                         if item["kind"] == "audio")
            self.assertIsNone(media["local_path"])

    def test_delivery_changes_include_rename_and_delete_without_following_symlinks(self):
        from vespera_agent.delivery import changes, snapshot

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rename.txt").write_text("move me")
            (root / "remove.txt").write_text("remove me")
            before = snapshot(root)
            (root / "rename.txt").rename(root / "renamed.txt")
            (root / "remove.txt").unlink()
            (root / "link.txt").symlink_to(root / "renamed.txt")
            self.assertEqual(list(changes(before, snapshot(root))), [
                ("renamed.txt", "rename", "rename.txt"), ("remove.txt", "delete", None),
            ])

    def test_delivery_accepts_local_mcp_resources_and_retains_legacy_writes(self):
        from pydantic_ai.mcp import ResourceLink
        from pydantic_ai.messages import ModelRequest
        from vespera_agent.delivery import delivery_groups, media_outputs

        outputs = {"outputs": [
            ResourceLink(name="image", uri="file:///tmp/cover%20image.png"),
            {"file_path": "demo.mp4"}, "https://example.invalid/remote.wav",
        ]}
        self.assertEqual(list(media_outputs(outputs)), ["/tmp/cover image.png", "demo.mp4"])
        with TemporaryDirectory() as directory:
            core = AgentCore(Path(directory), RESOURCES)
            cid = core.create()["conversation_id"]
            call = ToolCallPart("write_file", {"path": "old.txt"}, tool_call_id="write")
            result = ToolReturnPart("write_file", "done", tool_call_id="write")
            history = [ModelResponse(parts=[call], run_id="run"), ModelRequest(parts=[result], run_id="run")]
            groups = delivery_groups(core.sessions, cid, history, [])
            self.assertEqual(groups[0]["items"], [
                {"path": "old.txt", "kind": "file", "action": "write_file"},
            ])
