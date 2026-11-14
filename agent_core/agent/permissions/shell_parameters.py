"""Small, explicit argument grammars for commands with optional side effects."""
import re


def truncate_targets(args: list[str]) -> list[str] | None:
    targets = []
    size = False
    reference = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            targets.extend(args[index + 1:])
            break
        if arg in {"-s", "--size", "-r", "--reference"}:
            index += 1
            if index == len(args):
                return None
            value = args[index]
            if arg in {"-s", "--size"}:
                if not valid_size(value):
                    return None
                size = True
            else:
                if not value or value.startswith("-"):
                    return None
                reference = True
        elif arg.startswith(("--size=", "-s")):
            value = arg.partition("=")[2] if arg.startswith("--") else arg[2:]
            if not valid_size(value):
                return None
            size = True
        elif arg.startswith(("--reference=", "-r")):
            value = arg.partition("=")[2] if arg.startswith("--") else arg[2:]
            if not value or value.startswith("-"):
                return None
            reference = True
        elif arg in {"-c", "--no-create", "-o", "--io-blocks"}:
            pass
        elif arg.startswith("-"):
            return None
        else:
            targets.append(arg)
        index += 1
    return targets if targets and (size or reference) else None


def valid_size(value: str) -> bool:
    return re.fullmatch(r"[+\-<>/%]?\d+(?:[KMGTPEZYRQ](?:B|iB)?|[cbwk])?", value) is not None


def mkdir_targets(args: list[str]) -> list[str] | None:
    targets = []
    for index, arg in enumerate(args):
        if arg == "--":
            targets.extend(args[index + 1:])
            break
        if arg == "-p":
            continue
        if arg.startswith("-"):
            return None
        targets.append(arg)
    return targets or None


def git_reference_read(verb: str, args: list[str]) -> bool:
    if verb == "remote":
        return args in ([], ["-v"], ["--verbose"])
    if not args:
        return True
    if verb == "branch" and args == ["--show-current"]:
        return True
    flags = {"-l", "--list", "-n", "--no-column", "--column", "--ignore-case"}
    if verb == "branch":
        # -l means create-reflog for branch; only --list is a listing marker.
        flags = {"--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
                 "--no-color", "--color=never", "--no-column", "--column", "--ignore-case"}
    listing = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in flags:
            listing = listing or arg in {"--list", "-l"}
        elif verb == "branch" and re.fullmatch(r"-[arv]+", arg):
            pass
        elif arg.startswith(("--format=", "--sort=")) and arg.partition("=")[2]:
            pass
        elif arg == "--":
            return listing and all(not item.startswith("-") for item in args[index + 1:])
        elif not arg.startswith("-") and listing:
            pass
        else:
            return False
        index += 1
    # Tag flags such as --format alone could create a tag when a name is added;
    # require the unambiguous listing marker for every extended tag query.
    return verb == "branch" or listing


def find_read(args: list[str]) -> bool:
    flags = {"-print", "-print0", "-ls", "-prune", "-quit", "-true", "-false", "-empty",
             "-readable", "-writable", "-executable", "-nouser", "-nogroup", "-mount", "-xdev",
             "-depth", "-ignore_readdir_race", "-noignore_readdir_race"}
    values = {"-name", "-iname", "-path", "-ipath", "-regex", "-iregex", "-type", "-xtype",
              "-size", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-newer",
              "-newermt", "-user", "-group", "-uid", "-gid", "-perm", "-links", "-inum",
              "-maxdepth", "-mindepth", "-regextype"}
    index = 0
    expression = False
    depth = 0
    needs_operand = False
    while index < len(args):
        arg = args[index]
        if not expression and arg in {"-H", "-L", "-P"}:
            index += 1
            continue
        if not expression and not arg.startswith("-") and arg not in {"!", "("}:
            index += 1
            continue
        expression = True
        if arg in {"-a", "-and", "-o", "-or"}:
            if index == 0 or needs_operand:
                return False
            needs_operand = True
        elif arg in {"!", "-not", "("}:
            depth += arg == "("
            needs_operand = True
        elif arg == ")":
            if depth == 0 or needs_operand:
                return False
            depth -= 1
        elif arg in flags:
            needs_operand = False
        elif arg in values:
            index += 1
            if index == len(args) or not args[index] or args[index].startswith("-"):
                # Negative numeric comparisons are safe predicate values.
                if index == len(args) or not re.fullmatch(r"-\d+[bcwkMG]?", args[index]):
                    return False
            needs_operand = False
        else:
            return False
        index += 1
    return depth == 0 and not needs_operand


