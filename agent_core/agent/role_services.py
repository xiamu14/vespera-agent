"""Per-run native capabilities for mutable permissions, plans and role context."""
from pathlib import Path

from pydantic_ai.capabilities import AbstractCapability, CombinedCapability, HandleDeferredToolCalls
from pydantic_ai_harness import AskUser, Memory, Planning
from pydantic_ai_harness.planning import SqlitePlanStore
from pydantic_ai_harness.subagents import DelegationTasks

from context.project import ProjectContext
from storage.execution_lock import ExecutionBusyError, session_lease, workspace_lease
from integrations.mcp import MCPRegistry
from agent.mcp_services import RunMCP


class WorkspaceMemory(Memory):
    def __init__(self, store, *, database, **kwargs):
        super().__init__(store, **kwargs)
        self.database = database

    async def before_model_request(self, ctx, request_context):
        root = Path(await ctx.workspace.working_dir())
        try:
            with workspace_lease(self.database, root, shared=True):
                return await super().before_model_request(ctx, request_context)
        except ExecutionBusyError:
            return request_context


class RoleServices(AbstractCapability):
    def __init__(self, runtime, role, root_services=None):
        self.runtime = runtime
        self.role = role
        self.root_services = root_services

    async def for_run(self, ctx):
        if DelegationTasks.child_id() is None and self.root_services is not None:
            return CombinedCapability(self.root_services)
        root = Path(await ctx.workspace.working_dir()).resolve()
        policy = self.runtime.role_permissions(root, self.role)
        # Each child has its own plan. A clone cannot replace its parent's approved plan.
        store = SqlitePlanStore(str(self.runtime.database), session=ctx.run_id)
        project = ProjectContext(root, self.runtime.database.parent / "project-context", database=self.runtime.database)
        await project.refresh()
        services = [
            policy, HandleDeferredToolCalls(handler=policy.handle),
            AskUser(answerer=self.runtime.broker.ask),
            RunBoundary(self.runtime.database, project),
        ]
        if not policy.role_read_only:
            services.append(Planning(tools=["write_plan"], store=store))
        if self.role == "super":
            registry = MCPRegistry(self.runtime.mcp_path)
            registry.authorize_stdio = policy.authorize_stdio
            services.append(RunMCP(registry))
        return CombinedCapability(services)


class RunBoundary(AbstractCapability):
    def __init__(self, database, project):
        self.database = database
        self.project = project

    def get_instructions(self):
        return self.project.instructions

    async def wrap_run(self, ctx, *, handler):
        with session_lease(self.database, ctx.conversation_id):
            return await handler()
