from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from integrations.network import NetworkDenied
from vespera_agent import AgentCore
from vespera_agent.bridge import Bridge

RESOURCES = Path(__file__).resolve().parents[1]


class RemoteModel(FunctionModel):
    @property
    def base_url(self):
        return "https://example.com/v1"


class RunStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_model_failure_has_native_identity_and_one_saved_prompt(self):
        with TemporaryDirectory() as directory:
            events = []
            def model(messages, info):
                self.fail("privacy rejection must precede model execution")
            core = AgentCore(Path(directory) / "data", RESOURCES, model=RemoteModel(model))
            cid = core.create()["conversation_id"]
            bridge = Bridge.__new__(Bridge)
            bridge.core, bridge.jobs, bridge.events = core, {}, {}
            try:
                receipt = await bridge._handle({"command": "send", "conversation_id": cid,
                                                "prompt": "password=private"})
                with self.assertRaisesRegex(NetworkDenied, "未通过外发检查"):
                    await bridge.jobs[cid]
                poll = bridge._poll(cid)
                self.assertTrue(poll["done"])
                events = poll["events"]
                starts = [event for event in events if event["type"] == "run_started"]
                self.assertEqual(len(starts), 1)
                self.assertEqual(starts[0]["run_id"], receipt["run_id"])
                self.assertEqual(events[0]["type"], "run_started")
                self.assertFalse(any(event.get("event", {}).get("event_kind") == "part_delta" for event in events))
                opened = await core.open(cid)
                prompts = [(message, part) for message in opened["history"] for part in message["parts"]
                           if part["part_kind"] == "user-prompt" and part["content"] == "password=private"]
                self.assertEqual(len(prompts), 1)
                self.assertEqual(prompts[0][0]["run_id"], starts[0]["run_id"])
            finally:
                await core.close(cid)

    async def test_large_text_prompt_still_receives_answer(self):
        with TemporaryDirectory() as directory:
            def model(messages, info):
                return ModelResponse(parts=[TextPart("ok")])
            async def stream(messages, info):
                yield "ok"
            core = AgentCore(Path(directory) / "data", RESOURCES,
                             model=RemoteModel(model, stream_function=stream))
            cid = core.create()["conversation_id"]
            events = []
            try:
                result = await core.send(cid, "ordinary " * 140_000, events.append)
                self.assertEqual(result["output"], "ok")
                self.assertEqual(events[0]["type"], "run_started")
                self.assertEqual(events[0]["run_id"], result["run_id"])
            finally:
                await core.close(cid)
