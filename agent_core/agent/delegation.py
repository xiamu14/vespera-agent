"""Native managed delegations plus project-specific role/worktree boundaries."""
import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path
from hashlib import sha256
from dataclasses import dataclass, replace

from pydantic_ai import RunContext, UsageLimits
from pydantic_ai.capabilities import AbstractCapability, CombinedCapability, HandleDeferredToolCalls
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import ToolDenied
from pydantic_ai.exceptions import ModelRetry, SkipToolExecution
from pydantic_ai.usage import RunUsage
from pydantic_ai.workspaces import LocalWorkspaceBackend, Workspace
from pydantic_ai_harness.subagents import (
    DelegationReports, DelegationTasks, SubAgents, TaskControl,
)

from agent.interaction import Interaction
from agent.permissions.policy import ExecutionPermissions
from agent.permissions.workspace import RestrictedBackend
from agent.roles import ROLES
from storage.worktrees import Worktrees, git
from storage.delegations import delegation_directory
from storage.execution_lock import delegation_lease
from tools.external_hooks import hook_config_path


class RoleTaskOwner(DelegationTasks):
    def reports(self, *, conversation_id: str, parent_id: str | None = None):
        if parent_id is None:
            return []
        return super().reports(conversation_id=conversation_id, parent_id=parent_id)

    def super_reports(self, conversation_id: str):
        return super().reports(conversation_id=conversation_id)


@dataclass(frozen=True)
class WorkspacePlan:
    key: str
    agent_name: str
    directory: str
    baseline: str | None
    previous: str | None = None


