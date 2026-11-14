from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from agent.permissions.config import Settings
from integrations.git_push import Push, prepare_objects, run_git, send_push
from integrations.network import NetworkDenied


class GitHistorySecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        await run_git(self.root, "init", "--template=")
        self.identity = (("user.name", "Permission Test"), ("user.email", "test@example.com"))

    async def commit(self, name, content="ordinary project text"):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        await run_git(self.root, "add", "--", name)
        await run_git(self.root, "commit", "-m", "test change", config=self.identity)
        return (await run_git(self.root, "rev-parse", "HEAD")).decode().strip()

    async def inspect(self, source, protected=None):
        repo = self.base / "outbound"
        repo.mkdir(exist_ok=True)
        await run_git(repo, "init", "--bare", "--template=")
        await prepare_objects(
            self.root, repo, source,
            protected=tuple(Settings().protected_paths if protected is None else protected),
        )

    async def test_ordinary_project_history_is_allowed(self):
        source = await self.commit("src/main.py", "print('ordinary')")
        await self.inspect(source)

    async def test_default_protection_rejects_credentials_file_without_token_pattern(self):
        source = await self.commit("nested/credentials.txt", "private ordinary words")
        with self.assertRaisesRegex(NetworkDenied, "受保护文件"):
            await self.inspect(source)

    async def test_deleted_protected_file_in_history_is_still_rejected(self):
        await self.commit("credentials.txt", "private ordinary words")
        (self.root / "credentials.txt").unlink()
        await run_git(self.root, "add", "--", "credentials.txt")
        await run_git(self.root, "commit", "-m", "remove file", config=self.identity)
        source = (await run_git(self.root, "rev-parse", "HEAD")).decode().strip()
        with self.assertRaisesRegex(NetworkDenied, "受保护文件"):
            await self.inspect(source)

    async def test_custom_path_protection_keeps_full_directory_context(self):
        source = await self.commit("src/private/notes.txt")
        await self.inspect(source, ["private/**"])
        with self.assertRaisesRegex(NetworkDenied, "受保护文件"):
            await self.inspect(source, ["src/private/**"])

    async def test_same_blob_under_a_protected_path_is_not_lost_to_deduplication(self):
        await self.commit("public.txt", "identical ordinary words")
        source = await self.commit("credentials.txt", "identical ordinary words")
        with self.assertRaisesRegex(NetworkDenied, "受保护文件"):
            await self.inspect(source)

    async def test_annotated_tag_history_is_inspected(self):
        await self.commit("credentials.txt", "private ordinary words")
        await run_git(self.root, "tag", "-a", "release", "-m", "release", config=self.identity)
        source = (await run_git(self.root, "rev-parse", "release")).decode().strip()
        with self.assertRaisesRegex(NetworkDenied, "受保护文件"):
            await self.inspect(source)

    async def test_normal_annotated_tag_remains_supported(self):
        await self.commit("readme.txt")
        await run_git(self.root, "tag", "-a", "release", "-m", "release", config=self.identity)
        source = (await run_git(self.root, "rev-parse", "release")).decode().strip()
        await self.inspect(source)

    async def test_protected_history_is_rejected_before_dns_or_push(self):
        source = await self.commit("credentials.txt", "private ordinary words")
        push = Push("origin", "refs/heads/main", "refs/heads/main")
        endpoint = "https://example.com/repository.git"
        resolve = AsyncMock(side_effect=AssertionError("must reject before DNS"))
        with patch("integrations.git_push.prepare_push", AsyncMock(return_value=(endpoint, source))), patch(
            "integrations.git_push.NetworkPolicy.resolve", resolve,
        ):
            with self.assertRaisesRegex(NetworkDenied, "受保护文件"):
                await send_push(
                    self.root, push, (endpoint, source), protected=tuple(Settings().protected_paths),
                )
        resolve.assert_not_awaited()

    async def test_legacy_send_signature_loads_trusted_protection(self):
        source = await self.commit("private/notes.txt", "ordinary private words")
        settings = self.base / "permissions.json"
        settings.write_text('{"protected_paths":["private/**"]}')
        endpoint = "https://example.com/repository.git"
        push = Push("origin", "refs/heads/main", "refs/heads/main")
        with patch("agent.permissions.config.config_path", return_value=settings), patch(
            "integrations.git_push.prepare_push", AsyncMock(return_value=(endpoint, source)),
        ), patch("integrations.git_push.NetworkPolicy.resolve", new_callable=AsyncMock) as resolve:
            with self.assertRaisesRegex(NetworkDenied, "受保护文件"):
                await send_push(self.root, push, (endpoint, source))
        resolve.assert_not_awaited()

    async def test_unmatched_custom_pattern_does_not_reject_another_directory(self):
        source = await self.commit("public/key.txt", "ordinary file")
        await self.inspect(source, ["private/**"])
