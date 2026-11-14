"""Native FileSystem tools and mock models against temporary, checked workspaces."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent
from pydantic_ai.capabilities import HandleDeferredToolCalls
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.workspaces import Workspace, WorkspaceReadOnlyError
from pydantic_ai_harness import FileSystem

from agent.permissions.policy import CURRENT_GRANT, ExecutionPermissions
from agent.permissions.rules import Decision
from agent.permissions.search import FilesystemSearch, SearchWorkspace
from agent.permissions.workspace import RestrictedBackend


class SearchPermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="ani-search-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.config = self.base / "permissions.json"
        self.config.write_text("{}")
        self.interaction = SimpleNamespace(approve=AsyncMock(return_value=(True, False)))
        patches = [
            patch("agent.permissions.policy.config_path", return_value=self.config),
            patch("agent.permissions.policy.config_home", return_value=self.base / "config"),
            patch("agent.permissions.policy.redact", side_effect=lambda value: json.dumps(value)),
            patch.object(ExecutionPermissions, "refresh_readonly", lambda policy: setattr(policy, "readonly", ())),
        ]
        for patched in patches:
            patched.start()
            self.addCleanup(patched.stop)
        self.policy = ExecutionPermissions(
            self.root, self.interaction, self.base / "storage" / "db.sqlite", self.base / "mcp.json",
        )
        self.backend = RestrictedBackend(self.root, self.policy)
        self.addCleanup(self.backend.close)
        self.workspace = Workspace(self.backend)
        self.run_process = patch.object(self.backend, "run", side_effect=AssertionError("search launched a process"))
        self.process = self.run_process.start()
        self.addCleanup(self.run_process.stop)
        (self.root / "ordinary.py").write_text("Alpha marker\nsecond marker\n")
        (self.root / "ordinary.txt").write_text("ALPHA marker\n")
        self.secret = self.root / "credentials.txt"
        self.secret.write_text("PRIVATE_SEARCH_MARKER\n")
        self.schemas = []

    async def call(self, name="grep", arguments=None, *, read_only=False):
        self.policy.role_read_only = read_only
        arguments = arguments or {"pattern": "marker"}
        calls = 0

        def respond(messages, info):
            nonlocal calls
            self.schemas.extend(info.function_tools)
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart(name, arguments, tool_call_id="search-call")])
            return ModelResponse(parts=[TextPart("complete")])

        agent = Agent(FunctionModel(respond), capabilities=[
            self.policy, HandleDeferredToolCalls(handler=self.policy.handle), FilesystemSearch(),
            FileSystem(read_only=read_only, tools=["grep", "list_files", "read_file"]),
        ])
        result = await agent.run("inspect temporary files", conversation_id="search-test", workspace=self.workspace)
        return [part for message in result.all_messages() for part in message.parts
                if isinstance(part, ToolReturnPart)]

    async def test_readonly_roles_can_grep_without_processes(self):
        for role in ("reviewer", "architect"):
            with self.subTest(role=role):
                parts = await self.call(read_only=True)
                text = str(parts[0].content)
                self.assertIn("ordinary.py:1:Alpha marker", text)
                self.assertNotIn("PRIVATE_SEARCH_MARKER", text)
        self.process.assert_not_called()
        self.interaction.approve.assert_not_awaited()

    async def test_approved_exact_protected_file_uses_checked_read(self):
        original = self.backend.read_bytes
        reads = []

        async def read(path):
            reads.append((Path(path), CURRENT_GRANT.get()))
            return await original(path)

        with patch.object(self.backend, "read_bytes", side_effect=read):
            parts = await self.call(arguments={"pattern": "PRIVATE", "path": self.secret.name})
        self.assertIn("credentials.txt:1:PRIVATE_SEARCH_MARKER", str(parts[0].content))
        self.interaction.approve.assert_awaited_once()
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0][0], self.secret)
        self.assertEqual(reads[0][1].target, self.secret)
        self.assertFalse(reads[0][1].command)
        self.process.assert_not_called()

    async def test_denied_protected_grep_never_reads(self):
        self.interaction.approve.return_value = (False, False)
        with patch.object(self.backend, "read_bytes", wraps=self.backend.read_bytes) as reads:
            parts = await self.call(arguments={"pattern": "PRIVATE", "path": self.secret.name})
        self.assertNotIn("PRIVATE_SEARCH_MARKER", str(parts))
        reads.assert_not_called()
        self.interaction.approve.assert_awaited_once()

    async def test_directory_search_filters_protected_files_directories_and_aliases(self):
        protected = self.root / "credentials-dir"
        protected.mkdir()
        (protected / "nested.txt").write_text("PRIVATE_SEARCH_MARKER")
        (self.root / "alias.txt").symlink_to(self.secret)
        with patch.object(self.backend, "read_bytes", wraps=self.backend.read_bytes) as reads:
            parts = await self.call(arguments={"pattern": "PRIVATE|marker"})
        content = str(parts[0].content)
        self.assertIn("ordinary.py", content)
        self.assertNotIn("credentials", content)
        self.assertNotIn("alias.txt", content)
        self.assertNotIn("PRIVATE_SEARCH_MARKER", content)
        self.assertTrue(all("credentials" not in str(call.args[0]) for call in reads.call_args_list))
        self.interaction.approve.assert_not_awaited()

    async def test_list_files_uses_fallback_and_filters_protected_entries(self):
        parts = await self.call(name="list_files", arguments={"path": "."}, read_only=True)
        self.assertIn("ordinary.py", str(parts[0].content))
        self.assertNotIn("credentials.txt", str(parts[0].content))
        self.process.assert_not_called()

    async def test_literal_ignore_case_and_glob_remain_native(self):
        (self.root / "special.py").write_text("A+B marker\n")
        parts = await self.call(arguments={
            "pattern": "a+b", "literal": True, "ignore_case": True, "glob": "*.py",
        })
        self.assertIn("special.py:1:A+B marker", str(parts[0].content))
        self.assertNotIn("ordinary", str(parts[0].content))

    async def test_unsupported_options_rejected_before_approval_or_read(self):
        for option in ({"context": 1}, {"file_type": "py"}):
            with self.subTest(option=option):
                with patch.object(self.backend, "read_bytes", wraps=self.backend.read_bytes) as reads:
                    parts = await self.call(arguments={
                        "pattern": "PRIVATE", "path": self.secret.name, **option,
                    })
                self.assertEqual(parts[0].outcome, "retried")
                self.assertIn("Filesystem-only grep", str(parts[0].content))
                reads.assert_not_called()
        self.interaction.approve.assert_not_awaited()
        schema = next(tool for tool in self.schemas if tool.name == "grep")
        self.assertEqual(schema.parameters_json_schema["properties"]["context"]["const"], 0)
        self.assertEqual(schema.parameters_json_schema["properties"]["file_type"]["type"], "null")
        self.assertIn("context=0", schema.description)
        self.assertIn(".gitignore", schema.description)

    async def test_direct_native_search_requires_exact_read_grant(self):
        filesystem = FileSystem(tools=["grep"]).get_toolset()
        checked = SearchWorkspace(self.workspace)
        without = await filesystem.grep("PRIVATE", path=self.secret.name, workspace=checked)
        self.assertNotIn("PRIVATE_SEARCH_MARKER", without)
        other = self.root / "credentials-other.txt"
        other.write_text("PRIVATE_OTHER_MARKER")
        token = CURRENT_GRANT.set(Decision("ask", "test", str(other), target=other, forced=True))
        try:
            wrong = await filesystem.grep("PRIVATE", path=self.secret.name, workspace=checked)
            self.assertNotIn("PRIVATE_SEARCH_MARKER", wrong)
        finally:
            CURRENT_GRANT.reset(token)

    async def test_directory_grant_does_not_approve_protected_descendants(self):
        protected = self.root / "credentials-dir"
        protected.mkdir()
        (protected / "credentials-child.txt").write_text("PRIVATE_DESCENDANT_MARKER")
        parts = await self.call(arguments={"pattern": "PRIVATE", "path": protected.name})
        self.assertNotIn("PRIVATE_DESCENDANT_MARKER", str(parts))
        self.interaction.approve.assert_awaited_once()

    async def test_search_workspace_refuses_run_and_write(self):
        checked = SearchWorkspace(self.workspace)
        with self.assertRaises(WorkspaceReadOnlyError):
            await checked.run(["rg", "marker"])
        with self.assertRaises(WorkspaceReadOnlyError):
            await checked.write_bytes(str(self.root / "new.txt"), b"write")
        self.assertFalse((self.root / "new.txt").exists())

    async def test_target_changed_during_approval_is_rejected(self):
        async def changed(*args, **kwargs):
            self.secret.write_text("REPLACEMENT_PRIVATE_MARKER")
            return True, False

        self.interaction.approve.side_effect = changed
        with patch.object(self.backend, "read_bytes", wraps=self.backend.read_bytes) as reads:
            parts = await self.call(arguments={"pattern": "PRIVATE", "path": self.secret.name})
        self.assertNotIn("REPLACEMENT_PRIVATE_MARKER", str(parts))
        reads.assert_not_called()
