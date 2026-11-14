"""Classify the supported Shell grammar without evaluating expansions."""
from dataclasses import dataclass, replace
from integrations.protected_paths import is_protected_path
from pathlib import Path
import re

from agent.permissions.config import Settings
from agent.permissions.execution_scope import creation_targets
from agent.permissions.shell_syntax import OUTPUT_FD_COPIES, Token, command_after_prefixes, parse_shell
from integrations.shell_http import curl_args
from integrations.git_push import push_command
from agent.permissions.shell_parameters import (
    find_read, git_reference_read, grep_read, mkdir_targets, sed_read, truncate_targets,
)


@dataclass(frozen=True)
class ShellOperation:
    action: str
    reason: str
    targets: tuple[Path, ...] = ()
    git_write: bool = False
    supported: bool = True
    needs_classification: bool = False
    git_roots: tuple[Path, ...] = ()
    git_inits: tuple[Path, ...] = ()


UNKNOWN = ShellOperation("ask", "命令副作用无法完整识别，仅允许受限执行", needs_classification=True)
BULK_DELETE = ShellOperation("deny", "禁止递归删除或清空根目录、受保护资源或未限定范围")
READ = ShellOperation("allow", "本地读取")
GIT_WRITE = ShellOperation("allow", "本地 Git 元数据写入", git_write=True)

READ_VARIABLE_ARGUMENTS = frozenset({"pwd", "ls", "cat", "head", "tail", "wc", "stat", "echo", "printf", "[", "test"})


def collapse_directory_probes(segments, joins):
    # shortcut: recognize only the literal directory-probe grammar, expand when more compound forms are needed.
    for index in reversed(range(len(segments) - 4)):
        if joins[index:index + 4] != [";"] * 4:
            continue
        group = segments[index:index + 5]
        if any(token.operator for segment in group for token in segment):
            continue
        header = [token.source for token in group[0]]
        if len(header) != 4 or header[0] != "for" or header[2:] != ["in", "*/"]:
            continue
        name = header[1]
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", name):
            continue
        for variable in (f"${name}", "${" + name + "}"):
            if [[token.source for token in segment] for segment in group[1:]] == [
                ["do", "if", "[", "-d", f'"{variable}/.git"', "]"],
                ["then", "echo", f'"REPO: {variable}"'], ["fi"], ["done"],
            ]:
                segments[index:index + 5] = [[Token("true")]]
                del joins[index:index + 4]
                break


def classify_shell(command: str, root: Path, settings: Settings) -> ShellOperation:
    segments, joins, issue = parse_shell(command)
    collapse_directory_probes(segments, joins)
    operations = []
    states = {(root, True)}
    index = 0
    while index < len(segments):
        end = index
        while end < len(joins) and joins[end] == "|":
            end += 1
        connector = joins[index - 1] if index else ";"
        active = {(cwd, status) for cwd, status in states
                  if connector not in {"&&", "||"} or status == (connector == "&&")}
        following = states - active
        for cwd, _ in sorted(active):
            for stage in range(index, end + 1):
                segment = segments[stage]
                operation, changed = simple_operation(segment, root, cwd, settings)
                label = segment[0].value if segment else "空命令"
                operations.append((stage + 1, label, operation))
                if changed is not None and index == end:
                    following.add((changed, True))
                    following.add((cwd, False))
            if index != end or changed is None:
                following.update(((cwd, True), (cwd, False)))
        states = following
        if len(states) > 32:
            issue = issue or "目录分支超出分类预算"
            states = {(root, True), (root, False)}
        index = end + 1

    def explain(entry):
        number, label, operation = entry
        return replace(operation, reason=f"第 {number} 段（{Path(label).name}）：{operation.reason}")

    denied = next((entry for entry in operations
                   if entry[2].action == "deny" or not entry[2].supported), None)
    if denied and not (len(segments) == 1 and push_command(command) is not None
                       and not any(token.variable for token in segments[0])):
        return replace(explain(denied), action="deny")
    if issue:
        return replace(UNKNOWN, reason=issue + "；仅允许受限执行")
    if len(segments) == 1 and not any(token.variable for token in segments[0]):
        options = curl_args(command)
        if options is not None:
            if any(options[i].upper() == "DELETE" and options[i - 1] in {"-X", "--request"}
                   for i in range(1, len(options))):
                return ShellOperation("ask", "确认 HTTP DELETE 远端删除操作")
            return READ
        if (push := push_command(command)) is not None:
            if push.force or push.delete:
                return ShellOperation("ask", "确认 Git 强推或删除远端引用")
            return READ
    unknown = next((entry for entry in operations if not entry[2].targets
                    and entry[2].action == "ask" and entry[2].supported), None)
    if unknown:
        number, label, _ = unknown
        operation = explain(unknown)
        if unknown[2] == UNKNOWN:
            operation = replace(operation, reason=f"第 {number} 段（{Path(label).name}）：程序或参数的副作用无法完整识别；仅允许受限执行")
        return operation
    targets = tuple(dict.fromkeys(path for _, _, op in operations for path in op.targets))
    git_write = any(op.git_write for _, _, op in operations)
    if git_write and any(token.expands for segment in segments for token in segment):
        return replace(UNKNOWN, reason="包含未固定的路径展开，不能授予 Git 元数据写入权限")
    if git_write and ("|" in joins or any(Path(label).name not in {"git", "cd"} for _, label, _ in operations)):
        return replace(UNKNOWN, reason="Git 元数据写入不能与管道或其他程序混合授权；仅允许受限执行")
    asked = next((entry for entry in operations if entry[2].action == "ask"), None)
    if asked:
        return replace(explain(asked), targets=targets, git_write=git_write,
                       git_roots=tuple(dict.fromkeys(path for _, _, op in operations for path in op.git_roots)),
                       git_inits=tuple(dict.fromkeys(path for _, _, op in operations for path in op.git_inits)),
                       supported=all(op.supported for _, _, op in operations))
    if git_write:
        return replace(GIT_WRITE,
                       git_roots=tuple(dict.fromkeys(path for _, _, op in operations for path in op.git_roots)),
                       git_inits=tuple(dict.fromkeys(path for _, _, op in operations for path in op.git_inits)))
    return READ


