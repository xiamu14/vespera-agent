from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent.permissions.config import Settings
from agent.permissions.rules import decide
from agent.permissions.shell_rules import classify_shell


class BranchPermissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def test_readonly_branch_forms_are_allowed_by_default_shell_policy(self):
        for command in ("git branch", "git branch --list", "git branch --show-current"):
            with self.subTest(command=command):
                decision = decide(
                    Settings(), "shell", None, {"command": command}, root=self.root,
                    readonly=(), forbidden=(), command_arg="command", allow_sandboxed_shell=True,
                )
                self.assertEqual(decision.action, "allow")
                self.assertTrue(decision.shell_operation.supported)
                self.assertFalse(decision.shell_operation.git_write)

    def test_branch_mutations_and_mixed_options_keep_execution_restrictions(self):
        for arguments in (
            "new-name", "-d old-name", "-D old-name", "-m renamed", "-M renamed",
            "--show-current -d old-name", "--list -D old-name", "--unknown-option",
        ):
            with self.subTest(arguments=arguments):
                operation = classify_shell(f"git branch {arguments}", self.root, Settings())
                self.assertEqual(operation.action, "deny")
                self.assertFalse(operation.supported)
                self.assertEqual(operation.targets, (self.root,))

    def test_branch_read_cannot_escape_workspace(self):
        operation = classify_shell(
            'git -C .. branch --show-current', self.root, Settings(),
        )
        self.assertEqual(operation.action, "ask")

    def test_compound_repository_inspection_is_readonly(self):
        repository = self.root / "ani"
        repository.mkdir()
        command = (
            f'cd "{repository}" && git rev-parse --show-toplevel && git status --short '
            '&& echo "---branch---" && git branch --show-current && git log --oneline -5'
        )
        operation = classify_shell(command, self.root, Settings())
        self.assertEqual(operation.action, "allow")
        self.assertTrue(operation.supported)
        self.assertFalse(operation.git_write)
        self.assertEqual(operation.targets, ())
