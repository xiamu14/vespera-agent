from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent.permissions.config import Settings
from agent.permissions.rules import decide
from agent.permissions.shell_rules import classify_shell


class ShellParameterTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.settings = Settings()

    def classify(self, command):
        return classify_shell(command, self.root, self.settings)

    def test_truncate_checks_every_target_and_consumes_option_values(self):
        for arguments in (
            '-s 0 first second', 'first --size=0 second', '-s0 first second',
            '--reference template first second', '-rtemplate first second',
            '-c --io-blocks --size +2K first second', '-s -2 first second',
            '-s 0 -- first second',
        ):
            with self.subTest(arguments=arguments):
                operation = self.classify('truncate ' + arguments)
                self.assertEqual(operation.action, 'ask')
                self.assertFalse(operation.needs_classification)
                self.assertEqual(operation.targets, (self.root / 'first', self.root / 'second'))

    def test_truncate_does_not_mistake_values_for_targets(self):
        for arguments in ('-s 0', '--size', '-r', '--reference=', '-s nope first',
                          '--unknown first', '-s 0 first --unknown second', 'first',
                          '--size --reference first second'):
            with self.subTest(arguments=arguments):
                operation = self.classify('truncate ' + arguments)
                self.assertEqual(operation.action, 'ask')
                self.assertTrue(operation.needs_classification)
                self.assertEqual(operation.targets, ())

    def test_truncate_protected_target_cannot_hide_before_last_file(self):
        for target in ('.', '..', '.git/config', '.env', '/tmp/outside', 'dir/*'):
            with self.subTest(target=target):
                self.assertEqual(self.classify(f'truncate -s 0 {target} ordinary').action, 'deny')
        self.assertEqual(self.classify('truncate -s 0 -- --size=0 ordinary').targets,
                         (self.root / '--size=0', self.root / 'ordinary'))

    def test_mkdir_lists_all_targets_and_rejects_protected_paths(self):
        for command in ('mkdir first second', 'mkdir -p first second', 'mkdir -p -- first second'):
            operation = self.classify(command)
            self.assertEqual(operation.action, 'ask')
            self.assertEqual(operation.targets, (self.root / 'first', self.root / 'second'))
        for path in ('.', '..', '.env', '.git/hooks', 'dir/*', '/tmp/other'):
            self.assertEqual(self.classify(f'mkdir -p {path} ordinary').action, 'deny')
        for command in ('mkdir', 'mkdir -m 755 first', 'mkdir --unknown first', 'mkdir "$target"'):
            self.assertTrue(self.classify(command).needs_classification)

    def test_precise_git_read_queries(self):
        for command in (
            'git branch', 'git branch --list', 'git branch --show-current',
            'git branch -a', 'git branch -r', 'git branch -avv',
            'git branch --list "feature/*" "main"',
            'git branch --list --format="%(refname:short)" --sort=refname',
            'git tag', 'git tag --list', 'git tag -l "v*"',
            'git tag --list --format="%(refname)" "v*"',
            'git remote', 'git remote -v', 'git remote --verbose',
        ):
            with self.subTest(command=command):
                operation = self.classify(command)
                self.assertEqual(operation.action, 'allow')
                self.assertTrue(operation.supported)
                self.assertFalse(operation.git_write)

    def test_git_mutation_unknown_and_mixed_forms_are_unsupported_denials(self):
        for arguments in (
            'branch new-name', 'branch -l new-name', 'branch -d old', 'branch -D old',
            'branch -m new', 'branch --list -D old', 'branch --show-current --unknown',
            'branch --show-current -d old', 'branch --list --unknown',
            'tag new-name', 'tag -d old', 'tag --list --delete old',
            'tag --unknown', 'remote add origin https://example.com',
            'remote -v remove origin', 'remote --unknown',
            'reset --hard', 'restore file', 'checkout main', 'rebase main',
        ):
            with self.subTest(arguments=arguments):
                operation = self.classify('git ' + arguments)
                self.assertEqual(operation.action, 'deny')
                self.assertFalse(operation.supported)

    def test_read_queries_validate_every_argument(self):
        for command in (
            'find .', 'find . -name "*.py" -type f -print',
            'find src tests -maxdepth 2 -iname "*.py" -print0',
            'find . -mtime -2 -size +1M -print',
            'grep -n pattern file', 'grep -Rin --include="*.py" -e TODO src',
            'grep --fixed-strings -- pattern file', 'grep -A 3 -B2 -f patterns file',
            "sed -n '1,20p' file", "sed 's/foo/bar/g' file",
            "sed -E -e 's/foo/bar/' -e '2p' file", "sed -n '1p;3p' file",
            "sed 's|foo|bar|2p' file", "sed 's/[a/b]/x/g' file",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.classify(command).action, 'allow')
        for command in (
            'find . -exec touch output \\;', 'find . -execdir echo {} +',
            'find subdir -delete', 'find . -fprint output', 'find . -fprintf output fmt',
            'find . -fls output', 'find . -name', 'find . -unknown',
            'grep --pre=program pattern file', 'grep --hostname-bin=program pattern file',
            'grep --output=output pattern file', 'grep --unknown pattern file', 'grep -e',
            'grep --include', 'grep -A', 'sed -i s/foo/bar/ file',
            "sed -n '1e touch output' file", "sed 's/foo/bar/e' file",
            "sed 's/foo/bar/w output' file", "sed 'w output' file", "sed 'r input' file",
            'sed -f script file', 'sed --file=script file', 'sed -e',
            "sed --unknown '1p' file", "sed 'p;e touch output' file",
        ):
            with self.subTest(command=command):
                self.assertNotEqual(self.classify(command).action, 'allow')
        self.assertEqual(self.classify('find . -delete').action, 'deny')

    def test_compounds_variables_and_redirections_keep_boundaries(self):
        for command in (
            'mystery && git branch -D old', 'git branch -D old && mystery',
            'mystery | git tag -d old', 'git remote add origin x > output',
            'git branch "$unknown"', 'git branch -D old < input',
            'if git branch -D old; then echo done; fi',
        ):
            with self.subTest(command=command):
                operation = self.classify(command)
                self.assertEqual(operation.action, 'deny')
                self.assertFalse(operation.supported)
        for command in ('grep "$pattern" file', 'find "$dir" -print',
                        'sed "$script" file', 'truncate -s 0 "$file"',
                        'sed "$(cat script)" file', 'find . -print < input'):
            with self.subTest(command=command):
                self.assertTrue(self.classify(command).needs_classification)
        self.assertEqual(self.classify('grep pattern file > output').targets, (self.root / 'output',))
        self.assertEqual(self.classify('sed -n 1p file > .env').action, 'deny')
        operation = self.classify('truncate -s 0 first second && mkdir -p third')
        self.assertEqual(operation.targets,
                         (self.root / 'first', self.root / 'second', self.root / 'third'))
        self.assertEqual(self.classify('git branch -a | grep main').action, 'allow')
        self.assertNotEqual(self.classify('git -C .. branch --show-current').action, 'allow')

    def test_default_policy_cannot_prompt_to_allow_unsupported_commands(self):
        for command in ('git branch -D old', 'mystery && git branch -D old',
                        'git tag -d old > output'):
            with self.subTest(command=command):
                decision = decide(self.settings, 'shell', None, {'command': command}, root=self.root,
                                  readonly=(), forbidden=(), command_arg='command',
                                  allow_sandboxed_shell=True)
                self.assertEqual(decision.action, 'deny')
                self.assertTrue(decision.forced)
        for command in ('cp source .env', 'mv source .git/config', 'truncate -s 0 .env other'):
            self.assertEqual(self.classify(command).action, 'deny')


if __name__ == '__main__':
    unittest.main()