def simple_operation(tokens, root, cwd, settings):
    command = command_after_prefixes(tokens)
    if len(command) != len(tokens):
        operation, _ = simple_operation(command, root, cwd, settings)
        return (operation if operation.action == "deny" else UNKNOWN), None
    words = []
    redirects = []
    unsupported = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.operator and token.value in OUTPUT_FD_COPIES:
            # Duplicating stdout/stderr opens no new path. Check other redirections separately.
            pass
        elif token.operator:
            if token.value in {">", ">>", ">|"} and index + 1 < len(tokens) and not tokens[index + 1].operator:
                redirects.append(tokens[index + 1].value)
                index += 1
            else:
                unsupported = token.value
        else:
            words.append(token.value)
        index += 1
    name = Path(words[0]).name if words else ""
    args = words[1:]
    writing = destructive(redirects, root, cwd, settings) if redirects else None
    if writing and writing.action == "deny":
        return writing, None
    if words and words[0] == "cd":
        if len(args) != 1 or redirects or unsupported or any(token.expands or token.variable for token in tokens):
            return replace(UNKNOWN, reason="cd 参数或目录展开无法静态确定"), None
        target = (cwd / args[0]).resolve()
        if not target.is_relative_to(root):
            return replace(UNKNOWN, reason="cd 目标位于工作区之外"), target
        return READ, target
    operation = classify_segment(name, args, root, cwd, settings) if words else READ
    if operation.action == "deny" or not operation.supported:
        return replace(operation, action="deny"), None
    if redirects:
        if operation.action == "allow" and not operation.git_write:
            operation = writing
        elif operation.action == "ask" and operation != UNKNOWN:
            operation = replace(operation, targets=tuple(dict.fromkeys((*operation.targets, *writing.targets))))
        else:
            operation = replace(UNKNOWN, reason="重定向与程序副作用无法同时约束")
    if operation.action == "deny":
        return operation, None
    if name == "printf" and len(tokens) > 1 and (tokens[1].variable or tokens[1].expands):
        return replace(UNKNOWN, reason="printf 的格式或选项无法静态验证"), None
    if any(token.variable for token in tokens) and (
        not tokens or tokens[0].variable or redirects or operation != READ or name not in READ_VARIABLE_ARGUMENTS
    ):
        return replace(UNKNOWN, reason="变量展开用于无法静态验证的操作或目标"), None
    trusted = not words or "/" not in words[0] or words[0] in {
        f"/bin/{name}", f"/usr/bin/{name}", f"/usr/sbin/{name}",
    }
    if unsupported:
        return replace(UNKNOWN, reason=f"暂不支持 Shell 操作符 {unsupported}"), None
    if not trusted:
        return replace(UNKNOWN, reason="可执行文件不在受支持的系统路径中"), None
    return operation, None


