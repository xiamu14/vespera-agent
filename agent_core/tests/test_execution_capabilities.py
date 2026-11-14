"""Approval scopes bind the capabilities the executor can actually grant."""
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai.exceptions import SkipToolExecution
from pydantic_ai.messages import ToolCallPart
from agent.interaction import Interaction
from agent.permissions.config import Settings
from agent.permissions.policy import CURRENT_GRANT, ExecutionPermissions
from agent.permissions.rules import decide
from agent.permissions.workspace import RestrictedBackend
from integrations.git_push import run_git


SHELL = SimpleNamespace(tool_kind="shell", metadata={"code_arg_language": "shell", "code_arg_name": "command"})


class ExecutionCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="ani-capabilities-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.config = self.base / "permissions.json"
        self.config.write_text("{}")
        self.enterContext(patch("agent.permissions.policy.config_path", return_value=self.config))
        self.policy = ExecutionPermissions(self.root, Interaction(), self.base / "state/db.sqlite", self.base / "mcp.json")
        self.policy.allow_sandboxed_shell = True
        self.backend = RestrictedBackend(self.root, self.policy)
        self.addCleanup(self.backend.close)

    def configure(self, command, paths, **options):
        self.config.write_text(json.dumps({"shell_scopes": [{"command": command, "write_paths": paths, **options}]}))

    def evaluate(self, command):
        return self.policy.evaluate("shell", SHELL, {"command": command})

    def test_exact_trusted_scope_is_an_explicit_approval(self):
        (self.root / ".cache").mkdir()
        self.configure("npm run build", ["dist", ".cache/build"])
        decision, _ = self.evaluate("npm run build")
        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.shell_operation.targets, (self.root / "dist", self.root / ".cache/build"))
        self.assertFalse(decision.shell_operation.needs_classification)
        unrelated, _ = self.evaluate("npm run build && echo changed")
        self.assertEqual(unrelated.shell_operation.targets, ())
        self.configure("npm run build", ["dist"], workspace=str(self.base / "other"))
        self.assertEqual(self.evaluate("npm run build")[0].shell_operation.targets, ())

    def test_missing_output_parents_are_included_in_visible_authorization(self):
        self.configure("python task.py", ["build/output/results"])
        operation = self.evaluate("python task.py")[0].shell_operation
        self.assertEqual(operation.targets, (self.root / "build",))
        operation = self.evaluate("mkdir -p generated/nested")[0].shell_operation
        self.assertEqual(operation.targets, (self.root / "generated",))

    def test_scopes_cannot_override_hard_or_protected_boundaries(self):
        for path in (".", "..", "../outside", "/tmp/outside", ".git", ".env", "nested/.git", "build/*"):
            with self.subTest(path=path):
                self.configure("npm run build", [path])
                self.assertEqual(self.evaluate("npm run build")[0].action, "deny")
        (self.root / "alias").symlink_to(self.base, target_is_directory=True)
        self.configure("npm run build", ["alias/output"])
        self.assertEqual(self.evaluate("npm run build")[0].action, "deny")
        self.configure("rm -rf .", ["dist"])
        self.assertEqual(self.evaluate("rm -rf .")[0].action, "deny")
        self.configure("git reset --hard", ["dist"])
        self.assertEqual(self.evaluate("git reset --hard")[0].action, "deny")

    def test_parent_identity_is_bound_without_unrelated_sibling_timestamps(self):
        directory = self.root / "build"
        directory.mkdir()
        self.configure("python task.py", ["build/output"])
        _, before = self.evaluate("python task.py")
        (directory / "unrelated").touch()
        self.assertEqual(self.evaluate("python task.py")[1], before)
        directory.rename(self.root / "old-build")
        directory.mkdir()
        self.assertNotEqual(self.evaluate("python task.py")[1], before)

    def test_all_matching_rules_must_allow_memory(self):
        settings = Settings(rules=[
            {"tool": "read_file", "action": "ask", "remember": True},
            {"tool": "read_file", "action": "allow", "remember": False},
        ])
        decision = decide(settings, "read_file", "file_system.read_file", {"path": "public.txt"},
                          root=self.root, readonly=(), forbidden=(), command_arg=None)
        self.assertTrue(decision.forced)

    async def test_unavailable_or_unsupported_execution_does_not_request_approval(self):
        self.policy.interaction.approve = AsyncMock()
        ctx = SimpleNamespace(run_id="run", conversation_id="conversation", tool_call_approved=False)
        for command, config in (("git reset --hard", {}), ("python task.py", {"sandbox": False})):
            with self.subTest(command=command):
                self.config.write_text(json.dumps(config))
                with self.assertRaises(SkipToolExecution):
                    await self.policy.before_tool_execute(ctx, call=ToolCallPart("shell", {"command": command}),
                                                          tool_def=SHELL, args={"command": command})
                self.assertEqual(self.policy.pending, {})
        self.policy.interaction.approve.assert_not_awaited()

    async def repository(self, name):
        directory = self.root / name
        directory.mkdir()
        await run_git(directory, "init", "--template=")
        await run_git(directory, "config", "user.name", "Capability Test")
        await run_git(directory, "config", "user.email", "test@example.invalid")
        (directory / "file.txt").write_text("ordinary")
        return directory

    async def test_nested_repository_is_bound_and_other_metadata_stays_protected(self):
        repo = await self.repository("repo")
        other = await self.repository("other")
        decision, fingerprint = self.evaluate("git -C repo add .")
        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.git_scopes[0].git_dir, repo / ".git")
        profile = self.backend.profile(git_write=True, write_targets=(), git_scopes=decision.git_scopes)
        self.assertIn("require-all", profile)
        self.assertIn(str(repo / ".git"), profile)
        self.assertNotIn(f'(subpath "{other / ".git"}")', profile)
        (repo / ".git/config").write_text("[user]\nname=changed\n")
        self.assertNotEqual(self.evaluate("git -C repo add .")[1], fingerprint)

    async def test_linked_worktree_and_initialization_bind_specific_metadata(self):
        repo = await self.repository("source")
        await run_git(repo, "add", ".")
        await run_git(repo, "commit", "-m", "baseline")
        worktree = self.root / "linked"
        await run_git(repo, "worktree", "add", "--detach", str(worktree))
        decision, _ = self.evaluate("git -C linked add .")
        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.git_scopes[0].common_dir, repo / ".git")
        initial, _ = self.evaluate("git init --quiet")
        self.assertEqual(initial.action, "allow")
        self.assertEqual(initial.git_scopes[0].git_dir, self.root / ".git")

    async def test_fake_external_git_pointer_is_rejected(self):
        repo = await self.repository("source")
        fake = self.root / "fake"
        fake.mkdir()
        (fake / ".git").write_text(f"gitdir: {repo / '.git'}\n")
        decision, _ = self.evaluate("git -C fake add .")
        self.assertEqual(decision.action, "deny")

    async def test_git_identity_is_rechecked_before_process_launch(self):
        repo = await self.repository("repo")
        decision, _ = self.evaluate("git -C repo add .")
        (repo / ".git/config").write_text("[user]\nname=changed\n")
        token = CURRENT_GRANT.set(decision)
        try:
            with patch("pydantic_ai.workspaces.LocalWorkspaceBackend.run", new_callable=AsyncMock) as launch:
                with self.assertRaises(PermissionError):
                    await self.backend.run("git -C repo add .", shell=True)
                launch.assert_not_awaited()
        finally:
            CURRENT_GRANT.reset(token)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("sandbox-exec"), "requires macOS sandbox-exec")
    async def test_kernel_consumes_build_scope_and_nested_git_scope(self):
        command = "sh -c 'mkdir -p dist; printf built > dist/result; printf forbidden > unrelated'"
        self.configure(command, ["dist"])
        decision, _ = self.evaluate(command)
        self.assertEqual(decision.action, "ask")
        token = CURRENT_GRANT.set(decision)
        try:
            result = await self.backend.run(command, shell=True)
        finally:
            CURRENT_GRANT.reset(token)
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual((self.root / "dist/result").read_text(), "built")
        self.assertFalse((self.root / "unrelated").exists())
        repo = await self.repository("repo")
        decision, _ = self.evaluate("git -C repo add . && git -C repo commit -m checkpoint")
        self.assertEqual(decision.action, "allow")
        token = CURRENT_GRANT.set(decision)
        try:
            result = await self.backend.run(decision.subject, shell=True)
        finally:
            CURRENT_GRANT.reset(token)
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual((await run_git(repo, "log", "-1", "--format=%s")).strip(), b"checkpoint")
        other = await self.repository("other")
        probe = f"printf denied > '{other / '.git/config'}'"
        decision, _ = self.evaluate("git -C repo add .")
        token = CURRENT_GRANT.set(decision)
        try:
            denied = await self.backend.run(["/bin/sh", "-c", probe])
        finally:
            CURRENT_GRANT.reset(token)
        self.assertNotEqual(denied.exit_code, 0)
        self.assertNotEqual((other / ".git/config").read_text(), "denied")
        worktree = self.base / "linked"
        await run_git(repo, "worktree", "add", "--detach", str(worktree))
        linked_policy = ExecutionPermissions(worktree, Interaction(), self.base / "state/db.sqlite", self.base / "mcp.json")
        linked_policy.allow_sandboxed_shell = True
        linked_backend = RestrictedBackend(worktree, linked_policy)
        self.addCleanup(linked_backend.close)
        (worktree / "file.txt").write_text("linked modification")
        command = "git add . && git commit -m linked-checkpoint"
        decision, _ = linked_policy.evaluate("shell", SHELL, {"command": command})
        self.assertEqual(decision.action, "allow")
        token = CURRENT_GRANT.set(decision)
        try:
            result = await linked_backend.run(command, shell=True)
        finally:
            CURRENT_GRANT.reset(token)
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual((await run_git(worktree, "log", "-1", "--format=%s")).strip(), b"linked-checkpoint")
        self.assertEqual((await run_git(repo, "log", "-1", "--format=%s")).strip(), b"checkpoint")