def grep_read(args: list[str]) -> bool:
    short_flags = set("EFGivwxyonqscbHhLlrzRaIUuV")
    short_values = set("efmABCdD")
    long_flags = {"--extended-regexp", "--fixed-strings", "--basic-regexp", "--ignore-case",
                  "--no-ignore-case", "--word-regexp", "--line-regexp", "--invert-match",
                  "--count", "--files-with-matches", "--files-without-match", "--only-matching",
                  "--quiet", "--silent", "--no-messages", "--line-number", "--byte-offset",
                  "--with-filename", "--no-filename", "--null", "--null-data", "--recursive",
                  "--dereference-recursive", "--text", "--binary", "--line-buffered"}
    long_values = {"--regexp", "--file", "--max-count", "--after-context", "--before-context",
                   "--context", "--include", "--exclude", "--exclude-from", "--exclude-dir",
                   "--binary-files", "--directories", "--devices", "--label", "--color", "--colour"}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return True
        if arg.startswith("--"):
            key, separator, value = arg.partition("=")
            if key in long_flags and not separator:
                pass
            elif key in long_values:
                if not separator:
                    index += 1
                    if index == len(args):
                        return False
                    value = args[index]
                if not value:
                    return False
            else:
                return False
        elif arg.startswith("-") and arg != "-":
            for offset, letter in enumerate(arg[1:], 1):
                if letter in short_values:
                    if offset == len(arg) - 1:
                        index += 1
                        if index == len(args):
                            return False
                    break
                if letter not in short_flags:
                    return False
        index += 1
    return bool(args)


def sed_script_read(script: str) -> bool:
    # Recognize a deliberately small script language, rather than blacklist e/w.
    rest = script.strip()
    while rest:
        address = r"(?:\d+|\$)(?:\s*,\s*(?:\d+|\$))?\s*!?\s*"
        rest = re.sub("^" + address, "", rest, count=1)
        if not rest:
            return False
        command, rest = rest[0], rest[1:]
        if command == "s":
            if not rest or rest[0].isalnum() or rest[0] in "\\\n":
                return False
            delimiter, rest = rest[0], rest[1:]
            for _ in range(2):
                escaped = False
                in_class = False
                for index, char in enumerate(rest):
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == "[" and _ == 0:
                        in_class = True
                    elif char == "]" and _ == 0:
                        in_class = False
                    elif char == delimiter and not in_class:
                        rest = rest[index + 1:]
                        break
                else:
                    return False
            match = re.match(r"[gp0-9]*", rest)
            rest = rest[match.end():]
        elif command not in "pPdDnNqQ=lhHgGx":
            return False
        rest = rest.lstrip(" \t")
        if rest and rest[0] not in ";\n":
            return False
        rest = rest.lstrip(";\n \t")
    return bool(script.strip())


def sed_read(args: list[str]) -> bool:
    scripts = []
    positional_script = True
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            if positional_script:
                scripts.extend(args[index + 1:index + 2])
            break
        if arg in {"-n", "--quiet", "--silent", "-E", "-r", "--regexp-extended", "--posix"}:
            pass
        elif arg in {"-e", "--expression"}:
            index += 1
            if index == len(args):
                return False
            scripts.append(args[index])
            positional_script = False
        elif arg.startswith("--expression=") or arg.startswith("-e"):
            scripts.append(arg.partition("=")[2] if arg.startswith("--") else arg[2:])
            positional_script = False
        elif arg.startswith("-") and arg != "-":
            return False
        elif positional_script:
            scripts.append(arg)
            positional_script = False
        index += 1
    return bool(scripts) and all(sed_script_read(script) for script in scripts)
