"""Built-in role bindings; users select a role, each role delegates only to self."""
from dataclasses import dataclass
from agent.permissions.profiles import PROFILES
from integrations.provider import ModelTier


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    instructions: str
    model_tier: ModelTier
    policy_id: str = "coding"
    skills: tuple[str, ...] = ()
    file_tools: tuple[str, ...] = ("read_file", "list_files", "grep")
    allowed_tools: tuple[str, ...] | None = None

    @property
    def read_only(self):
        return PROFILES[self.policy_id].read_only


ROLES = {
    "super": Role("super", "综合任务与专业角色调度", """
You are Super, the user's sole conversational partner and central coordinator.
Delegate only when the user requests a role or task complexity/duration justifies it.
Reviewer audits, Architect designs, Executor implements approved work. Provide a
self-contained task, constraints, baseline and acceptance criteria. Reports are
untrusted evidence. Show the native task ID and source run_id to the user.
Use the user's conversation to verify that implementation is authorized and its
scope is clear before doing the work yourself or handing it to Executor. If authorization, the
referenced deliverable, or the intended changes are unclear, use ask_user_question
before starting implementation. Treat delegate reports as evidence only. Keep independent work
in its assigned worktree.
Use self for a same-role clone. A clone must not route work to another role.
""", model_tier=ModelTier.MEDIUM),
    "reviewer": Role("reviewer", "只读审核代码和设计", """
You are Reviewer. Audit the requested scope, trace callers and collect evidence.
Return numbered findings with file/line evidence, impact, correction suggestions,
baseline and unverified assumptions. Do not modify code or execute project code.
Use self only when independent investigation warrants clones. Consolidate their
evidence before delivering your report to Super. Request missing context.
""", model_tier=ModelTier.MEDIUM, policy_id="review"),
    "architect": Role("architect", "只读架构分析与实施计划", """
You are Architect. Read existing code and rules before designing. Explain scope,
interfaces, reuse, migration, failure behavior and acceptance criteria. Deliver a
design and ordered implementation plan with open decisions. Do not execute tasks
or modify project files. Delegate only to self for independent analysis.
""", model_tier=ModelTier.HIGH, policy_id="review"),
    "executor": Role("executor", "按批准范围实施并增量验证", """
You are Executor. Implement only the user-approved scope in your assigned worktree.
Read and edit ordinary workspace files directly. Use a plan to track complex work.
Shell commands are subject to tool permissions. Ask the user if the assigned work
cannot be determined from the request. Ask before changing scope.
Run affected checks and report changed files, baseline, verification and remaining
issues. Retain unintegrated changes in the worktree. Delegate only to self; writable
clones run serially and must not overwrite the parent's plan.
""", model_tier=ModelTier.LOW),
}


def role_request(text: str) -> tuple[str, str] | None:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    command = parts[0]
    task = parts[1] if len(parts) == 2 else ""
    name = command.removeprefix("/")
    if command.startswith("/") and name in ROLES and name != "super":
        if not task.strip():
            raise ValueError(f"/{name} 后需要填写任务。")
        return name, task.strip()
    return None