class Delegations:
    def __init__(self, database, workspace, conversation_id):
        self.database = database
        self.workspace = workspace
        self.conversation_id = conversation_id
        directory = database.parent / "worktrees"
        if directory.resolve().is_relative_to(workspace.resolve()):
            key = sha256(str(database.resolve()).encode()).hexdigest()[:16]
            directory = Path.home() / ".cache" / "ani bot" / "worktrees" / key
        self.worktrees = Worktrees(directory / sha256(conversation_id.encode()).hexdigest(), workspace)
        self.owner = None
        self.stack = None
        self.observer = None
        self.broker = Interaction()
        self.agents = {}
        self.backends = []
        self.write_locks = {}
        self.workspace_plans = {}

    async def open(self, store, *, observer=None, on_question=None):
        if observer is not None:
            self.observer = observer
        if on_question is not None and (self.stack is None or observer is not None):
            self.broker.on_question = on_question
        if self.stack is not None:
            return
        self.broker.active = True
        self.owner = RoleTaskOwner(
            directory=delegation_directory(self.database, self.conversation_id),
            step_store=store, observer=self.observe, max_depth=3,
        )
        stack = AsyncExitStack()
        opened = False
        try:
            if observer is not None:
                stack.enter_context(delegation_lease(self.database, self.conversation_id))
            await stack.enter_async_context(self.owner.opened())
            self.stack = stack
            opened = True
        finally:
            if not opened:
                await stack.aclose()

    async def observe(self, event):
        if self.observer is not None:
            await self.observer(event)

    async def close(self):
        if self.stack is not None:
            await self.stack.aclose()
            self.stack = None
        for backend in self.backends:
            backend.close()
        self.backends.clear()
        self.broker.close()

    def write_lock(self, directory):
        return self.write_locks.setdefault(directory, asyncio.Lock())

    def role_permissions(self, directory, name):
        role = ROLES[name]
        policy = ExecutionPermissions(directory, self.broker, self.database, self.mcp_path,
                                      policy_id=role.policy_id)
        if name != "super":
            policy.libraries = tuple(Path(__file__).resolve().parents[1] / path for path in role.skills)
            policy.refresh_readonly()
        hook_path = hook_config_path()
        if hook_path is not None:
            policy.forbidden += (hook_path,)
        return policy

    async def resolve_workspace(self, ctx, request):
        # Native SubAgents invokes this inside the tool body, after all execution
        # authorization hooks. A wrap hook itself runs before authorization.
        plan = self.workspace_plans.get((ctx.run_id, ctx.tool_call_id))
        if plan is None or (plan.directory, plan.agent_name) != (request.directory, request.agent_name):
            raise ModelRetry("委派工作区缺少当前调用的受信任计划。")
        if plan.directory == str(self.workspace):
            directory = self.workspace
        else:
            if plan.previous is not None:
                record = await asyncio.to_thread(self.worktrees.validate, plan.previous)
            else:
                record = await self.worktrees.allocate(plan.key, plan.baseline)
            if (record.path, record.baseline) != (plan.directory, plan.baseline):
                raise ModelRetry("分配的工作区与已授权计划不匹配。")
            directory = Path(record.path)
        policy = self.role_permissions(directory, request.agent_name)
        backend = RestrictedBackend(directory, policy)
        self.backends.append(backend)
        return Workspace(backend)

    async def start_role(self, conversation, role, task, *, source_id=None):
        if self.owner is None:
            raise RuntimeError("委派会话未打开")
        if role not in self.agents:
            raise ModelRetry("未知内置角色")
        if not isinstance(task, str) or not task.strip():
            raise ModelRetry("委派任务必须是非空文本。")
        source = self.owner.records.get(source_id) if source_id else None
        if source_id and source is None:
            raise ValueError("找不到指定的原生任务 ID。")
        if source is not None and (source.status != "finished" or source.outcome != "ok"):
            raise ValueError("来源任务尚未成功交付。")
        if source is not None:
            task += f"\n\n来源任务 {source.id}，run_id {source.run_id}\n{source.output}"
        revision = "HEAD"
        if source is not None and source.working_directory and source.working_directory != str(self.workspace):
            origin = await asyncio.to_thread(self.worktrees.validate, source.working_directory)
            if await asyncio.to_thread(git, Path(origin.path), "status", "--porcelain"):
                raise ValueError("来源 worktree 仍有未提交修改；请先明确提交检查点再交接。")
            revision = await asyncio.to_thread(git, Path(origin.path), "rev-parse", "HEAD")
        # Explicit requests use the same native validation, approval tickets and
        # handler as model calls; calling delegate_task directly bypasses hooks.
        toolset = SubAgents(
            agents=list(self.agents.values()), workspace_resolver=self.resolve_workspace,
            task_control=TaskControl(max_running_tasks=4),
        ).get_toolset()
        policy = conversation.permissions
        # This parent only supplies identity and a directory to the native tool.
        # The restricted child backend (including its scratch directory) is
        # constructed by the resolver only after the tool has been authorized.
        backend = LocalWorkspaceBackend(self.workspace)
        ctx = RunContext(
            deps=None, model=conversation.agent.model, usage=RunUsage(), messages=[],
            conversation_id=self.conversation_id, agent=conversation.agent,
            workspace=Workspace(backend), usage_limits=UsageLimits(request_limit=50),
        )
        capability = CombinedCapability([
            DelegationPolicy(self, "super", revision=revision), policy,
            HandleDeferredToolCalls(handler=policy.handle),
        ])
        call = ToolCallPart("delegate_task", {"agent_name": role, "task": task, "background": True})
        with self.owner.bind():
            manager = await ToolManager(toolset, root_capability=capability).for_run_step(ctx)
            try:
                result = await manager.handle_call(call, wrap_validation_errors=False)
                if isinstance(result, ToolDenied):
                    raise ValueError(result.message)
                return result
            finally:
                key = (ctx.run_id, call.tool_call_id)
                for mapping in (self.workspace_plans, policy.pending, policy.tickets, policy.known_calls):
                    mapping.pop(key, None)

    async def plan_workspace(self, key, name, task, *, revision="HEAD", previous=None):
        """Read and bind a proposed directory and commit without creating resources."""
        if previous == str(self.workspace) or (previous is None and revision == "HEAD"
                                               and not (self.workspace / ".git").exists()):
            return WorkspacePlan(key, name, str(self.workspace), None, previous), (
                f"共享工作目录 {self.workspace}\n{task}"
            )
        if previous is not None:
            record = await asyncio.to_thread(self.worktrees.validate, previous)
            directory, baseline = record.path, record.baseline
        else:
            root = Path(await asyncio.to_thread(git, self.workspace, "rev-parse", "--show-toplevel")).resolve()
            if root != self.workspace:
                raise ModelRetry("独立角色任务目前要求工作目录为 Git 仓库根目录。")
            baseline = await asyncio.to_thread(git, root, "rev-parse", "--verify", f"{revision}^{{commit}}")
            directory = str(self.worktrees.directory / sha256(key.encode()).hexdigest())
        return WorkspacePlan(key, name, directory, baseline, previous), (
            f"基线 {baseline}\n工作区 {directory}\n{task}"
        )

    def reports(self):
        return DelegationReports(self.owner, conversation_id=self.conversation_id)


