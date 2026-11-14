import asyncio
from contextlib import asynccontextmanager
import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.permissions.policy import CURRENT_GRANT
from agent.permissions.rules import Decision
from agent.permissions.secure_io import CheckedRead
from agent.permissions.workspace import RestrictedBackend


class FileSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="ani-file-security-")
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name).resolve()
        self.root = base / "workspace"
        self.root.mkdir()
        self.forbidden = base / "forbidden"
        self.forbidden.mkdir()
        self.secret = self.forbidden / "marker.txt"
        self.secret.write_bytes(b"FORBIDDEN_TEST_MARKER")
        settings = base / "permissions.json"
        settings.write_text("{}")
        policy = SimpleNamespace(path=settings, forbidden=(self.forbidden,), readonly=(),
                                 role_read_only=False, allow_sandboxed_shell=True)
        self.backend = RestrictedBackend(self.root, policy)
        self.addCleanup(self.backend.close)
        self.file = self.root / "ordinary.txt"
        self.file.write_bytes(b"ordinary marker")

    async def test_normal_file_and_initial_symlink(self):
        alias = self.root / "alias.txt"
        alias.symlink_to(self.file.name)
        for target in (self.file, alias):
            with self.subTest(target=target.name):
                grant = Decision("allow", "test", str(self.file), target=self.file)
                token = CURRENT_GRANT.set(grant)
                try:
                    self.assertEqual(await self.backend.read_bytes(str(target)), b"ordinary marker")
                finally:
                    CURRENT_GRANT.reset(token)

    async def test_explicit_external_file_approval(self):
        outside = self.root.parent / "external.txt"
        outside.write_bytes(b"approved outside marker")
        with self.assertRaises(PermissionError):
            await self.backend.read_bytes(str(outside))
        token = CURRENT_GRANT.set(Decision("ask", "test", str(outside), target=outside, forced=True))
        try:
            self.assertEqual(await self.backend.read_bytes(str(outside)), b"approved outside marker")
        finally:
            CURRENT_GRANT.reset(token)

    async def test_file_replaced_by_forbidden_link_while_waiting_for_lock(self):
        @asynccontextmanager
        async def swapped(path):
            self.file.unlink()
            self.file.symlink_to(self.secret)
            yield
        self.backend._file_lock = swapped
        with self.assertRaises(PermissionError):
            await self.backend.read_bytes(str(self.file))

    async def test_ancestor_replaced_while_waiting_for_lock(self):
        directory = self.root / "sub"
        directory.mkdir()
        file = directory / "marker.txt"
        file.write_bytes(b"safe subtree")
        @asynccontextmanager
        async def swapped(path):
            directory.rename(self.root / "original-sub")
            directory.symlink_to(self.forbidden, target_is_directory=True)
            yield
        self.backend._file_lock = swapped
        with self.assertRaises(PermissionError):
            await self.backend.read_bytes(str(file))

    async def test_link_changed_during_resolution(self):
        alias = self.root / "alias.txt"
        alias.symlink_to(self.file.name)
        readlink = os.readlink
        def swapped(path, *, dir_fd=None):
            destination = readlink(path, dir_fd=dir_fd)
            if dir_fd is not None and path == alias.name:
                alias.unlink()
                alias.symlink_to(self.secret)
            return destination
        with patch("agent.permissions.secure_io.os.readlink", side_effect=swapped):
            with self.assertRaises(PermissionError):
                await self.backend.read_bytes(str(alias))

    async def test_changed_granted_target_cannot_become_another_ordinary_file(self):
        other = self.root / "other.txt"
        other.write_bytes(b"different ordinary marker")
        alias = self.root / "alias.txt"
        alias.symlink_to(other)
        token = CURRENT_GRANT.set(Decision("allow", "test", str(self.file), target=self.file))
        try:
            with self.assertRaises(PermissionError):
                await self.backend.read_bytes(str(alias))
        finally:
            CURRENT_GRANT.reset(token)

    async def test_fifo_and_directory_are_rejected(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for target in (fifo, self.root):
            with self.subTest(target=target.name), self.assertRaises(OSError):
                await self.backend.read_bytes(str(target))

    async def test_nested_git_is_protected_without_grant(self):
        target = self.root / "repo" / ".git" / "config"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"nested git test marker")
        with self.assertRaises(PermissionError):
            await self.backend.read_bytes(str(target))
        token = CURRENT_GRANT.set(Decision("ask", "test", str(target), target=target, forced=True))
        try:
            self.assertEqual(await self.backend.read_bytes(str(target)), b"nested git test marker")
        finally:
            CURRENT_GRANT.reset(token)

    async def test_list_dir_rejects_ancestor_exchange(self):
        directory = self.root / "sub"
        directory.mkdir()
        (directory / "safe.txt").write_bytes(b"safe")
        original = CheckedRead.verify
        swapped = False
        def verify(checked):
            nonlocal swapped
            if not swapped and checked.path == directory:
                swapped = True
                directory.rename(self.root / "original-sub")
                directory.symlink_to(self.forbidden, target_is_directory=True)
            return original(checked)
        with patch.object(CheckedRead, "verify", verify):
            with self.assertRaises(PermissionError):
                await self.backend.list_dir(str(directory))

    async def test_list_dir_initial_alias_and_child_link_metadata(self):
        directory = self.root / "sub"
        directory.mkdir()
        (directory / "safe.txt").write_bytes(b"safe")
        (directory / "link.txt").symlink_to(self.secret)
        alias = self.root / "alias"
        alias.symlink_to(directory, target_is_directory=True)
        entries = await self.backend.list_dir(str(alias))
        self.assertEqual([entry.name for entry in entries], ["link.txt", "safe.txt"])
        link = entries[0]
        self.assertEqual(link.size, (directory / "link.txt").lstat().st_size)

    async def test_write_preserves_mode_and_supports_missing_parents(self):
        self.file.chmod(0o640)
        await self.backend.write_bytes(str(self.file), b"updated marker")
        self.assertEqual(self.file.read_bytes(), b"updated marker")
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)
        new = self.root / "new" / "sub" / "file.txt"
        await self.backend.write_bytes(str(new), b"new marker")
        self.assertEqual(new.read_bytes(), b"new marker")

    async def test_write_to_initial_symlink_updates_checked_target(self):
        alias = self.root / "alias.txt"
        alias.symlink_to(self.file)
        await self.backend.write_bytes(str(alias), b"updated through alias")
        self.assertTrue(alias.is_symlink())
        self.assertEqual(self.file.read_bytes(), b"updated through alias")

    async def test_write_rejects_ancestor_exchange(self):
        directory = self.root / "sub"
        directory.mkdir()
        file = directory / "marker.txt"
        file.write_bytes(b"safe subtree")
        @asynccontextmanager
        async def swapped(path):
            directory.rename(self.root / "original-sub")
            directory.symlink_to(self.forbidden, target_is_directory=True)
            yield
        self.backend._file_lock = swapped
        with self.assertRaises(PermissionError):
            await self.backend.write_bytes(str(file), b"must not reach forbidden")
        self.assertEqual(self.secret.read_bytes(), b"FORBIDDEN_TEST_MARKER")

    async def test_write_rejects_new_file_appearing_during_lock_wait(self):
        new = self.root / "new.txt"
        @asynccontextmanager
        async def swapped(path):
            new.symlink_to(self.secret)
            yield
        self.backend._file_lock = swapped
        with self.assertRaises(PermissionError):
            await self.backend.write_bytes(str(new), b"must not reach forbidden")
        self.assertEqual(self.secret.read_bytes(), b"FORBIDDEN_TEST_MARKER")

    async def test_cancellation_keeps_read_lock_until_worker_exits(self):
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        held = False
        read = CheckedRead.read
        @asynccontextmanager
        async def lock(path):
            nonlocal held
            held = True
            try:
                yield
            finally:
                held = False
        def blocked_read(checked):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise TimeoutError("test worker was not released")
            return read(checked)
        self.backend._file_lock = lock
        with patch.object(CheckedRead, "read", blocked_read):
            task = asyncio.create_task(self.backend.read_bytes(str(self.file)))
            try:
                await asyncio.wait_for(started.wait(), 5)
                task.cancel()
                await asyncio.sleep(0)
                self.assertTrue(held)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertFalse(held)

    async def test_file_lock_still_covers_read(self):
        entered = False
        read = CheckedRead.read
        @asynccontextmanager
        async def lock(path):
            nonlocal entered
            entered = True
            try:
                yield
            finally:
                entered = False
        def checked_read(checked):
            self.assertTrue(entered)
            return read(checked)
        self.backend._file_lock = lock
        with patch.object(CheckedRead, "read", checked_read):
            self.assertEqual(await self.backend.read_bytes(str(self.file)), b"ordinary marker")
        self.assertFalse(entered)
