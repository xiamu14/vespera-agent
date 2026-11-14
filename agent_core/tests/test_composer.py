import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.function import FunctionModel, DeltaToolCall

from vespera_agent import AgentCore


RESOURCES = Path(__file__).resolve().parents[1]


class ComposerTests(unittest.IsolatedAsyncioTestCase):
    async def test_choices_and_explicit_role_report(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            (project / "notes.txt").write_text("sample notes")
            (project / "outside.txt").symlink_to(root / "external.txt")
            async def stream(messages, info):
                data = ModelMessagesTypeAdapter.dump_json(messages).decode()
                if "child summary" in data:
                    self.fail("Role result must be displayed without another model request")
                elif "sample notes" in data:
                    yield "child summary"
                else:
                    yield {0: DeltaToolCall(name="read_file", json_args='{"path":"notes.txt"}')}
            core = AgentCore(root / "data", RESOURCES, model=FunctionModel(stream_function=stream))
            cid = core.create(str(project))["conversation_id"]
            choices = await core.composer_choices(cid)
            self.assertEqual({row["name"] for row in choices["roles"]}, {"architect", "executor", "reviewer"})
            self.assertEqual(await core.composer_choices(cid, ".txt"), [])
            events = []
            try:
                result = await asyncio.wait_for(core.send(cid, "Read /reviewer the notes", events.append), 12)
                self.assertEqual(result["output"], "child summary")
                updates = [event["task"] for event in events if event["type"] == "delegation"]
                self.assertTrue(any(item["status"] == "finished" for item in updates), updates)
                self.assertTrue(any(item.get("tool", {}).get("name") == "read_file" for item in updates), updates)
                self.assertIn("child summary", str((await core.open(cid))["history"]))
            finally:
                await core.close(cid)

    async def test_selected_skill_is_rendered_with_arguments(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            skill = root / ".agents" / "skills" / "test-desktop"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: test-desktop\ndescription: Exercise the desktop selector.\n---\n"
                "Handle this input: $ARGUMENTS\n"
            )
            from unittest.mock import patch

            async def stream(messages, info):
                self.assertIn("Handle this input: sample", ModelMessagesTypeAdapter.dump_json(messages).decode())
                yield "Skill response"

            with patch("pathlib.Path.home", return_value=root):
                core = AgentCore(root / "data", RESOURCES, model=FunctionModel(stream_function=stream))
                cid = core.create()["conversation_id"]
                try:
                    choices = await core.composer_choices(cid)
                    self.assertIn("test-desktop", [skill["name"] for skill in choices["skills"]])
                    result = await asyncio.wait_for(core.send(cid, "sample /test-desktop"), 12)
                    self.assertEqual(result["output"], "Skill response")
                finally:
                    await core.close(cid)

    async def test_explicit_and_model_executor_handoffs_run_without_reconfirmation(self):
        import json
        from pydantic_ai.messages import UserPromptPart

        async def stream(messages, info):
            data = ModelMessagesTypeAdapter.dump_json(messages).decode()
            if "executor result" in data or "tool-return" in data:
                yield "Super summary"
            elif any(tool.name == "delegate_task" for tool in info.function_tools) and any(
                isinstance(part, UserPromptPart) and part.content == "请让 Executor 读取 notes.txt"
                for part in messages[-1].parts
            ):
                yield {0: DeltaToolCall(name="delegate_task", json_args=json.dumps({
                    "agent_name": "executor", "task": "读取 notes.txt", "background": False,
                }))}
            else:
                yield "executor result"

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            core = AgentCore(root / "data", RESOURCES, model=FunctionModel(stream_function=stream))
            for prompt in ("请 /executor 读取 notes.txt", "请让 Executor 读取 notes.txt"):
                with self.subTest(prompt=prompt):
                    cid = core.create()["conversation_id"]
                    events = []
                    def emit(event):
                        self.assertNotEqual(event["type"], "question", event)
                        events.append(event)
                    try:
                        await asyncio.wait_for(core.send(cid, prompt, emit), 12)
                        self.assertTrue(any(
                            event["type"] == "delegation" and event["task"]["role"] == "executor"
                            and event["task"]["status"] == "finished" for event in events
                        ), events)
                    finally:
                        await core.close(cid)

    async def test_background_task_result_is_delivered_without_model_summary(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            import subprocess
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "commit",
                            "--allow-empty", "--no-gpg-sign", "-qm", "baseline"], check=True)

            async def stream(messages, info):
                data = ModelMessagesTypeAdapter.dump_json(messages).decode()
                if "native reviewer result" in data:
                    self.fail("Background result must be displayed without another model request")
                elif "Review this example" in data and "Start a reviewer" not in data:
                    yield "native reviewer result"
                elif "running in the background" in data:
                    yield "Started reviewer"
                else:
                    yield {0: DeltaToolCall(name="delegate_task", json_args=(
                        '{"agent_name":"reviewer","task":"Review this example","background":true}'
                    ))}
            core = AgentCore(root / "data", RESOURCES, model=FunctionModel(stream_function=stream))
            cid = core.create(str(project))["conversation_id"]
            try:
                result = await asyncio.wait_for(core.send(cid, "Start a reviewer"), 20)
                self.assertEqual(result["output"], "native reviewer result")
                history = str((await core.open(cid))["history"])
                self.assertIn("Started reviewer", history)
                self.assertIn("native reviewer result", history)
            finally:
                await core.close(cid)

    async def test_at_reuses_only_current_history_and_escaped_triggers_are_literal(self):
        from pydantic_ai import BinaryContent
        from pydantic_ai.messages import UserPromptPart

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            (project / "unreferenced.txt").touch()
            notes = project / "notes.txt"
            notes.touch()
            upload = root / "uploaded.pdf"
            upload.write_bytes(b"%PDF-1.4\nexample")
            prompts = []

            async def stream(messages, info):
                prompts.append([item for part in messages[-1].parts if isinstance(part, UserPromptPart)
                                for item in ([part.content] if isinstance(part.content, str) else part.content)])
                yield "OK"

            model = FunctionModel(stream_function=stream)
            core = AgentCore(root / "data", RESOURCES, model=model)
            cid = core.create(str(project))["conversation_id"]
            other = core.create(str(project))["conversation_id"]
            try:
                await core.send(cid, f'［文件引用："{notes}"］', paths=[str(upload)])
                upload.unlink()
                self.assertEqual(await core.composer_choices(other, ""), [])
                choices = await core.composer_choices(cid, "")
                self.assertEqual([item["label"] for item in choices], ["uploaded.pdf", str(notes)])
                attachment = choices[0]["path"]
                await core.send(cid, rf'\/reviewer \@mention ［附件引用："{attachment}"］')
                self.assertTrue(any(isinstance(item, str) and item.startswith("/reviewer @mention")
                                    for item in prompts[-1]), prompts[-1])
                self.assertTrue(any(isinstance(item, BinaryContent) and item.data == b"%PDF-1.4\nexample"
                                    for item in prompts[-1]))
            finally:
                await core.close(cid)
