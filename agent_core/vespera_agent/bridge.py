"""Transport for an in-process host. All core operations share one asyncio loop."""

import asyncio
import json
from pathlib import Path
import sqlite3
import httpx
import httpx2
from threading import Thread

from dotenv import load_dotenv
from pydantic_ai.exceptions import AgentRunError, UserError

from agent.conversation import error_message
from storage.execution_lock import ExecutionBusyError

from .core import AgentCore, CoreBusyError
from .sessions import ConversationNotFound
from .inspector import list_directory


EXPECTED_ERRORS = (
    ValueError, OSError, sqlite3.Error, AgentRunError, UserError, ExecutionBusyError, CoreBusyError,
    httpx.HTTPError, httpx2.HTTPError,
)


class Bridge:
    def __init__(self, data, resources):
        load_dotenv(Path(data) / ".env")
        self.core = AgentCore(Path(data), Path(resources))
        self.jobs = {}
        self.events = {}
        self.loop = asyncio.new_event_loop()
        Thread(target=self.loop.run_forever, name="vespera-agent", daemon=True).start()

    def request(self, payload):
        try:
            request = json.loads(payload)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": error_message(exc)})
        result = asyncio.run_coroutine_threadsafe(self._request(request), self.loop)
        return json.dumps(result.result(), ensure_ascii=False)

    async def _request(self, request):
        try:
            if not isinstance(request, dict):
                raise ValueError("Request must be an object")
            return {"ok": await self._handle(request)}
        except ConversationNotFound as exc:
            return {"error": error_message(exc), "code": "not_found"}
        except EXPECTED_ERRORS as exc:
            return {"error": error_message(exc)}

    async def _handle(self, request):
        command = request.get("command")
        conversation_id = request.get("conversation_id")
        if command == "list":
            return await self.core.list_conversations()
        if command == "create":
            path = request.get("work_path")
            if path is not None and not isinstance(path, str):
                raise ValueError("work_path must be a string or null")
            return self.core.create(path)
        if command == "open" and conversation_id is None:
            return self.core.create()
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("conversation_id is required")
        if command == "open":
            return await self.core.open(conversation_id)
        if command == "inspect":
            return await self.core.inspect(conversation_id)
        if command == "directory":
            return await asyncio.to_thread(list_directory, self.core, conversation_id, self._text(request, "path"))
        if command == "set_work_path":
            path = request.get("work_path")
            deleted = request.get("deleted", False)
            if "work_path" not in request or path is not None and not isinstance(path, str):
                raise ValueError("work_path must be a string or null")
            if not isinstance(deleted, bool):
                raise ValueError("deleted must be a boolean")
            return await self.core.set_work_path(conversation_id, path, deleted=deleted)
        if command == "cancel":
            await self.core.close(conversation_id)
            return None
        if command == "close":
            await self.core.close(conversation_id)
            self.jobs.pop(conversation_id, None)
            self.events.pop(conversation_id, None)
            return None
        if command == "answer":
            if "question_id" not in request:
                return self.core.answer(conversation_id, self._text(request, "text"))
            question_id, selected, text = request["question_id"], request.get("selected", []), request.get("text")
            if (type(question_id) is not int or not isinstance(selected, list)
                    or not all(isinstance(item, str) for item in selected)
                    or text is not None and not isinstance(text, str)):
                raise ValueError("Invalid question answer")
            return self.core.answer_question(conversation_id, question_id, selected, text)
        if command == "send":
            if conversation_id in self.jobs:
                raise ValueError("Poll the previous response before sending again")
            prompt, paths = request.get("prompt"), request.get("paths", [])
            if not isinstance(prompt, str) or not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise ValueError("Invalid message or attachment paths")
            if not prompt.strip() and not paths:
                raise ValueError("Message cannot be empty")
            events = self.events.setdefault(conversation_id, [])
            self.jobs[conversation_id] = asyncio.create_task(
                self.core.send(conversation_id, prompt, events.append, paths=paths),
            )
            return None
        if command == "poll":
            return self._poll(conversation_id)
        raise ValueError("Unknown command")

    def _poll(self, conversation_id):
        if conversation_id not in self.events:
            raise ValueError("No response to poll")
        task = self.jobs.get(conversation_id)
        events = self.events[conversation_id]
        result = {"events": events.copy(), "done": task is None or task.done()}
        events.clear()
        if result["done"] and self.core.has_background_work(conversation_id):
            result["done"] = False
        if task is not None and task.done() and result["done"]:
            del self.jobs[conversation_id]
            if task.cancelled():
                result["cancelled"] = True
            else:
                try:
                    result["result"] = task.result()
                except EXPECTED_ERRORS as exc:
                    result["error"] = error_message(exc)
        return result

    @staticmethod
    def _text(request, key):
        value = request.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be nonempty text")
        return value
