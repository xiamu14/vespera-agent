"""Deny first, protected resources second, explicit rules third, ask by default."""
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
import shlex

from agent.permissions.config import Settings
from agent.permissions.execution_scope import scoped_operation
from agent.permissions.git_scope import GitScope
from agent.permissions.shell_rules import ShellOperation, classify_shell
from integrations.protected_paths import is_protected_path


INTERNAL = frozenset({
    "write_plan", "review_diff", "web_fetch", "web_search",
    "ask_user_question", "load_capability", "search_tools", "search_conversation_history",
    "read_memory", "write_memory", "search_memory", "delete_memory",
    "delegate_task", "list_tasks", "message_task", "answer_task", "cancel_task", "ask_parent",
})


def git_access(command: str) -> str | None:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return None
    # shortcut: quoted shell programs are not parsed recursively; add shell AST parsing if nested approval is needed.
    if not any(Path(word).name == "git" for word in words):
        return None
    readonly = {"status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "version"}
    access = "read"
    segments = [[]]
    for word in words:
        if word in {";", "&&", "||"}:
            segments.append([])
        else:
            segments[-1].append(word)
    for segment in segments:
        if not segment:
            continue
        if segment[0] == "cd" and len(segment) == 2:
            continue
        if Path(segment[0]).name != "git":
            return "approval"
        index = 1
        while index < len(segment):
            option = segment[index]
            if option in {"-C", "-c", "--git-dir", "--work-tree"}:
                index += 2
            elif option.startswith(("--git-dir=", "--work-tree=")):
                index += 1
            else:
                break
        if index >= len(segment) or any(word in {"|", "&", "(", ")", ">", ">>"} for word in segment):
            return "approval"
        subcommand = segment[index]
        if subcommand in {"add", "commit", "init"}:
            access = "write"
        elif subcommand not in readonly:
            return "approval"
    return access


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    subject: str
    target: Path | None = None
    write: bool = False
    command: bool = False
    forced: bool = False
    sandbox_only: bool = False
    shell_operation: ShellOperation | None = None
    git_scopes: tuple[GitScope, ...] = ()


def decide(settings: Settings, name: str, kind: str | None, args: dict, *, root: Path,
           readonly: tuple[Path, ...], forbidden: tuple[Path, ...], command_arg: str | None,
           allow_sandboxed_shell: bool = False, classify_command: bool = True) -> Decision:
    command = command_arg is not None
    file_tool = bool(kind and kind.startswith("file_system."))
    write = bool(kind and kind.rsplit(".", 1)[-1] in {"write_file", "edit_file", "create_directory"})
    target = (root / args.get("path", ".")).resolve() if file_tool else None
    subject = str(args[command_arg]) if command else str(target) if target else name
    operation = classify_shell(subject, root, settings) if command and classify_command else None
    if operation is not None:
        operation = scoped_operation(operation, subject, root, settings, forbidden)

    def result(action, reason, forced=False):
        return Decision(action, reason, subject, target, write, command, forced, shell_operation=operation)

    if target and any(target == path or target.is_relative_to(path) for path in forbidden):
        return result("deny", "权限配置、凭据目录和运行存储不允许工具访问", True)
    def matches_rule(rule):
        if rule.argument is None:
            resource = subject
        elif rule.argument == "path" and target:
            resource = str(target)
        elif rule.argument in args:
            resource = str(args[rule.argument])
        else:
            return False
        return fnmatchcase(name, rule.tool) and fnmatchcase(resource, rule.pattern)
    matches = [rule for rule in settings.rules if matches_rule(rule)]
    if any(rule.action == "deny" for rule in matches):
        return result("deny", "命中拒绝规则", True)
    if operation and (operation.action == "deny" or not operation.supported):
        return result("deny", operation.reason, True)
    if operation and operation.action == "ask":
        return result("ask", operation.reason, True)
    if target:
        relative = target.relative_to(root).as_posix() if target.is_relative_to(root) else str(target)
        if is_protected_path(relative, settings.protected_paths):
            return result("ask", "受保护资源需逐次确认", True)
        if not target.is_relative_to(root):
            if not write and any(target.is_relative_to(path) for path in readonly):
                return result("allow", "只读技能资源")
            return result("ask", "访问工作目录之外的资源需逐次确认", True)
    if any(rule.action == "ask" for rule in matches):
        return result("ask", "命中确认规则", not all(rule.remember for rule in matches))
    if settings.mode == "read-only" and (write or command):
        return result("ask", "read-only 模式确认写入与命令")
    if any(rule.action == "allow" for rule in matches):
        return result("allow", "命中明确放行规则")
    if file_tool:
        return result("allow", "工作目录内普通文件操作")
    if name in INTERNAL:
        return result("allow", "框架问答、发现与会话记忆工具")
    if allow_sandboxed_shell and name == "shell" and command and settings.sandbox and settings.mode == "auto":
        return Decision("allow", "Shell 沙箱内执行", subject, command=True, sandbox_only=True,
                        shell_operation=operation)
    if settings.mode == "full":
        return result("allow", "full 模式；硬限制仍生效")
    return result("ask", "命令、外部工具或未分类操作默认询问", True)
