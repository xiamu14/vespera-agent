"""Native Permissions with project-wide checks and exact-call approval tickets."""
import asyncio
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import hashlib
import os
import re
from pathlib import Path

from pydantic_ai.exceptions import ApprovalRequired, SkipToolExecution, ToolFailed
from pydantic_ai.tools import ToolApproved, ToolDenied
from pydantic_ai_harness.permissions import Permissions

from agent.permissions.config import config_path, digest, load_settings, redact
from agent.permissions.profiles import PROFILES
from agent.permissions.execution_scope import path_state
from agent.permissions.git_scope import resolve_git_scopes
from agent.permissions.rules import INTERNAL, Decision, decide
from integrations.config_home import config_home
from integrations.model_data import check_model_payload, model_messages
from integrations.network_data import check_data
from integrations.decisions import check_public_text
from integrations.git_push import prepare_push, push_command, send_push
from integrations.web_http import fetch_http
import httpx2
from integrations.network import NetworkDenied
from integrations.network_privacy import SCANNER_VERSION, check_values, public_https
from pydantic_ai.messages import ModelMessagesTypeAdapter
from integrations.shell_http import CurlRequest, curl_args, prepare_curl
from integrations.shell_network import unsupported_network
from storage.execution_lock import ExecutionBusyError, workspace_lease


CURRENT_GRANT: ContextVar[Decision | None] = ContextVar("coder_execution_grant", default=None)
CHECKED_PUSH: ContextVar[tuple[str, str] | None] = ContextVar("coder_checked_push", default=None)
CHECKED_HTTP: ContextVar[CurlRequest | None] = ContextVar("coder_checked_http", default=None)
EXECUTION_CHECK: ContextVar[tuple[Decision, str] | None] = ContextVar("coder_execution_check", default=None)


@dataclass(frozen=True)
class Pending:
    decision: Decision
    fingerprint: str
    arguments: dict


