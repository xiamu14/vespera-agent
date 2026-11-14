"""Tokenize the supported Shell subset while retaining quoted operator identity."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Token:
    value: str
    operator: bool = False
    expands: bool = False
    variable: bool = False
    source: str = ""


CONTROL = frozenset({"&&", "||", ";", "|", "&"})
COMMAND_PREFIXES = frozenset({"do", "then", "else", "elif", "if", "while", "until", "!"})
OUTPUT_FD_COPIES = frozenset({"2>&1", "1>&2"})
VARIABLE = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*|\{[A-Za-z_][A-Za-z_0-9]*\})")


def command_after_prefixes(tokens):
    index = 0
    while index < len(tokens) and tokens[index].source in COMMAND_PREFIXES:
        index += 1
    return tokens[index:]


def parse_shell(command: str):
    tokens = []
    word = []
    started = False
    expands = False
    variable = False
    start = 0
    quote = None
    escaped = False
    issue = None

    def flush():
        nonlocal started, expands, variable
        if started:
            tokens.append(Token("".join(word), expands=expands, variable=variable, source=command[start:index]))
            word.clear()
            started = False
            expands = False
            variable = False

    index = 0
    while index < len(command):
        char = command[index]
        if not started:
            start = index
        if not (started or quote or escaped) and char in "12":
            copy = command[index:index + 4]
            end = index + len(copy)
            if copy in OUTPUT_FD_COPIES and (end == len(command) or command[end] in " \t\r\n;&|<>()"):
                tokens.append(Token(copy, operator=True))
                index = end
                continue
        if char == "$" and not escaped and quote != "'":
            reference = VARIABLE.match(command, index)
            if reference:
                started = variable = True
                word.append(reference[0])
                index = reference.end()
                continue
        if escaped:
            if char in "\r\n":
                issue = issue or "暂不支持反斜杠续行"
            if quote == '"' and char not in '$`"\\\n':
                word.append("\\")
            word.append(char)
            escaped = False
        elif char == "\\" and quote != "'":
            started = True
            escaped = True
        elif quote:
            if char == quote:
                quote = None
            else:
                word.append(char)
                if quote == '"' and char in "$`":
                    issue = issue or "包含变量展开或命令替换，暂不支持静态分类"
        elif char in "'\"":
            started = True
            quote = char
        elif char in "$`":
            started = True
            issue = issue or "包含变量展开或命令替换，暂不支持静态分类"
            word.append(char)
        elif char == "#" and not started:
            # Comments end at a newline, which remains outside this supported grammar.
            end = command.find("\n", index)
            index = len(command) if end < 0 else end - 1
        elif char in " \t\r\n":
            flush()
            if char in "\r\n":
                issue = issue or "暂不支持多行 Shell 命令"
        elif char in ";&|<>()":
            flush()
            operator = char
            while index + 1 < len(command) and command[index + 1] in ";&|<>()":
                index += 1
                operator += command[index]
            tokens.append(Token(operator, True))
        else:
            expands = expands or char in "*?[{" or (char == "~" and not started)
            started = True
            word.append(char)
        index += 1
    flush()
    if quote or escaped:
        issue = issue or "Shell 引号或转义不完整"
    segments = [[]]
    joins = []
    for token in tokens:
        if token.operator and token.value in CONTROL:
            joins.append(token.value)
            segments.append([])
        else:
            segments[-1].append(token)
    if segments[-1] == [] and joins and joins[-1] == ";":
        segments.pop()
        joins.pop()
    if "&" in joins:
        issue = issue or "后台执行 & 无法绑定完整的执行生命周期"
    if any(not segment for segment in segments):
        issue = issue or "连接符前后缺少命令"
    return segments, joins, issue
