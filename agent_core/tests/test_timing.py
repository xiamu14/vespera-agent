from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from vespera_agent.sessions import Sessions
from vespera_agent.timing import active_seconds


class TimingTests(unittest.TestCase):
    def test_waits_are_clipped_merged_and_unfinished_wait_stays_paused(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        def stamp(seconds):
            return (start + timedelta(seconds=seconds)).isoformat()
        self.assertEqual(active_seconds(start, start + timedelta(seconds=30), []), 30)
        self.assertEqual(active_seconds(start, start + timedelta(seconds=30), [
            (stamp(5), stamp(15)), (stamp(10), stamp(20)), (stamp(25), None),
        ]), 10)
        self.assertEqual(active_seconds(start, start + timedelta(seconds=30), [
            (stamp(-10), stamp(5)), (stamp(25), stamp(40)),
        ]), 20)

    def test_user_wait_boundaries_survive_reopening_sessions(self):
        with TemporaryDirectory() as directory:
            sessions = Sessions(Path(directory))
            sessions.user_wait("run", 1)
            self.assertIsNone(sessions.user_waits("run")[0][1])
            sessions.user_wait("run", 1, finished=True)
            waits = Sessions(Path(directory)).user_waits("run")
            self.assertEqual(len(waits), 1)
            self.assertIsNotNone(waits[0][1])


class QuestionTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_resumes_run_and_persists_wait_boundaries(self):
        import asyncio
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
        from pydantic_ai.models.function import FunctionModel
        from vespera_agent import AgentCore

        calls = 0
        def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("ask_user_question", {"questions": [{
                    "header": "Q", "question": "Choose", "options": [{"label": "A"}, {"label": "B"}],
                }]})])
            return ModelResponse(parts=[TextPart("done")])

        async def stream(messages, info):
            from pydantic_ai.models.function import DeltaToolCall
            part = model(messages, info).parts[0]
            if isinstance(part, ToolCallPart):
                yield {0: DeltaToolCall(name=part.tool_name, json_args=part.args_as_json_str())}
            else:
                yield part.content

        with TemporaryDirectory() as directory:
            resources = Path(__file__).resolve().parents[1]
            core = AgentCore(Path(directory), resources, model=FunctionModel(model, stream_function=stream))
            cid = core.create()["conversation_id"]
            events = []
            def emit(event):
                events.append(event)
                if event["type"] == "question":
                    self.assertTrue(core.answer_question(cid, event["question_id"], ["A"], None))
            try:
                async with asyncio.timeout(15):
                    result = await core.send(cid, "choose", emit)
                self.assertTrue(any(event["type"] == "user_wait_finished" for event in events), events)
                waits = core.sessions.user_waits(result["run_id"])
                self.assertEqual(len(waits), 1)
                self.assertIsNotNone(waits[0][1])
                opened = await core.open(cid)
                self.assertIn(result["run_id"], opened["run_seconds"])
            finally:
                await core.close(cid)