class ExecutionPermissions(Permissions):
    id = "execution_permissions"

    def __init__(self, root: Path, interaction, database: Path, mcp_path: Path, *, policy_id=None):
        super().__init__("read-only")
        self.root = root.resolve()
        self.database = database
        self.interaction = interaction
        self.path = config_path()
        load_settings(self.path)
        self.mcp_path = mcp_path
        self.libraries = (Path(__file__).resolve().parents[2] / "skills", Path.home() / ".agents/skills")
        self.refresh_readonly()
        self.forbidden = tuple(path.resolve() for path in (
            config_home(), self.path, database, Path(str(database) + "-wal"), Path(str(database) + "-shm"),
            database.parent / "permissions.jsonl", database.parent / "execution-locks",
            Path.home() / ".ssh", Path.home() / ".aws",
        ))
        if not self.root.is_relative_to(database.parent.resolve()):
            self.forbidden += (database.parent.resolve(),)
        self.pending: dict[tuple[str, str], Pending] = {}
        self.known_calls = {}
        self.tickets: dict[tuple[str, str], tuple[str, bool]] = {}
        self.grants: set[tuple[str, str]] = set()
        self.lock = asyncio.Lock()
        self.audit_path = database.parent / "permissions.jsonl"
        profile = PROFILES[policy_id] if policy_id is not None else None
        self.role_read_only = profile.read_only if profile else False
        self.allow_sandboxed_shell = profile.sandboxed_shell if profile else False

    def refresh_readonly(self):
        roots = [path.resolve() for path in self.libraries]
        for library in self.libraries:
            if library.is_dir():
                roots.extend(path.resolve() for path in library.iterdir() if (path / "SKILL.md").is_file())
        self.readonly = tuple(roots)

    def evaluate(self, name, tool_def, args):
        if self.root.resolve() != self.root:
            return Decision("deny", "工作区路径已被替换为符号链接", name, forced=True), digest(["workspace-changed", str(self.root)])
        if self.role_read_only and tool_def.tool_kind not in {
            "file_system.read_file", "file_system.list_files", "file_system.grep",
        } and name not in {"delegate_task", "list_tasks", "message_task", "answer_task", "cancel_task",
                           "ask_parent", "ask_user_question", "review_diff", "load_capability"}:
            return Decision("deny", "角色只读边界", name, forced=True), digest(["role-read-only", name])
        settings, version = load_settings(self.path)
        metadata = tool_def.metadata or {}
        command_arg = metadata.get("code_arg_name") if metadata.get("code_arg_language") == "shell" else None
        decision = decide(settings, name, tool_def.tool_kind, args, root=self.root, readonly=self.readonly,
                          forbidden=self.forbidden, command_arg=command_arg,
                          allow_sandboxed_shell=self.allow_sandboxed_shell)
        operation = decision.shell_operation
        if operation is not None and operation.git_write and decision.action != "deny":
            try:
                scopes = resolve_git_scopes(self.root, operation.git_roots, operation.git_inits,
                                            settings.protected_paths, self.forbidden)
                if not scopes:
                    raise PermissionError("Git 写入缺少已验证的目标仓库")
            except (OSError, ValueError, UnicodeError):
                decision = replace(decision, action="deny", reason="无法验证目标 Git 仓库及元数据边界", forced=True)
            else:
                decision = replace(decision, git_scopes=scopes)
        if self.role_read_only and decision.target is not None and not decision.target.is_relative_to(self.root):
            if not any(decision.target.is_relative_to(path) for path in self.readonly):
                return Decision("deny", "只读角色不能离开分配的工作区", name, forced=True), digest(["role-root", name])
        try:
            mcp_version = digest(self.mcp_path.read_bytes().hex())
        except FileNotFoundError:
            mcp_version = "missing"
        target_state = None
        if decision.target is not None:
            try:
                info = decision.target.stat()
                target_state = [info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size]
            except FileNotFoundError:
                pass
        shell_targets = [path_state(path) for path in (
            decision.shell_operation.targets if decision.shell_operation else ()
        )]
        fingerprint = digest([version, mcp_version, str(self.root), name, args, str(decision.target),
                              target_state, shell_targets, decision.git_scopes])
        return decision, fingerprint

    def record(self, ctx, call, action, fingerprint, reason, *, tool_name=None):
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "conversation_id": getattr(ctx, "conversation_id", None), "run_id": getattr(ctx, "run_id", None),
            "tool_call_id": getattr(call, "tool_call_id", None), "tool": tool_name or call.tool_name,
            "action": action, "fingerprint": fingerprint, "reason": reason,
        }
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def before_tool_execute(self, ctx, *, call, tool_def, args):
        decision, fingerprint = self.evaluate(call.tool_name, tool_def, args)
        executing = EXECUTION_CHECK.get()
        if executing is not None and executing != (decision, fingerprint):
            self.record(ctx, call, "deny", fingerprint, "执行前调用、目标或策略发生变化")
            raise SkipToolExecution("执行前批准已失效，请重新发起调用。")
        key = (ctx.run_id, call.tool_call_id)
        if decision.action == "deny":
            self.record(ctx, call, "deny", fingerprint, decision.reason)
            raise SkipToolExecution("权限拒绝：" + decision.reason)
        try:
            metadata = tool_def.metadata or {}
            if decision.command and curl_args(decision.subject) is None and push_command(decision.subject) is None:
                import shutil
                import sys
                settings, _ = load_settings(self.path)
                if not settings.sandbox or sys.platform != "darwin" or shutil.which("sandbox-exec") is None:
                    raise NetworkDenied("本地命令隔离能力不可用；无法通过审批授予执行权限")
            if not (decision.command or (tool_def.tool_kind or "").startswith("file_system.")
                    or call.tool_name in INTERNAL or metadata.get("ani_mcp")
                    or metadata.get("ani_network") == "local"):
                raise NetworkDenied("工具未登记网络属性，拒绝执行")
            if decision.command and (reason := unsupported_network(decision.subject)):
                raise NetworkDenied(reason)
            if call.tool_name == "web_fetch":
                public_https(args["url"])
            elif call.tool_name == "web_search":
                check_data(json.dumps(args, ensure_ascii=False), destination="search", category="public")
            elif (tool_def.metadata or {}).get("ani_mcp"):
                from integrations.mcp import MCPRegistry
                metadata = tool_def.metadata
                MCPRegistry(self.mcp_path).check_service(metadata.get("ani_mcp_service"), metadata.get("ani_mcp_policy"))
                check_data(json.dumps(args, ensure_ascii=False), destination="mcp", category="project")
                if (tool_def.metadata or {}).get("ani_mcp_public_classification"):
                    await check_public_text(json.dumps(args, ensure_ascii=False))
            elif decision.command and (options := curl_args(decision.subject)) is not None:
                settings, _ = load_settings(self.path)
                CHECKED_HTTP.set(prepare_curl(options, self.root, forbidden=self.forbidden,
                                             protected=settings.protected_paths))
                request = CHECKED_HTTP.get()
                fingerprint = digest([fingerprint, SCANNER_VERSION, request.url, request.method, request.header_items,
                                      hashlib.sha256(request.body or b"").hexdigest()])
            elif decision.command and (push := push_command(decision.subject)) is not None:
                CHECKED_PUSH.set(await prepare_push(self.root, push))
                fingerprint = digest([fingerprint, SCANNER_VERSION, CHECKED_PUSH.get()])
        except (NetworkDenied, OSError, UnicodeDecodeError) as error:
            reason = str(error) if isinstance(error, NetworkDenied) else "无法准备已检查的网络请求"
            self.record(ctx, call, "deny", fingerprint, reason)
            raise SkipToolExecution(reason) from None
        if ctx.tool_call_approved:
            ticket = self.tickets.pop(key, None)
            if ticket is None or ticket[0] != fingerprint:
                self.record(ctx, call, "deny", fingerprint, "批准后调用、目标或策略发生变化")
                raise SkipToolExecution("批准已失效：调用、目标或权限配置发生变化，请重新发起。")
            if ticket[1] and not decision.forced:
                self.grants.add((ctx.conversation_id, fingerprint))
            self.record(ctx, call, "execute_approved", fingerprint, decision.reason)
            return await self.execute_network(ctx, decision, args)
        if decision.action == "allow":
            self.record(ctx, call, "allow", fingerprint, decision.reason)
            return await self.execute_network(ctx, decision, args)
        if not decision.forced and (ctx.conversation_id, fingerprint) in self.grants:
            self.record(ctx, call, "allow_remembered", fingerprint, decision.reason)
            return await self.execute_network(ctx, decision, args)
        self.pending[key] = Pending(decision, fingerprint, dict(args))
        self.record(ctx, call, "ask", fingerprint, decision.reason)
        try:
            await super().before_tool_execute(ctx, call=call, tool_def=tool_def, args=args)
        except ApprovalRequired:
            raise
        raise ApprovalRequired(metadata={"coder_permission": {"reason": decision.reason, "fingerprint": fingerprint}})

    async def execute_network(self, ctx, decision, args):
        if not decision.command:
            return args
        options = curl_args(decision.subject)
        push = push_command(decision.subject)
        if options is None and push is None:
            return args
        try:
            if args.get("mode") == "background":
                raise NetworkDenied("受控网络请求仅支持前台执行")
            if push is not None:
                prepared = CHECKED_PUSH.get()
                if prepared is None:
                    raise NetworkDenied("Git 推送缺少执行前检查")
                settings, _ = load_settings(self.path)
                result = await send_push(self.root, push, prepared, protected=tuple(settings.protected_paths))
            else:
                request = CHECKED_HTTP.get()
                if request is None:
                    raise NetworkDenied("HTTP 请求缺少执行前检查")
                _, body, _ = await fetch_http(request.url, method=request.method,
                                             body=request.body, headers=request.headers, category=request.category)
                result = body.decode("utf-8")
        except (NetworkDenied, httpx2.HTTPError, OSError, UnicodeDecodeError, TimeoutError) as error:
            result = str(error) if isinstance(error, NetworkDenied) else "网络操作失败"
        raise SkipToolExecution(result)

    async def prepare_model_request(self, ctx, request_context):
        endpoint = getattr(request_context.model, "base_url", None)
        if endpoint:
            from integrations.network import origin
            try:
                origin(str(endpoint))
                check_values(str(endpoint))
                settings, _ = load_settings(self.path)
                messages, restricted = model_messages(request_context.messages, self.root, settings.protected_paths)
                request_context = replace(request_context, messages=messages)
                if restricted:
                    self.record(ctx, None, "deny_egress", digest(sorted(restricted)),
                                "受保护文件内容不进入模型上下文", tool_name="model_request")
                check_model_payload(json.dumps([
                    part.content for part in request_context.model_request_parameters.instruction_parts or ()
                ]).encode())
                payload = ModelMessagesTypeAdapter.dump_json(request_context.messages, exclude_none=True)
                check_model_payload(payload)
                self.record(ctx, None, "allow_egress", digest([str(endpoint), hashlib.sha256(payload).hexdigest()]),
                            f"NET-10; destination={digest(str(endpoint))}; bytes={len(payload)}",
                            tool_name="model_request")
            except (ValueError, NetworkDenied) as error:
                reason = str(error) if isinstance(error, NetworkDenied) else "模型请求格式或目的地无效"
                self.record(ctx, None, "deny_egress", digest(str(endpoint)), reason,
                            tool_name="model_request")
                raise NetworkDenied("模型请求未通过外发检查：" + reason) from None
        return request_context

    async def before_tool_validate(self, ctx, *, call, tool_def, args):
        self.known_calls[(ctx.run_id, call.tool_call_id)] = tool_def
        return args

    async def handle(self, ctx, requests):
        approvals = {}
        for call in requests.approvals:
            key = (ctx.run_id, call.tool_call_id)
            pending = self.pending.pop(key, None)
            definition = self.known_calls.get(key)
            if pending is None and definition is not None:
                arguments = call.args_as_dict()
                decision, fingerprint = self.evaluate(call.tool_name, definition, arguments)
                if decision.action != "deny":
                    pending = Pending(decision, fingerprint, arguments)
            if pending is None:
                approvals[call.tool_call_id] = ToolDenied("缺少受信任的审批上下文，未执行。")
                continue
            decision = pending.decision
            rememberable = decision.action == "ask" and not decision.forced and not decision.command and not decision.write
            details = f"工具：{call.tool_name}\n\n原因：{decision.reason}"
            if decision.command:
                details += f"\n\n工作目录：{self.root}"
                operation = decision.shell_operation
                if operation and operation.targets:
                    details += "\n\n允许写入或删除的目标（目录包含其子路径）：\n" + "\n".join(map(str, operation.targets))
                elif operation and not operation.git_write:
                    details += "\n\n项目文件只读；仅可写任务临时目录。需要项目输出时请配置 shell_scopes。"
            arguments = redact(pending.arguments)
            fence = "`" * max(3, 1 + max(map(len, re.findall(r"`+", arguments)), default=0))
            details += f"\n\n{fence}json\n{arguments}\n{fence}"
            approved, remember = await self.interaction.approve(details, rememberable=rememberable)
            self.record(ctx, call, "approved" if approved else "denied", pending.fingerprint, decision.reason)
            if approved:
                self.tickets[key] = (pending.fingerprint, remember and rememberable)
                approvals[call.tool_call_id] = ToolApproved()
            else:
                approvals[call.tool_call_id] = ToolDenied(
                    "用户未授权上述工具调用，操作未执行。"
                    "请根据原工具调用的参数识别被拒绝的操作及其目的。"
                    "本轮不要通过更换目录、路径、参数或工具来重试同一操作。"
                    "可以继续处理独立事项，或说明哪些工作因缺少授权而无法完成。"
                )
        return requests.build_results(approvals=approvals)

    async def authorize_stdio(self, name, server, cwd, stack):
        from agent.permissions.workspace import RestrictedBackend
        settings, version = load_settings(self.path)
        if not settings.sandbox:
            raise PermissionError("stdio MCP 需要隔离以限制直连网络")
        if settings.sandbox:
            import shutil
            import sys
            if sys.platform != "darwin" or shutil.which("sandbox-exec") is None:
                raise PermissionError("本地 MCP 隔离后端不可用，拒绝启动")
        identifier = f"mcp.start.{name}"
        subject = json.dumps([server.command, *server.args], ensure_ascii=False)
        try:
            config_bytes = self.mcp_path.read_bytes()
        except FileNotFoundError:
            config_bytes = b""
        fingerprint = digest([version, config_bytes.hex(), server.model_dump(), str(cwd), cwd.stat().st_ino])
        reason = "启动本地 MCP 子进程"
        decision = decide(settings, identifier, None, {"command": subject},
                          root=cwd, readonly=(), forbidden=(), command_arg="command",
                          classify_command=False)
        if decision.action == "deny":
            self.record(self.interaction.context, None, "deny_start", fingerprint, reason, tool_name=identifier)
            raise PermissionError(f"拒绝启动 stdio MCP：{name}")
        if decision.action == "ask":
            self.record(self.interaction.context, None, "ask_start", fingerprint, reason, tool_name=identifier)
            approved, _ = await self.interaction.approve(
                f"启动本地 MCP：{name}\n工作目录：{cwd}\n命令与参数：{redact([server.command, *server.args])}"
                "\n启动即执行程序；不向其开放权限配置和凭据目录。",
                rememberable=False,
            )
            if not approved:
                self.record(self.interaction.context, None, "denied_start", fingerprint, reason, tool_name=identifier)
                raise PermissionError(f"未批准启动 stdio MCP：{name}")
        _, new_version = load_settings(self.path)
        try:
            new_bytes = self.mcp_path.read_bytes()
        except FileNotFoundError:
            new_bytes = b""
        if digest([new_version, new_bytes.hex(), server.model_dump(), str(cwd), cwd.stat().st_ino]) != fingerprint:
            raise PermissionError("MCP 启动批准失效：配置或工作目录发生变化")
        self.record(self.interaction.context, None, "start", fingerprint, reason, tool_name=identifier)
        if not settings.sandbox:
            raise PermissionError("stdio MCP 需要隔离以限制直连网络")
        backend = RestrictedBackend(cwd, self)
        stack.callback(backend.close)
        return "/usr/bin/sandbox-exec", ["-p", backend.profile(), os.path.expanduser(server.command), *server.args], {
            "TMPDIR": str(backend.scratch), "UV_CACHE_DIR": str(backend.scratch / "uv-cache"),
        }

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        # Serial execution makes approval and target rechecks precede every side effect.
        task_control = call.tool_name in {
            "delegate_task", "list_tasks", "message_task", "answer_task", "cancel_task", "ask_parent",
        }
        async with (nullcontext() if task_control else self.lock):
            decision, _ = self.evaluate(call.tool_name, tool_def, args)
            name = call.tool_name
            file_tool = (tool_def.tool_kind or "").startswith("file_system.")
            memory_tool = name in {"read_memory", "search_memory", "write_memory", "delete_memory"}
            lease = nullcontext()
            if file_tool or memory_tool or decision.command or name == "review_diff":
                shared = not (decision.write or decision.command or name in {"write_memory", "delete_memory"})
                lease = workspace_lease(self.database, self.root, shared=shared)
            try:
                with lease:
                    decision, fingerprint = self.evaluate(call.tool_name, tool_def, args)
                    push = CHECKED_PUSH.set(None)
                    http = CHECKED_HTTP.set(None)
                    check = EXECUTION_CHECK.set((decision, fingerprint))
                    token = CURRENT_GRANT.set(decision)
                    try:
                        return await handler(args)
                    finally:
                        CURRENT_GRANT.reset(token)
                        EXECUTION_CHECK.reset(check)
                        CHECKED_PUSH.reset(push)
                        CHECKED_HTTP.reset(http)
            except ExecutionBusyError as exc:
                raise ToolFailed(f"工作区操作暂不可用：{exc}") from exc

    async def wrap_run(self, ctx, *, handler):
        try:
            return await handler()
        finally:
            for mapping in (self.pending, self.tickets, self.known_calls):
                for key in list(mapping):
                    if key[0] == ctx.run_id:
                        del mapping[key]
