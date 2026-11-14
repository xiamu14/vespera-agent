"""Match protected resources consistently across file, process, and outbound boundaries."""
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Iterable


def is_protected_path(path: str, patterns: Iterable[str]) -> bool:
    resource = PurePosixPath(path)
    lineage = (resource, *resource.parents)
    prefixes = tuple(str(parent) for parent in lineage if str(parent) not in {".", "/"})
    components = tuple(part for part in resource.parts if part != "/")
    return any(
        any(fnmatchcase(prefix, pattern) for prefix in prefixes)
        or ("/" not in pattern and any(fnmatchcase(part, pattern) for part in components))
        for pattern in patterns
    )
