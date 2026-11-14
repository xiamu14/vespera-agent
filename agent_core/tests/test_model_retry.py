import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.function import FunctionModel

from vespera_agent import AgentCore
from vespera_agent.bridge import Bridge

RESOURCES = Path(__file__).resolve().parents[1]


class ModelRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_failed_request_then_send_new_message(self):
        requests = []

        async def stream(messages, info):
            requests.append(ModelMessagesTypeAdapter.dump_json(messages))
            if len(requests) == 1:
                raise ModelHTTPError(403, "original-model", body={})
            yield "已完成"

        with TemporaryDirectory() as directory:
            core = AgentCore(Path(directory), RESOURCES, model=FunctionModel(stream_function=stream))
            cid = core.create()["conversation_id"]
            bridge = Bridge.__new__(Bridge)
            bridge.core, bridge.jobs, bridge.events = core, {}, {}
            failure = asyncio.Event()

            class Events(list):
                def append(self, event):
                    super().append(event)
                    if event["type"] == "model_failure":
                        failure.set()

            bridge.events[cid] = Events()
            try:
                sent = await bridge._handle({"command": "send", "conversation_id": cid, "prompt": "失败的消息"})
                await asyncio.wait_for(failure.wait(), 5)
                await bridge._handle({"command": "cancel", "conversation_id": cid})
                poll = bridge._poll(cid)
                self.assertTrue(poll["done"])
                self.assertTrue(poll["cancelled"])
                history = (await core.open(cid))["history"]
                self.assertTrue(any(message["run_id"] == sent["run_id"] for message in history))

                await bridge._handle({"command": "send", "conversation_id": cid, "prompt": "新消息"})
                await asyncio.wait_for(bridge.jobs[cid], 5)
                self.assertEqual(bridge._poll(cid)["result"]["output"], "已完成")
                self.assertIn("新消息".encode(), requests[-1])
            finally:
                await core.close(cid)

    async def test_manual_retry_preserves_model_request_and_run(self):
        requests = []

        async def stream(messages, info):
            requests.append(ModelMessagesTypeAdapter.dump_json(messages))
            if len(requests) == 1:
                raise ModelHTTPError(403, "original-model", body={"detail": "private-canary"})
            yield "已完成"

        with TemporaryDirectory() as directory:
            core = AgentCore(Path(directory), RESOURCES, model=FunctionModel(
                stream_function=stream, model_name="original-model",
            ))
            cid = core.create()["conversation_id"]
            failure = asyncio.Future()
            events = []

            def emit(event):
                events.append(event)
                if event["type"] == "model_failure":
                    failure.set_result(event)

            task = asyncio.create_task(core.send(cid, "继续", emit))
            try:
                error = await asyncio.wait_for(failure, 5)
                self.assertIn("403", error["message"])
                self.assertNotIn("private-canary", error["message"])
                self.assertEqual(len(requests), 1)
                self.assertFalse(core.retry_model(cid, "stale-token"))
                self.assertTrue(core.retry_model(cid, error["token"]))
                self.assertFalse(core.retry_model(cid, error["token"]))
                result = await asyncio.wait_for(task, 5)
                self.assertEqual(result["output"], "已完成")
                self.assertEqual(requests, [requests[0], requests[0]])
                self.assertEqual([event["run_id"] for event in events if event["type"] == "run_started"],
                                 [result["run_id"]])
                history = (await core.open(cid))["history"]
                self.assertEqual(sum(part["content"] == "继续" for message in history for part in message["parts"]
                                     if part["part_kind"] == "user-prompt"), 1)
            finally:
                await core.close(cid)
                await asyncio.gather(task, return_exceptions=True)