def classify_segment(name, args, root, cwd, settings):
    if name == "rm":
        options = []
        operands = []
        for index, arg in enumerate(args):
            if arg == "--":
                operands.extend(args[index + 1:])
                break
            (options if arg.startswith("-") else operands).append(arg)
        operation = destructive(operands, root, cwd, settings)
        if any(not re.fullmatch(r"-[fRrdiPvW]+", flag) and flag not in {"--recursive", "--force"}
               for flag in options):
            return BULK_DELETE if operation.action == "deny" else UNKNOWN
        return operation
    if name == "find":
        # Actions that launch programs or open output files must never inherit a
        # read classification from neighboring predicates (including -delete).
        if "-delete" in args:
            paths = []
            for arg in args:
                if arg.startswith("-") or arg in {"!", "("}:
                    break
                paths.append(arg)
            deletion = destructive(paths, root, cwd, settings) if paths else BULK_DELETE
            return deletion if deletion.action == "deny" else UNKNOWN
        return READ if find_read(args) else UNKNOWN
    if name == "grep":
        return READ if grep_read(args) else UNKNOWN
    if name == "sed":
        return READ if sed_read(args) else UNKNOWN
    if name in {"truncate", "mkdir"}:
        operands = truncate_targets(args) if name == "truncate" else mkdir_targets(args)
        operation = destructive(operands, root, cwd, settings) if operands else UNKNOWN
        if name == "mkdir" and "-p" in args and operation.action == "ask" and operation.targets:
            operation = replace(operation, targets=creation_targets(operation.targets, root))
        return operation
    if name == "git":
        return git_operation(args, root, cwd, settings)
    if name in {"cp", "mv"} and args and not any(arg.startswith("--target-directory") or arg == "-t" for arg in args):
        return destructive(args[-2:] if name == "mv" else args[-1:], root, cwd, settings)
    if name == "rsync" and any(arg.startswith("--delete") for arg in args):
        return destructive(args[-1:], root, cwd, settings)
    if name in READ_VARIABLE_ARGUMENTS | {"rg", "true", "false"}:
        if name == "printf" and args and args[0] == "-v":
            return UNKNOWN
        if not any(arg.startswith(("--pre", "--hostname-bin")) or arg == "-z" for arg in args):
            return READ
    return UNKNOWN


def destructive(operands, root, cwd, settings):
    targets = []
    for operand in operands:
        if any(char in operand for char in "*?["):
            return BULK_DELETE
        target = (cwd / Path(operand).expanduser()).resolve()
        if not target.is_relative_to(root) or target == root:
            return BULK_DELETE
        relative = target.relative_to(root)
        if is_protected_path(relative.as_posix(), settings.protected_paths):
            return BULK_DELETE
        targets.append(target)
    return ShellOperation("ask", "需要确认删除或覆盖目标", tuple(targets)) if targets else UNKNOWN


def git_operation(args, root, cwd, settings):
    while args and args[0] in {"-C", "-c"}:
        if len(args) < 2:
            return UNKNOWN
        option, value, *args = args
        if option == "-C":
            cwd = (cwd / value).resolve()
        elif not value.startswith(("user.name=", "user.email=")):
            return UNKNOWN
    if not args:
        return UNKNOWN
    verb, *operands = args
    if verb == "clean":
        return READ if cwd.is_relative_to(root) and operands in (["-n"], ["--dry-run"]) else BULK_DELETE
    if not cwd.is_relative_to(root):
        return UNKNOWN
    if verb in {"branch", "tag", "remote"} and git_reference_read(verb, operands):
        return READ
    if verb in {"reset", "restore", "checkout", "push", "rebase", "branch", "tag", "remote"}:
        return ShellOperation("deny", "当前执行端无法约束此 Git 操作的副作用", (cwd,), supported=False)
    if verb in {"add", "commit", "init"}:
        if not git_write_arguments(verb, operands):
            return UNKNOWN
        return replace(GIT_WRITE, git_roots=(cwd,), git_inits=(cwd,) if verb == "init" else ())
    if verb in {"status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "version"}:
        outputs = []
        for index, argument in enumerate(operands):
            if argument == "--output":
                if index + 1 == len(operands):
                    return UNKNOWN
                outputs.append(operands[index + 1])
            elif argument.startswith("--output="):
                outputs.append(argument.partition("=")[2])
        if outputs:
            return destructive(outputs, root, cwd, settings)
        return UNKNOWN if any(arg in {"--ext-diff", "--textconv"} for arg in operands) else READ
    return UNKNOWN


def git_write_arguments(verb: str, args: list[str]) -> bool:
    if verb == "init":
        return all(arg in {"-q", "--quiet"} for arg in args)
    if verb == "add":
        return all(not arg.startswith("-") or arg in {"--", "-A", "--all", "-u", "--update"}
                   for arg in args)
    remaining = iter(args)
    for arg in remaining:
        if arg in {"-m", "--message"}:
            if next(remaining, None) is None:
                return False
        elif not (arg == "--no-gpg-sign" or arg.startswith("--message=")):
            return False
    return any(arg in {"-m", "--message"} or arg.startswith("--message=") for arg in args)
