from dataclasses import dataclass
import json
from pathlib import Path

from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.capabilities import DynamicCapability, HandleDeferredToolCalls, SelectModel, WebFetch
from pydantic_ai_harness import AskUser, ConversationSearch, FileSystem, Planning, Skills
from pydantic_ai_harness import StepPersistence, SummarizingCompaction
from pydantic_ai_harness.coder import Coder
from pydantic_ai_harness.conversation_search import SnapshotHistorySource
from pydantic_ai_harness.memory import FileStore
from pydantic_ai_harness.planning import SqlitePlanStore
from pydantic_ai_harness.step_persistence import SqliteStepStore
from pydantic_ai_harness.subagents import DelegationTasks, SubAgent, SubAgents, TaskControl

from agent.delegation import DelegationPolicy, Delegations
from agent.permissions.policy import ExecutionPermissions
from agent.role_services import RoleServices, WorkspaceMemory
from agent.roles import ROLES
from context.project import ProjectContext
from integrations.mcp import MCPRegistry
from integrations.provider import build_model, load_provider_config
from tools.external_hooks import external_result_hooks, hook_config_path, load_hook_settings
from tools.output_hooks import output_hooks
from tools.review import review_diff
from tools.web_tools import web_search


@dataclass(frozen=True)
class RunDeps:
    project: ProjectContext
    project_memory: list[dict]
    title: str


async def project_context(ctx: RunContext[RunDeps]):
    context = await ctx.deps.project.instructions(ctx)
    directory = await ctx.workspace.working_dir()
    binding = {"work_path": directory, "changes": ctx.deps.project_memory}
    return f"Project binding (JSON data): {json.dumps(binding, ensure_ascii=False)}\n{context}"


class Runtime:
    def __init__(self, database, resources, model=None):
        self.database = database
        self.resources = resources
        self.store = SqliteStepStore(database=str(database))
        self.model = model
        self.models = {}
        self.config = None if model is not None else load_provider_config()
        self.skills = Skills.combine([
            Skills(resources / "skills"),
            Skills(Path.home() / ".agents" / "skills",
                   missing_directories="skip", duplicate_names="keep_first"),
        ])
        self.agent = Agent(
            self.role_model("super"), name="super", model_settings=self.settings("super"),
            deps_type=RunDeps,
            instructions=(resources / "prompts" / "system.md").read_text(encoding="utf-8")
            + ROLES["super"].instructions,
            capabilities=[
                Coder(unrestricted_filesystem=True, repo_context=False, sub_agents=False),
                WebFetch(native=False, local=True), output_hooks, self.skills,
                DynamicCapability(self.persistence, id="root_persistence"),
                ConversationSearch(SnapshotHistorySource(self.store), scope="conversation", max_matches=3),
                SummarizingCompaction(max_messages=60, keep_messages=20),
                WorkspaceMemory(FileStore(".agent-memory"), database=database, max_tokens=1000,
                                max_search_results=3, injection_errors="raise"),
            ],
            tools=[web_search],
        )
        self.agent.instructions(name="project-context", on_change="rewrite")(project_context)

    def role_model(self, role):
        if self.model is not None:
            return self.model
        if role not in self.models:
            self.models[role] = build_model(self.model_name(role))
        return self.models[role]

    def model_name(self, role):
        return self.config.model_for_tier(ROLES[role].model_tier)

    def settings(self, role):
        return {} if self.config is None else {"timeout": self.config.models[self.model_name(role)].timeout}

    async def persistence(self, ctx):
        if DelegationTasks.child_id() is None:
            return StepPersistence(store=self.store, capture_frontier=True,
                                   metadata={"workspace": await ctx.workspace.working_dir(), "title": ctx.deps.title,
                                             "context_window": str(ctx.model.context_window or "")})
        return None

    def delegate(self, runtime, name):
        role = ROLES[name]
        capabilities = [
            DynamicCapability(RoleServices(runtime, name).for_run, id="role_services"),
            DelegationPolicy(runtime, name), output_hooks,
            SummarizingCompaction(max_messages=60, keep_messages=20),
            SubAgents(include_self=True, max_depth=3, task_control=TaskControl(max_running_tasks=4)),
            SelectModel(lambda ctx: self.role_model(name)),
        ]
        if role.skills:
            capabilities.append(Skills([self.resources / path for path in role.skills]))
        if role.read_only:
            capabilities.append(FileSystem(read_only=True, tools=role.file_tools))
        else:
            capabilities.append(Coder(unrestricted_filesystem=True, repo_context=False, sub_agents=False))
        child = Agent(self.role_model("super"), name=name, description=role.description,
                      instructions=role.instructions, capabilities=capabilities,
                      model_settings=self.settings(name), tools=[review_diff] if role.read_only else [])
        return SubAgent(child, name=name, description=role.description, read_only=role.read_only,
                        usage_limits=UsageLimits(request_limit=50))

    def workspace_services(self, root, conversation_id):
        return WorkspaceServices(self, root, conversation_id)


class WorkspaceServices:
    def __init__(self, core, root, conversation_id):
        database = core.database
        self.root = root
        self.project = ProjectContext(root, database.parent / "project-context", database=database)
        self.mcp = MCPRegistry()
        self.delegations = runtime = Delegations(database, root, conversation_id)
        self.permissions = permissions = ExecutionPermissions(root, runtime.broker, database, self.mcp.path)
        permissions.allow_sandboxed_shell = True
        hook_path = hook_config_path()
        if hook_path is not None:
            permissions.forbidden += (hook_path,)
        self.mcp.authorize_stdio = permissions.authorize_stdio
        runtime.mcp_path = self.mcp.path
        permissions.review_gate = runtime.check_deliverables
        plan = SqlitePlanStore(str(database), session=conversation_id)
        root_services = [
            AskUser(answerer=runtime.broker.ask), permissions, Planning(tools=["write_plan"], store=plan),
            HandleDeferredToolCalls(handler=permissions.handle),
        ]
        runtime.agents.update({name: core.delegate(runtime, name)
                               for name in ("reviewer", "executor", "architect")})
        self.capabilities = [
            DynamicCapability(RoleServices(runtime, "super", root_services).for_run, id="role_services"),
            DelegationPolicy(runtime, "super"),
            SubAgents(agents=list(runtime.agents.values()), include_self=False, max_depth=3,
                      workspace_resolver=runtime.resolve_workspace, task_control=TaskControl(max_running_tasks=4)),
            external_result_hooks(load_hook_settings(hook_path), runtime.broker, database=database,
                                  workspace=root, config_path=hook_path),
            self.mcp,
        ]
