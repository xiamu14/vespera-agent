from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from agent.permissions.config import Rule, Settings
from agent.permissions.rules import decide
from agent.permissions.shell_rules import classify_shell
from integrations.model_data import model_messages
from integrations.network import NetworkDenied
from integrations.protected_paths import is_protected_path
from integrations.shell_http import payload_file


class ProtectedPathTests(unittest.TestCase):
    def test_defaults_cover_nested_resources_and_protected_ancestors(self):
        patterns = Settings().protected_paths
        for path in (
            ".git/config", "repo/.git/config", "repo/.git/hooks/pre-commit",
            "repo/.git", "repo/.env/config", "credentials-dir/notes.txt",
            "src/signing.key/child", "nested/credentials.txt",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_protected_path(path, patterns))
        for path in ("src/main.py", "repo/.github/workflows/test.yml", "docs/git-notes.md"):
            with self.subTest(path=path):
                self.assertFalse(is_protected_path(path, patterns))

    def test_path_patterns_remain_scoped_and_cover_descendants(self):
        self.assertTrue(is_protected_path("private/key/file", ["private/key"]))
        self.assertTrue(is_protected_path("private/key", ["private/**"]))
        self.assertFalse(is_protected_path("src/private/key", ["private/**"]))
        self.assertTrue(is_protected_path("src/private/key", ["**/private/**"]))
        self.assertTrue(is_protected_path("/tmp/repo/.git/config", [".git"]))

    def test_file_shell_upload_and_model_share_nested_git_protection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "repo/.git/config"
            target.parent.mkdir(parents=True)
            target.write_text("LOCAL_ONLY_MARKER")
            settings = Settings(rules=[Rule(tool="*", action="allow")])
            for kind in ("read_file", "write_file"):
                with self.subTest(kind=kind):
                    decision = decide(
                        settings, kind, f"file_system.{kind}", {"path": "repo/.git/config"},
                        root=root, readonly=(), forbidden=(), command_arg=None,
                    )
                    self.assertEqual(decision.action, "ask")
                    self.assertTrue(decision.forced)
            operation = classify_shell("rm repo/.git/config", root, settings)
            self.assertEqual(operation.action, "deny")
            with self.assertRaisesRegex(NetworkDenied, "受保护"):
                payload_file("repo/.git/config", root, protected=settings.protected_paths)
            for tool in ("read_file", "grep", "search_files"):
                messages = [
                    ModelResponse(parts=[ToolCallPart(tool, {"path": "repo/.git/config"}, "read-1")]),
                    ModelRequest(parts=[ToolReturnPart(tool, "LOCAL_ONLY_MARKER", "read-1")]),
                ]
                projected, restricted = model_messages(messages, root, settings.protected_paths)
                self.assertEqual(restricted, {"read-1"})
                self.assertNotIn("LOCAL_ONLY_MARKER", str(projected))
                self.assertIn("LOCAL_ONLY_MARKER", str(messages))

    def test_custom_rules_do_not_block_unrelated_uploads(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "notes.txt").write_text("ordinary project notes")
            self.assertEqual(
                payload_file("notes.txt", root, protected=["private/**"]),
                b"ordinary project notes",
            )
