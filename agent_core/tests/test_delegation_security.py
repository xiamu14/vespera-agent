"""Delegation resource creation must follow native validation and authorization."""
import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.capabilities import CombinedCapability, HandleDeferredToolCalls
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import ToolDenied
from pydantic_ai.usage import RunUsage
from pydantic_ai.workspaces import LocalWorkspaceBackend, Workspace
from pydantic_ai_harness.subagents import DelegationTasks, SubAgent, SubAgents, TaskControl

from agent.delegation import DelegationPolicy, Delegations
from agent.interaction import Interaction
from agent.permissions.policy import ExecutionPermissions
from storage.worktrees import Worktree


BASELINE = "a" * 40


class DelegationSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = AsyncExitStack()
        await self.stack.__aenter__()
        self.addAsyncCleanup(self.stack.aclose)
        self.temp = self.stack.enter_context(TemporaryDirectory())
        self.root = Path(self.temp).resolve() / "project"
        self.root.mkdir()
        (self.root / ".git").mkdir()  # Marker only; Git is always mocked.
        self.config = Path(self.temp) / "permissions.json"
        self.settings("allow")
        self.stack.enter_context(patch("agent.permissions.policy.config_path", return_value=self.config))
        self.stack.enter_context(patch("agent.delegation.git", side_effect=self.git))
        self.backend_constructor = self.stack.enter_context(patch(
            "agent.delegation.RestrictedBackend",
            side_effect=lambda directory, policy: LocalWorkspaceBackend(directory),
        ))
        self.interaction = Interaction()
        self.policy = ExecutionPermissions(self.root, self.interaction, Path(self.temp) / "db.sqlite",
                                           Path(self.temp) / "mcp.json")
        self.runtime = Delegations(self.policy.database, self.root, "conversation")
        self.runtime.owner = DelegationTasks()
        await self.stack.enter_async_context(self.runtime.owner.opened())
        self.runtime.role_permissions = lambda directory, role: self.policy
        self.runtime.worktrees.allocate = AsyncMock(side_effect=self.allocate)
        self.runtime.worktrees.validate = unittest.mock.Mock(side_effect=self.validate)
        self.child = Agent(TestModel(custom_output_text="done", call_tools=[]), name="reviewer")
        self.runtime.agents = {"reviewer": SubAgent(self.child, name="reviewer")}
        self.agent = Agent(TestModel(custom_output_text="done", call_tools=[]), name="super")
        self.conversation = SimpleNamespace(agent=self.agent, permissions=self.policy)

    def settings(self, action):
        self.config.write_text(json.dumps({"rules": [{"tool": "delegate_task", "action": action}]}))

    def git(self, root, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return str(self.root)
        if args[:2] == ("rev-parse", "--verify"):
            return BASELINE
        raise AssertionError(f"Unexpected Git operation: {args}")

    async def allocate(self, key, revision):
        from hashlib import sha256
        self.assertEqual(revision, BASELINE)
        directory = self.runtime.worktrees.directory / sha256(key.encode()).hexdigest()
        directory.mkdir(parents=True, exist_ok=True)
        return Worktree(str(self.root), str(directory), revision)

    def validate(self, directory):
        return Worktree(str(self.root), directory, BASELINE)

    async def manager(self, *, reverse=False, role="super"):
        ctx = RunContext(deps=None, model=self.agent.model, usage=RunUsage(), messages=[],
                         conversation_id="conversation", run_id="parent-run", agent=self.agent,
                         workspace=Workspace(LocalWorkspaceBackend(self.root)),
                         usage_limits=UsageLimits(request_limit=10))
        delegation = DelegationPolicy(self.runtime, role)
        policies = [self.policy, delegation] if reverse else [delegation, self.policy]
        capability = CombinedCapability([*policies, HandleDeferredToolCalls(handler=self.policy.handle)])
        toolset = SubAgents(agents=list(self.runtime.agents.values()), include_self=True,
                            workspace_resolver=self.runtime.resolve_workspace,
                            task_control=TaskControl(max_running_tasks=4)).get_toolset()
        with self.runtime.owner.bind():
            return await ToolManager(toolset, root_capability=capability).for_run_step(ctx)

    async def call(self, *, reverse=False, args=None, approved=False):
        manager = await self.manager(reverse=reverse)
        call = ToolCallPart("delegate_task", args if args is not None else {
            "agent_name": "reviewer", "task": "review", "background": False,
        }, tool_call_id="call")
        with self.runtime.owner.bind():
            return await manager.handle_call(call, approved=approved, wrap_validation_errors=False)

    async def explicit(self):
        return await self.runtime.start_role(self.conversation, "reviewer", "review")

    async def test_deny_allocates_nothing_in_both_entry_points_and_hook_orders(self):
        self.settings("deny")
        for reverse in (False, True):
            await self.call(reverse=reverse)
        await self.explicit()
        self.runtime.worktrees.allocate.assert_not_awaited()
        self.backend_constructor.assert_not_called()
        self.assertEqual(self.runtime.owner.records, {})
        self.assertFalse(self.runtime.worktrees.directory.exists())

    async def test_allowed_allocates_once_in_both_entry_points_and_hook_orders(self):
        for entry in (self.call, lambda: self.call(reverse=True), self.explicit):
            with self.subTest(entry=entry):
                self.runtime.worktrees.allocate.reset_mock()
                await entry()
                self.runtime.worktrees.allocate.assert_awaited_once()
        self.assertTrue(all(record.working_directory for record in self.runtime.owner.records.values()))
        self.assertTrue(all(record.id != "call" for record in self.runtime.owner.records.values()))

    async def exercise_approval(self, entry, approve):
        self.settings("ask")
        waiting = asyncio.Event()
        answer = asyncio.get_running_loop().create_future()

        async def respond(details, **kwargs):
            waiting.set()
            return await answer

        self.interaction.approve = respond
        directory_existed = self.runtime.worktrees.directory.exists()
        backend_count = self.backend_constructor.call_count
        running = asyncio.create_task(entry())
        try:
            await asyncio.wait_for(waiting.wait(), timeout=3)
            self.runtime.worktrees.allocate.assert_not_awaited()
            self.assertEqual(self.runtime.owner.records, {})
            self.assertEqual(self.runtime.worktrees.directory.exists(), directory_existed)
            self.assertEqual(self.backend_constructor.call_count, backend_count)
            answer.set_result((approve, False))
            if not approve and entry == self.explicit:
                with self.assertRaises(ValueError):
                    await running
            else:
                result = await running
                if not approve:
                    self.assertIsInstance(result, ToolDenied)
        finally:
            if not running.done():
                running.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await running

    async def test_waiting_and_rejected_allocate_nothing(self):
        for entry in (self.call, lambda: self.call(reverse=True), self.explicit):
            await self.exercise_approval(entry, False)
            self.runtime.worktrees.allocate.assert_not_awaited()

    async def test_cancelled_approval_allocates_nothing(self):
        self.settings("ask")
        for entry in (self.call, self.explicit):
            waiting = asyncio.Event()
            answer = asyncio.get_running_loop().create_future()

            async def respond(details, **kwargs):
                waiting.set()
                return await answer

            self.interaction.approve = respond
            running = asyncio.create_task(entry())
            try:
                await asyncio.wait_for(waiting.wait(), timeout=3)
            finally:
                running.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await running
            self.runtime.worktrees.allocate.assert_not_awaited()
            self.backend_constructor.assert_not_called()
            self.assertFalse(self.runtime.worktrees.directory.exists())
            self.assertEqual(self.runtime.workspace_plans, {})

    async def test_approved_allocates_once(self):
        for entry in (self.call, lambda: self.call(reverse=True), self.explicit):
            self.runtime.worktrees.allocate.reset_mock()
            self.runtime.owner.records.clear()
            await self.exercise_approval(entry, True)
            self.runtime.worktrees.allocate.assert_awaited_once()
            await asyncio.gather(*self.runtime.owner._workers.values())

    async def test_invalid_arguments_allocate_nothing(self):
        for args in (
            {"agent_name": "reviewer", "task": ""},
            {"agent_name": "reviewer", "task": "review", "background": "false"},
            {"agent_name": "reviewer", "task": "review", "working_directory": 4},
            {"agent_name": "reviewer", "task": "review", "working_directory": "/tmp/other"},
            {"agent_name": "reviewer", "task": "review", "model": "unknown"},
            {"agent_name": "unknown", "task": "review"},
            {"agent_name": "reviewer", "task": "review", "resume": "unknown"},
            {"agent_name": "reviewer", "task": "review", "unexpected": True},
        ):
            with self.subTest(args=args), self.assertRaises((ModelRetry, ValidationError)):
                await self.call(args=args)
        for role, task in (("unknown", "review"), ("reviewer", "")):
            with self.assertRaises((ModelRetry, ValidationError)):
                await self.runtime.start_role(self.conversation, role, task)
        self.runtime.worktrees.allocate.assert_not_awaited()
        self.assertEqual(self.runtime.workspace_plans, {})

    async def test_approved_flag_without_current_ticket_cannot_allocate(self):
        self.settings("ask")
        await self.call(approved=True)
        self.runtime.worktrees.allocate.assert_not_awaited()

    async def test_policy_changes_while_approving_invalidate_ticket(self):
        self.settings("ask")

        async def change_policy(details, **kwargs):
            self.settings("allow")
            return True, False

        self.interaction.approve = change_policy
        for entry in (self.call, self.explicit):
            self.settings("ask")
            await entry()
        self.runtime.worktrees.allocate.assert_not_awaited()
        self.backend_constructor.assert_not_called()

    async def test_resume_keeps_native_identity_and_workspace_without_allocating(self):
        await self.call()
        previous = next(iter(self.runtime.owner.records.values()))
        directory = previous.working_directory
        self.runtime.worktrees.allocate.reset_mock()
        await self.call(args={"agent_name": "reviewer", "task": "continue", "resume": previous.id})
        self.runtime.worktrees.allocate.assert_not_awaited()
        self.assertEqual(list(self.runtime.owner.records), [previous.id])
        self.assertEqual(previous.working_directory, directory)

    async def test_self_keeps_parent_workspace_without_allocating(self):
        await self.call(args={"agent_name": "self", "task": "inspect"})
        record = next(iter(self.runtime.owner.records.values()))
        self.assertEqual(record.agent_name, "self")
        self.assertIsNone(record.working_directory)
        self.runtime.worktrees.allocate.assert_not_awaited()

    async def test_non_git_explicit_is_foreground_and_shared(self):
        (self.root / ".git").rmdir()
        await self.explicit()
        record = next(iter(self.runtime.owner.records.values()))
        self.assertEqual(record.status, "finished")
        self.assertEqual(record.output, "done")
        self.assertEqual(record.working_directory, str(self.root))
        self.runtime.worktrees.allocate.assert_not_awaited()

    async def model_call(self):
        def respond(messages, info):
            if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
                return ModelResponse(parts=[TextPart("complete")])
            return ModelResponse(parts=[ToolCallPart("delegate_task", {
                "agent_name": "reviewer", "task": "review", "background": False,
            }, tool_call_id="model-call")])

        agent = Agent(FunctionModel(respond), capabilities=[
            DelegationPolicy(self.runtime, "super"), self.policy,
            HandleDeferredToolCalls(handler=self.policy.handle),
            SubAgents(agents=list(self.runtime.agents.values()), workspace_resolver=self.runtime.resolve_workspace,
                      task_control=TaskControl(max_running_tasks=4)),
        ])
        with self.runtime.owner.bind():
            return await agent.run("review", conversation_id="conversation",
                                   workspace=Workspace(LocalWorkspaceBackend(self.root)))

    async def test_model_graph_denial_has_no_allocation(self):
        self.settings("deny")
        await self.model_call()
        self.runtime.worktrees.allocate.assert_not_awaited()

    async def test_model_graph_approval_waits_before_allocation(self):
        await self.exercise_approval(self.model_call, True)
        self.runtime.worktrees.allocate.assert_awaited_once()

    async def test_commit_changes_while_approving_invalidate_ticket(self):
        self.settings("ask")

        baselines = iter(("b" * 40, "c" * 40))

        async def change_commit(details, **kwargs):
            baseline = next(baselines)
            self.stack.enter_context(patch("agent.delegation.git", side_effect=lambda root, *args: (
                str(self.root) if args == ("rev-parse", "--show-toplevel") else baseline
            )))
            return True, False

        self.interaction.approve = change_commit
        for entry in (self.call, self.explicit):
            await entry()
        self.runtime.worktrees.allocate.assert_not_awaited()

    async def test_resolver_rejects_unplanned_directory(self):
        ctx = SimpleNamespace(run_id="untrusted", tool_call_id="untrusted")
        request = SimpleNamespace(agent_name="reviewer", directory=str(self.root))
        with self.assertRaises(ModelRetry):
            await self.runtime.resolve_workspace(ctx, request)
        self.runtime.worktrees.allocate.assert_not_awaited()
