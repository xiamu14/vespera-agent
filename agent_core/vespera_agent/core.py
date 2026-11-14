import asyncio
from itertools import count
import re
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from pydantic import TypeAdapter
from pydantic_ai import ModelRetry, UsageLimits
from pydantic_ai.messages import (
    AgentStreamEvent, ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextContent, TextPart,
    ToolCallPart, UserPromptPart,
)
from pydantic_ai.workspaces import Workspace
from pydantic_ai_harness.ask_user import AskUserAnswer, AskUserRequest, AskUserResponse, check_response
from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord, SqliteStepStore

from agent.conversation import _super_history
from agent.interaction import Interaction
from agent.roles import ROLES, role_request
from agent.permissions.workspace import RestrictedBackend
from storage.execution_lock import session_lease
from storage.sessions import load_session_history, session_title

from .attachments import load_attachments
from .composer import literal_triggers, prompt_attachments, reference_choices, skill_catalog, slash_invocation
from .delivery import TURN
from .inspector import inspection, user_messages
from .model_failure import ModelRequestRetry
from .runtime import RunDeps, Runtime
from .sessions import Binding, Sessions
from .timing import active_seconds
from .task_status import task_event


STREAM_EVENT = TypeAdapter(AgentStreamEvent)


def no_events(event):
    pass


def first_sentence(text):
    return next((title for part in re.split(r"[，,。.!！？?；;、\n]+", text)
                 if (title := session_title(part))), None)


def history_title(history):
    return next((title for _, text in user_messages(history) if (title := first_sentence(text))), None)


class CoreBusyError(RuntimeError):
    pass


class StreamInteraction(Interaction):
    def __init__(self, emit):
        super().__init__()
        self.emit = emit

    async def before_run(self, ctx):
        await super().before_run(ctx)
        self.emit({"type": "agent", "run_id": ctx.run_id, "model": ctx.model.model_name,
                   "event": {"event_kind": "run_started"}})

    async def before_model_request(self, ctx, request_context):
        self.emit({"type": "agent", "run_id": ctx.run_id,
                   "event": {"event_kind": "model_request_started"}})
        return request_context


