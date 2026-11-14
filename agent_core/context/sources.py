"""Extract a bounded project overview from explicit information sources."""

import json
import re
import tomllib
from pathlib import Path

MANIFESTS = {"pyproject.toml", "package.json", "go.mod", "Cargo.toml", "requirements.txt"}
DOCUMENTS = {"README.md", "README.rst", "README", "ARCHITECTURE.md", "architecture.md"}
EXCLUDED = {
    ".git", ".venv", "venv", "env", "node_modules", "vendor", "dist", "build", "target",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".next", ".nuxt", ".cache", ".agent-memory", ".logfire", "coverage", "htmlcov",
}
FRAMEWORKS = {
    "react", "vue", "next", "express", "typescript", "svelte", "vite", "django", "flask",
    "fastapi", "pydantic-ai", "pydantic-ai-harness", "textual", "sqlalchemy", "axum", "actix-web",
}


def visible_name(name: str) -> bool:
    return name not in EXCLUDED and not name.startswith(".") and not name.endswith(
        (".lock", ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pyc", ".log")
    )


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value, limit=350) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def _frameworks(names) -> str:
    return ", ".join(sorted({str(name).lower().replace("_", "-") for name in names} & FRAMEWORKS))


def _document(text: str) -> str:
    parts, section, fence = [], "intro", False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            if section == "architecture":
                parts.append(line)
            continue
        if line.startswith("#"):
            section = "intro" if line.startswith("# ") and not parts else "architecture" if re.search(
                r"architecture|structure|layout|overview|架构|结构|模块|职责|概述", line, re.I
            ) else "other"
        if section in {"intro", "architecture"} or (line.startswith("# ") and not parts):
            parts.append(line)
        if sum(len(part) for part in parts) >= 1800:
            break
    return "\n".join(parts)[:1800]


def summarize(path: Path, text: str, truncated: bool) -> str:
    name = path.name
    if name == "AGENTS.md":
        result = text[:2600]
    elif name in DOCUMENTS:
        result = _document(text)
    else:
        result = _manifest(name, text)
    suffix = "\n[来源已截断；任务需要时读取原文件]" if truncated or name == "AGENTS.md" and len(text) > 2600 else ""
    return result + suffix


def _manifest(name: str, text: str) -> str:
    try:
        if name == "package.json":
            data = _mapping(json.loads(text))
            deps = {**_mapping(data.get("dependencies")), **_mapping(data.get("devDependencies"))}
            return "\n".join(filter(None, (
                "Node.js " + _text(data.get("name", "")), _text(data.get("description", "")),
                "Frameworks: " + _frameworks(deps),
                "Scripts: " + ", ".join(sorted(_mapping(data.get("scripts")))[:12]),
            )))
        if name in {"pyproject.toml", "Cargo.toml"}:
            data = tomllib.loads(text)
            project = _mapping(data.get("project" if name == "pyproject.toml" else "package"))
            deps = project.get("dependencies", []) if name == "pyproject.toml" else data.get("dependencies", {})
            names = []
            if isinstance(deps, (list, dict)):
                names = [match.group() for dep in deps if isinstance(dep, str)
                         if (match := re.match(r"[A-Za-z0-9_.-]+", dep))]
            language = "Python" if name == "pyproject.toml" else "Rust"
            workspace = _mapping(data.get("workspace"))
            return "\n".join(filter(None, (
                language + " " + _text(project.get("name", "")), _text(project.get("description", "")),
                "Runtime: " + _text(project.get("requires-python", project.get("edition", ""))),
                "Frameworks: " + _frameworks(names),
                "Scripts: " + ", ".join(sorted(_mapping(project.get("scripts")))[:12]),
                "Workspace: " + str(workspace.get("members", []))[:500] if workspace else "",
            )))
        if name == "go.mod":
            return "Go\n" + "\n".join(line for line in text.splitlines() if line.startswith(("module ", "go ")))[:500]
        if name == "requirements.txt":
            names = [match.group() for line in text.splitlines()
                     if (match := re.match(r"[A-Za-z0-9_.-]+", line))]
            return "Python\nFrameworks: " + _frameworks(names)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError):
        return "[项目清单无法解析；任务需要时读取原文件]"
    return ""