class DelegationPolicy(AbstractCapability):
    def __init__(self, runtime, role, *, revision="HEAD"):
        self.runtime = runtime
        self.role = role
        self.revision = revision

    async def wrap_tool_validate(self, ctx, *, call, tool_def, args, handler):
        valid = False
        try:
            result = await handler(args)
            valid = True
            return result
        finally:
            if not valid and call.tool_name == "delegate_task":
                self.runtime.workspace_plans.pop((ctx.run_id, call.tool_call_id), None)

    async def prepare_tools(self, ctx, tool_defs):
        allowed = ROLES[self.role].allowed_tools
        tools = tool_defs if allowed is None else [tool for tool in tool_defs if tool.name in allowed]
        if self.role == "super" and DelegationTasks.child_id() is None:
            tools = [replace(tool, sequential=True) if tool.name == "delegate_task" else tool for tool in tools]
        if self.role == "super" and DelegationTasks.child_id() is not None:
            result = []
            for tool in tools:
                if tool.name == "delegate_task":
                    schema = tool.parameters_json_schema
                    properties = dict(schema["properties"])
                    properties["agent_name"] = {**properties["agent_name"], "enum": ["self"]}
                    tool = replace(tool, parameters_json_schema={**schema, "properties": properties})
                result.append(tool)
            return result
        return tools

    async def before_tool_validate(self, ctx, *, call, tool_def, args):
        allowed = ROLES[self.role].allowed_tools
        if allowed is not None and call.tool_name not in allowed:
            raise SkipToolExecution("当前角色未绑定此工具。")
        if call.tool_name != "delegate_task":
            return args
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except json.JSONDecodeError as exc:
            raise ModelRetry("委派参数必须是 JSON 对象。") from exc
        if not isinstance(args, dict):
            raise ModelRetry("委派参数必须是 JSON 对象。")
        args = dict(args)
        if not isinstance(args.get("task"), str) or not args["task"].strip():
            raise ModelRetry("委派任务必须是非空文本。")
        if "background" in args and not isinstance(args["background"], bool):
            raise ModelRetry("background 必须是布尔值。")
        for field in ("agent_name", "resume", "working_directory", "model"):
            if args.get(field) is not None and not isinstance(args[field], str):
                raise ModelRetry(f"{field} 必须是文本。")
        # These built-in delegates have no model menu. Reject before a resolver
        # could allocate: native task-control resolves its model after workspace.
        if args.get("model") is not None:
            raise ModelRetry("内置角色不支持 model 参数。")
        name = args.get("agent_name")
        child = DelegationTasks.child_id()
        if name != "self" and (self.role != "super" or child is not None):
            raise ModelRetry("分身只能委派 self；跨角色任务由 Super 主运行调度。")
        if name == "self":
            if args.get("working_directory"):
                raise ModelRetry("同角色分身必须复用父工作区。")
            if not ROLES[self.role].read_only and args.get("background"):
                raise ModelRetry("写入角色分身必须前台串行执行。")
            return args
        if name not in self.runtime.agents:
            raise ModelRetry("未知内置角色")
        owner = self.runtime.owner
        previous = owner.check_resume(
            args["resume"], conversation_id=ctx.conversation_id, agent_name=name,
        ) if args.get("resume") is not None else None
        if previous is not None and previous.working_directory is None:
            raise ModelRetry("来源原生任务缺少绑定的角色工作区。")
        plan, task = await self.runtime.plan_workspace(
            f"{ctx.run_id}:{call.tool_call_id}", name, args["task"], revision=self.revision,
            previous=previous.working_directory if previous else None,
        )
        if args.get("working_directory") is not None and args["working_directory"] != plan.directory:
            raise ModelRetry("角色工作目录必须与当前调用的应用分配计划一致。")
        self.runtime.workspace_plans[(ctx.run_id, call.tool_call_id)] = plan
        args["working_directory"], args["task"] = plan.directory, task
        if plan.directory == str(self.runtime.workspace):
            args["background"] = False
        return args

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        try:
            return await self._wrap_execution(ctx, call=call, tool_def=tool_def, args=args, handler=handler)
        finally:
            if call.tool_name == "delegate_task":
                self.runtime.workspace_plans.pop((ctx.run_id, call.tool_call_id), None)

    async def _wrap_execution(self, ctx, *, call, tool_def, args, handler):
        child_id = DelegationTasks.child_id()
        record = self.runtime.owner.records.get(child_id)
        clone = record is not None and record.agent_name == "self"
        directory = await ctx.workspace.working_dir()
        if call.tool_name == "delegate_task" and args.get("agent_name") == "self" and not ROLES[self.role].read_only:
            if clone:
                raise SkipToolExecution("写入分身首版不再嵌套委派。")
            async with self.runtime.write_lock(directory):
                return await handler(args)
        side_effect = (call.tool_name == "write_plan" or
                       tool_def.tool_kind in {"file_system.write_file", "file_system.edit_file", "file_system.create_directory"}
                       or (tool_def.metadata or {}).get("code_arg_language") == "shell")
        if not clone and not ROLES[self.role].read_only and side_effect:
            async with self.runtime.write_lock(directory):
                return await handler(args)
        return await handler(args)