class AgentCore:
    def __init__(self, data: Path, resources: Path, *, model=None):
        self.sessions = Sessions(data)
        self.resources = resources
        self.model = model
        self._runtime = None
        self._services = {}
        self._running = {}
        self._closing = set()
        self._interactions = {}
        self._questions = {}
        self._model_retries = {}
        self._question_ids = count(1)

    @property
    def runtime(self):
        if self._runtime is None:
            self._runtime = Runtime(self.sessions, self.resources, self.model)
        return self._runtime

    def create(self, path: str | None = None):
        binding = self._validate_path(path)
        conversation_id, created_at, memory = self.sessions.create(binding)
        return {
            "conversation_id": conversation_id,
            "updated_at": created_at,
            "work_path": str(binding.path) if binding else None,
            "history": [],
            "project_memory": [memory] if memory else [],
        }

    async def list_conversations(self):
        rows = self.sessions.list_bindings()
        if not rows:
            return []
        database = self.sessions.database
        store = SqliteStepStore(database=str(database))
        activity = {}
        titles = {}
        registered = {conversation_id for conversation_id, _, _ in rows}
        for run in await store.list_runs():
            cid = run.conversation_id
            if cid not in registered or run.parent_run_id is not None:
                continue
            activity[cid] = max(activity.get(cid, run.started_at), run.started_at)
            titles.setdefault(cid, first_sentence(run.metadata.get("title", "")))

        summaries = []
        for cid, work_path, created_at in rows:
            if cid in activity and not titles[cid]:
                history = await load_session_history(database, cid)
                titles[cid] = history_title(history)
            summaries.append({
                "conversation_id": cid,
                "work_path": work_path,
                "title": titles.get(cid) or None,
                "created_at": created_at,
                "updated_at": activity[cid].isoformat() if cid in activity else created_at,
            })
        return sorted(summaries, key=lambda item: item["updated_at"] or "", reverse=True)

    async def open(self, conversation_id: str | None = None):
        if conversation_id is None:
            return self.create()
        if conversation_id not in self._running and conversation_id not in self._closing:
            with session_lease(self.sessions.database, conversation_id):
                await self._reconcile(conversation_id)
        binding = self.sessions.binding(conversation_id)
        history = await load_session_history(self.sessions.database, conversation_id)
        call_ids = {part.tool_call_id for message in history for part in message.parts
                    if isinstance(part, ToolCallPart)}
        approvals = await asyncio.to_thread(self.sessions.approvals, conversation_id, call_ids) if call_ids else {}
        store = SqliteStepStore(database=str(self.sessions.database))
        timings = {}
        for run in await store.list_runs(conversation_id=conversation_id):
            if run.parent_run_id is not None:
                continue
            events = await store.list_events(run_id=run.run_id)
            start = next((event.timestamp for event in events if event.kind == "model_request_started"), run.started_at)
            end = events[-1].timestamp if events else start
            timings[run.run_id] = active_seconds(start, end, await asyncio.to_thread(self.sessions.user_waits, run.run_id))
        return {
            "conversation_id": conversation_id,
            "work_path": str(binding.path) if binding else None,
            "title": history_title(history),
            "workspace": str(binding.path if binding else self.sessions.private_workspace(conversation_id, create=False)),
            "history": ModelMessagesTypeAdapter.dump_python(history, mode="json"),
            "approvals": approvals,
            "run_seconds": timings,
            "project_memory": self.sessions.memory(conversation_id),
        }

    async def inspect(self, conversation_id: str):
        return await inspection(self, conversation_id)

    def _validate_path(self, path):
        if path is None:
            return None
        if not path.strip():
            raise ValueError("Use null for an unbound conversation")
        return self.sessions.validate(Binding.from_path(Path(path)))

    async def set_work_path(self, conversation_id: str, path: str | None, *, deleted=False):
        if deleted and path is not None:
            raise ValueError("A deleted project cannot have a replacement path")
        new = self._validate_path(path)
        old = self.sessions.binding(conversation_id)
        if deleted and old is not None:
            try:
                old.path.lstat()
            except (FileNotFoundError, NotADirectoryError):
                pass
            else:
                raise ValueError("The bound path still exists")
        if old != new:
            await self._stop(conversation_id)
            with session_lease(self.sessions.database, conversation_id):
                current = self.sessions.binding(conversation_id)
                reason = "deleted" if deleted else "unbound" if new is None else "bound" if current is None else "changed"
                if current and new and (current.device, current.inode) == (new.device, new.inode):
                    reason = "renamed"
                self.sessions.change(conversation_id, current, new, reason)
        return await self.open(conversation_id)

    async def _close_services(self, conversation_id):
        services = self._services.get(conversation_id)
        if services is not None:
            await services.delegations.close()
            self._services.pop(conversation_id)

    async def _reconcile(self, conversation_id):
        old = self.sessions.binding(conversation_id)
        new, reason = self.sessions.resolve_binding(old)
        if reason is not None:
            await self._close_services(conversation_id)
            self.sessions.change(conversation_id, old, new, reason)
        return new

    async def _stop(self, conversation_id):
        if conversation_id in self._closing:
            raise CoreBusyError("Conversation is already changing its workspace")
        self._closing.add(conversation_id)
        try:
            task = self._running.get(conversation_id)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self._close_services(conversation_id)
        finally:
            self._closing.remove(conversation_id)

    async def cancel(self, conversation_id: str):
        task = self._running.get(conversation_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self, conversation_id: str):
        await self._stop(conversation_id)

    def retry_model(self, conversation_id, token):
        retry = self._model_retries.get(conversation_id)
        return retry is not None and retry.confirm(token)

    def answer(self, conversation_id: str, text: str):
        services = self._services.get(conversation_id)
        if services is not None and services.delegations.broker.waiting is not None:
            return services.delegations.broker.feedback(text)
        interaction = self._interactions.get(conversation_id)
        return interaction.feedback(text) if interaction is not None else False

    def has_background_work(self, conversation_id):
        services = self._services.get(conversation_id)
        owner = services.delegations.owner if services else None
        return owner is not None and any(task.status == "running" for task in owner.records.values())

    def answer_question(self, conversation_id, question_id, selected, text):
        pending = self._questions.get(conversation_id)
        if pending is None:
            return False
        token, question, future = pending
        if token != question_id or future.done():
            return False
        answer = AskUserAnswer(header=question.header, selected=tuple(selected), custom_answer=text)
        response = AskUserResponse(answers=(answer,))
        check_response(AskUserRequest(questions=(question,)), response)
        future.set_result(answer)
        return True

    async def composer_choices(self, conversation_id, query=None):
        binding = self.sessions.binding(conversation_id)
        if query is not None:
            history = await load_session_history(self.sessions.database, conversation_id)
            return await asyncio.to_thread(reference_choices, history, query)
        root = binding.path if binding else self.sessions.private_workspace(conversation_id)
        if binding and (not root.is_dir() or not binding.matches(root.lstat())):
            raise ValueError("项目目录不可用")
        roles = [{"name": name, "description": role.description}
                 for name, role in ROLES.items() if name != "super"]
        skills = await skill_catalog(self.runtime, root)
        return {"roles": roles, "skills": [{"name": name, "description": skill.description}
                                         for name, skill in sorted(skills.items())]}

    async def send(self, conversation_id: str, prompt: str, emit: Callable = no_events, *, paths=(), run_id=None):
        if not prompt.strip() and not paths:
            raise ValueError("Message cannot be empty")
        if conversation_id in self._running or conversation_id in self._closing:
            raise CoreBusyError("Conversation is busy")
        task = asyncio.create_task(self._send(conversation_id, prompt, emit, paths, run_id or uuid4().hex))
        self._running[conversation_id] = task
        try:
            return await task
        finally:
            self._running.pop(conversation_id, None)

    async def _send(self, conversation_id, prompt, emit, paths, run_id):
        uploads = await asyncio.to_thread(load_attachments, paths) if paths else []
        database = self.sessions.database
        with session_lease(database, conversation_id):
            binding = await self._reconcile(conversation_id)
            root = binding.path if binding else self.sessions.private_workspace(conversation_id)
            services = self._services.get(conversation_id)
            if services is not None and services.root != root:
                await self._close_services(conversation_id)
                services = None
            if services is None:
                services = self.runtime.workspace_services(root, conversation_id)
                self._services[conversation_id] = services

            history = await load_session_history(database, conversation_id)
            attachments = prompt_attachments(prompt, paths, uploads, history)
            text = literal_triggers(prompt)
            user_prompt = [*([text] if text.strip() else []), *attachments] if attachments else text
            role_prompt, skill, arguments = await slash_invocation(prompt, self.runtime, root)
            role = role_request(role_prompt) if role_prompt else None
            if role:
                role = (role[0], literal_triggers(role[1]))
            if role and attachments:
                raise ValueError("角色任务支持历史文件路径引用。图片或 PDF 请发给 Super。")
            if skill is not None:
                skill_text = skill.render(literal_triggers(arguments))
                user_prompt = [text, TextContent(skill_text, metadata={"origin": "ani-skill"}), *attachments]

            async def question(text):
                pending = services.delegations.broker.waiting or interaction.waiting
                if pending is None:
                    emit({"type": "question", "text": text})
                    return
                item, future = pending
                question_id = next(self._question_ids)
                self._questions[conversation_id] = (question_id, item, future)
                run_id = interaction.context.run_id if interaction.context else None
                if run_id is not None:
                    await asyncio.to_thread(self.sessions.user_wait, run_id, question_id)
                emit({"type": "question", "text": text, "question_id": question_id,
                      "question": item.model_dump(mode="json"),
                      "details": text.partition(f"\n\n**{item.header}**")[0]})
                try:
                    await asyncio.shield(future)
                finally:
                    if run_id is not None:
                        await asyncio.to_thread(self.sessions.user_wait, run_id, question_id, finished=True)
                    emit({"type": "user_wait_finished", "question_id": question_id})

            task_changed = asyncio.Event()

            async def observe(event):
                task_changed.set()
                emit({"type": "delegation", "task": task_event(event)})

            async def events(ctx, stream):
                async for event in stream:
                    emit({"type": "agent", "run_id": ctx.run_id,
                          "context_used": ctx.context_window_used, "model": ctx.model.model_name,
                          "event": STREAM_EVENT.dump_python(event, mode="json")})

            async def save_role_results(records, messages, *, run, step):
                outputs = [record.output.strip() for record in records if record.output.strip()]
                if records and not outputs:
                    outputs = ["角色任务已结束，未返回文本。"]
                await self.runtime.store.save_snapshot(ContinuableSnapshot(
                    conversation_id=conversation_id, run_id=run, step_index=step, messages=[
                        *messages, *(ModelResponse(parts=[TextPart(output)], run_id=run,
                                                   conversation_id=conversation_id) for output in outputs),
                    ],
                ))
                for record in records:
                    record.delivered = True
                    await services.delegations.owner.save(record)
                return "\n\n".join(outputs)

            interaction = StreamInteraction(emit)
            retry = ModelRequestRetry(emit)
            self._model_retries[conversation_id] = retry
            interaction.open(question)
            self._interactions[conversation_id] = interaction
            try:
                await services.delegations.open(self.runtime.store, observer=observe, on_question=question)
                async with AsyncExitStack() as stack:
                    backend = RestrictedBackend(root, services.permissions)
                    stack.callback(backend.close)
                    toolsets = await stack.enter_async_context(services.mcp.toolsets())
                    owner = services.delegations.owner
                    with owner.bind():
                        if role:
                            await self.runtime.store.register_run(RunRecord(
                                conversation_id=conversation_id, run_id=run_id,
                                metadata={"title": first_sentence(prompt) or "", "workspace": str(root)},
                            ))
                            messages = [*history, ModelRequest(
                                parts=[UserPromptPart(user_prompt)], conversation_id=conversation_id, run_id=run_id,
                            )]
                            await save_role_results([], messages, run=run_id, step=0)
                            token = TURN.set((conversation_id, run_id, first_sentence(prompt) or ""))
                            try:
                                previous = set(owner.records)
                                launcher = SimpleNamespace(agent=self.runtime.agent, permissions=services.permissions)
                                try:
                                    await services.delegations.start_role(launcher, *role)
                                except ModelRetry as exc:
                                    raise ValueError(str(exc)) from exc
                                started = [record for key, record in owner.records.items() if key not in previous]
                                while any(record.status == "running" for record in started):
                                    task_changed.clear()
                                    await task_changed.wait()
                                reports = [record for record in started if record.status == "finished"]
                                output = await save_role_results(reports, messages, run=run_id, step=1)
                                return {"conversation_id": conversation_id, "run_id": run_id, "output": output}
                            finally:
                                TURN.reset(token)

                        emit({"type": "run_started", "run_id": run_id})
                        result = await self.runtime.agent.run(
                            user_prompt, conversation_id=conversation_id, run_id=run_id,
                            message_history=_super_history(history), workspace=Workspace(backend),
                            toolsets=toolsets,
                            capabilities=[*services.capabilities, interaction, retry, services.delegations.reports()],
                            deps=RunDeps(services.project, self.sessions.memory(conversation_id), first_sentence(prompt) or ""),
                            event_stream_handler=events, usage_limits=UsageLimits(request_limit=50),
                        )

                        while self.has_background_work(conversation_id):
                            task_changed.clear()
                            await task_changed.wait()
                        pending_reports = owner.super_reports(conversation_id)
                        if pending_reports:
                            snapshot = await self.runtime.store.latest_snapshot(run_id=run_id)
                            output = await save_role_results(
                                pending_reports, result.all_messages(), run=run_id,
                                step=snapshot.step_index + 1,
                            )
                            return {"conversation_id": conversation_id, "run_id": run_id, "output": output}
            finally:
                interaction.close()
                self._interactions.pop(conversation_id, None)
                self._model_retries.pop(conversation_id, None)
                pending = self._questions.get(conversation_id)
                if pending and pending[2].done():
                    self._questions.pop(conversation_id, None)
            return {"conversation_id": result.conversation_id, "run_id": result.run_id, "output": result.output}
