"""Bind trusted command declarations to explicit project output directories."""
from dataclasses import replace
from pathlib import Path

from integrations.protected_paths import is_protected_path


def scoped_operation(operation, command, root, settings, forbidden):
    matches = [scope for scope in settings.shell_scopes if scope.command == command and (
        scope.workspace is None or Path(scope.workspace).is_absolute() and Path(scope.workspace).resolve() == root
    )]
    if not matches or operation.action == "deny" or not operation.supported:
        return operation
    if len(matches) != 1:
        return replace(operation, action="deny", reason="命令匹配多个写入范围，无法确定执行能力", supported=False)
    if not operation.needs_classification:
        return operation
    targets = []
    for spelling in matches[0].write_paths:
        path = Path(spelling)
        target = root / path
        if (not spelling or path.is_absolute() or ".." in path.parts or any(c in spelling for c in "*?[")
                or target.resolve() != target or target == root or not target.is_relative_to(root)
                or is_protected_path(target.relative_to(root).as_posix(), settings.protected_paths)
                or any(target == item or target.is_relative_to(item) or item.is_relative_to(target) for item in forbidden)):
            return replace(operation, action="deny", reason="声明的命令写入范围不安全或越过保护边界", supported=False)
        targets.append(target)
    targets = creation_targets(targets, root)
    if any(target == item or target.is_relative_to(item) or item.is_relative_to(target)
           for target in targets for item in forbidden):
        return replace(operation, action="deny", reason="创建输出目录所需范围与硬保护路径重叠", supported=False)
    return replace(operation, action="ask", reason="确认声明的项目写入范围；其余项目路径只读，网络仍受限",
                   targets=targets, needs_classification=False)


def creation_targets(targets, root):
    effective = []
    for path in targets:
        target = path
        while target.parent != root and not target.parent.exists():
            target = target.parent
        effective.append(target)
    return tuple(dict.fromkeys(effective))


def path_state(path):
    """Include parent identity so replacing an ancestor invalidates an approval."""
    result = []
    for index, item in enumerate((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            state = None
        else:
            state = (info.st_dev, info.st_ino, info.st_mode)
            if index == 0:
                state += (info.st_mtime_ns, info.st_ctime_ns, info.st_size)
        result.append((str(item), state))
    return result
