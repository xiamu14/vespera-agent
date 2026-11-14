from functools import partial, cache
from pathlib import Path

import logfire
from integrations.telemetry import instrumentation
from dotenv import load_dotenv
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.capabilities import HandleDeferredToolCalls, DynamicCapability, SelectModel
from pydantic_ai.conversation import Conversation as NativeConversation
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai_harness import (
    AskUser,
    ConversationSearch,
    Planning,
    Skills,
    StepPersistence,
    SummarizingCompaction,
)
from pydantic_ai_harness.coder import Coder
from pydantic_ai_harness.conversation_search import SnapshotHistorySource
from pydantic_ai_harness.memory import FileStore
from pydantic_ai_harness.planning import SqlitePlanStore
from pydantic_ai_harness.step_persistence import SqliteStepStore

from agent.conversation import Conversation
from agent.interaction import Interaction
from agent.permissions.policy import ExecutionPermissions
from agent.permissions.search import FilesystemSearch
from agent.permissions.workspace import RestrictedWorkspace
from context.project import ProjectContext
from integrations.mcp import MCPRegistry
from integrations.provider import build_model, load_provider_config
from agent.roles import ROLES
from agent.delegation import Delegations, DelegationPolicy
from agent.role_services import RoleServices, WorkspaceMemory
from pydantic_ai_harness import FileSystem
from pydantic_ai_harness.subagents import SubAgent, SubAgents, TaskControl, DelegationTasks
from storage.execution_lock import session_lease
from storage.sessions import (
    load_session_history,
    save_session,
    session_database,
    session_workspace,
)
from tools.external_hooks import external_result_hooks, hook_config_path, load_hook_settings
from tools.output_hooks import output_hooks
from tools.web_tools import web_fetch, web_search
from tools.review import review_diff


async def create_agent(session_id: str | None = None, path: str | None = None) -> Conversation:
    hook_path = hook_config_path()
    hook_settings = load_hook_settings(hook_path)
    load_dotenv()
    config = load_provider_config()
    model_names = {name: config.model_for_tier(role.model_tier) for name, role in ROLES.items()}
    super_model = model_names["super"]
    model = build_model(super_model, openai_model=OpenAIChatModel)
    logfire.configure(service_name="ani-bot", send_to_logfire=False, metrics=False, console=False)
    logfire.instrument_pydantic_ai(**instrumentation())
    database = session_database()
    workspace = await session_workspace(database, session_id, path)
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStepStore(database=str(database))
    history = await load_session_history(database, session_id) if session_id else []
    if session_id is None:
        session_id = NativeConversation().conversation_id
    await save_session(database, session_id, workspace)
    # Concurrent first-open attempts must all bind to the workspace that won registration.
    workspace = await session_workspace(database, session_id, None)
    project = ProjectContext(workspace, database.parent / "project-context", database=database)
    await project.refresh()
    mcp = MCPRegistry()
    skills = Skills.combine([
        Skills(Path(__file__).resolve().parents[1] / "skills"),
        Skills(
            Path.home() / ".agents" / "skills",
            missing_directories="skip", duplicate_names="keep_first",
        ),
    ])
    interaction = Interaction()
    permissions = ExecutionPermissions(workspace, interaction, database, mcp.path,
                                       policy_id=ROLES["super"].policy_id)
    if hook_path is not None:
        permissions.forbidden += (hook_path,)
    plan_store = SqlitePlanStore(str(database), session=session_id)
    mcp.authorize_stdio = permissions.authorize_stdio
    runtime = Delegations(database, workspace, session_id)
    runtime.mcp_path = mcp.path
    permissions.interaction = runtime.broker
    root_services = [
        AskUser(answerer=runtime.broker.ask), permissions,
        Planning(tools=["write_plan"], store=plan_store),
        HandleDeferredToolCalls(handler=permissions.handle),
    ]
    delegates = []
    for name in ("reviewer", "executor", "architect"):
        role = ROLES[name]
        capabilities = [
            DynamicCapability(RoleServices(runtime, name).for_run, id="role_services"),
            DelegationPolicy(runtime, name), FilesystemSearch(), output_hooks,
            SummarizingCompaction(max_messages=60, keep_messages=20),
            SubAgents(include_self=True, max_depth=3,
                      task_control=TaskControl(max_running_tasks=4)),
        ]
        if role.skills:
            capabilities.append(Skills([Path(__file__).resolve().parents[1] / path for path in role.skills]))
        @cache
        def role_model(key=model_names[name]):
            return build_model(key, openai_model=OpenAIChatModel)

        capabilities.append(SelectModel(lambda ctx, resolve=role_model: resolve()))
        if role.read_only:
            capabilities.append(FileSystem(read_only=True, tools=role.file_tools))
        else:
            capabilities.append(Coder(unrestricted_filesystem=True, repo_context=False, sub_agents=False))
        child = Agent(
            model, name=name,
            description=role.description, instructions=role.instructions,
            capabilities=capabilities,
            model_settings={"timeout": config.models[model_names[name]].timeout},
            tools=[review_diff] if role.read_only else [],
        )
        delegate = SubAgent(child, name=name, description=role.description, read_only=role.read_only,
                            usage_limits=UsageLimits(request_limit=50))
        runtime.agents[name] = delegate
        delegates.append(delegate)
    agent = Agent(
        model, name="super",
        model_settings={"timeout": config.models[super_model].timeout},
        capabilities=[
            DynamicCapability(RoleServices(runtime, "super", root_services).for_run, id="role_services"),
            DelegationPolicy(runtime, "super"), FilesystemSearch(),
            SubAgents(agents=delegates, include_self=True, max_depth=3,
                      workspace_resolver=runtime.resolve_workspace,
                      task_control=TaskControl(max_running_tasks=4)),
            RestrictedWorkspace(workspace, permissions),
            Coder(unrestricted_filesystem=True, repo_context=False, sub_agents=False),
            # Local execution keeps fetches inside the tool permission and hook pipeline.
            output_hooks,
            external_result_hooks(
                hook_settings, runtime.broker, database=database, workspace=workspace, config_path=hook_path,
            ),
            skills,
            mcp,
            DynamicCapability(lambda ctx: None if DelegationTasks.child_id() else StepPersistence(
                store=store, metadata={"workspace": str(workspace)}, capture_frontier=True), id="root_persistence"),
            ConversationSearch(SnapshotHistorySource(store), scope="conversation", max_matches=3),
            SummarizingCompaction(max_messages=60, keep_messages=20),
            WorkspaceMemory(
                FileStore(".agent-memory"), database=database, max_tokens=1000, max_search_results=3,
                injection_errors="raise",
            ),
        ],
        tools=[web_fetch, web_search],
        instructions=(Path(__file__).resolve().parents[1] / "prompts" / "system.md").read_text(encoding="utf-8") + ROLES["super"].instructions,
    )
    agent.instructions(name="project-context", on_change="rewrite")(project.instructions)
    return Conversation(
        agent, session_id, history=history, workspace=workspace, mcp=mcp, skills=skills, interaction=interaction,
        history_loader=partial(load_session_history, database),
        execution_lock=partial(session_lease, database, session_id),
        delegations=runtime, store=store, permissions=permissions,
    )
