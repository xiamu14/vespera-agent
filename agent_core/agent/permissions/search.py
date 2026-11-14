"""Route native filesystem searches through checked reads, never command execution."""
from copy import deepcopy
from dataclasses import replace
from inspect import isawaitable

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.toolsets import WrapperToolset
from pydantic_ai.workspaces import ReadOnlyWorkspace, Workspace, WrapperWorkspace


_SEARCH_KINDS = frozenset({"file_system.grep", "file_system.list_files"})
_SEARCH_DESCRIPTION = (
    " Search uses checked filesystem reads without commands. Ignore files (.gitignore) are not "
    "applied; use glob to narrow the search. Only context=0 and file_type=null are supported; "
    "use glob (for example '*.py') instead of file_type, and read_file near a match for context."
)


def _validate_search(arguments):
    if arguments.get("context", 0) != 0 or arguments.get("file_type") is not None:
        raise ModelRetry(
            "Filesystem-only grep supports context=0 and file_type=null. "
            "Use glob for file types and read_file for surrounding lines."
        )


class SearchWorkspace(ReadOnlyWorkspace):
    """Native fallback plus directory filtering using the existing backend's policy.

    CheckedRead and cancellation/lock ownership remain in RestrictedBackend.read_bytes.
    A directory approval is not an approval to read protected descendants.
    """

    def __init__(self, workspace):
        from agent.permissions.workspace import RestrictedBackend

        super().__init__(workspace)
        backend = workspace
        while isinstance(backend, Workspace):
            backend = backend.wrapped if isinstance(backend, WrapperWorkspace) else backend.backend
        if not isinstance(backend, RestrictedBackend):
            raise TypeError("FilesystemSearch requires a RestrictedBackend workspace")
        self._restricted = backend

    async def list_dir(self, path):
        entries = await super().list_dir(path)
        visible = []
        for entry in entries:
            try:
                self._restricted.check(entry.path)
            except PermissionError:
                continue
            visible.append(entry)
        return visible


class SearchToolset(WrapperToolset):
    async def get_tools(self, ctx):
        tools = await super().get_tools(ctx)
        result = dict(tools)
        for name, tool in tools.items():
            kind = tool.tool_def.tool_kind
            if kind not in _SEARCH_KINDS:
                continue
            schema = deepcopy(tool.tool_def.parameters_json_schema)
            description = (tool.tool_def.description or "") + _SEARCH_DESCRIPTION
            if kind == "file_system.grep":
                properties = schema["properties"]
                properties["context"] = {"type": "integer", "const": 0, "default": 0}
                properties["file_type"] = {"type": "null", "default": None}
                previous = tool.args_validator_func

                async def validate(ctx, _previous=previous, **arguments):
                    _validate_search(arguments)
                    if _previous is not None:
                        checked = _previous(ctx, **arguments)
                        if isawaitable(checked):
                            await checked

                tool = replace(tool, args_validator_func=validate)
            result[name] = replace(tool, tool_def=replace(
                tool.tool_def, parameters_json_schema=schema, description=description,
            ))
        return result

    async def call_tool(self, name, tool_args, ctx, tool):
        if tool.tool_def.tool_kind in _SEARCH_KINDS:
            # Validate here too for direct toolset callers; normal agent validation runs
            # args_validator_func before execution permissions and approval handling.
            if tool.tool_def.tool_kind == "file_system.grep":
                _validate_search(tool_args)
            ctx = replace(ctx, workspace=SearchWorkspace(ctx.workspace))
        return await super().call_tool(name, tool_args, ctx, tool)


class FilesystemSearch(AbstractCapability):
    """Use native FileSystem grep/list_files without granting a shell channel."""

    id = "filesystem_search"

    def get_wrapper_toolset(self, toolset):
        return SearchToolset(toolset)
