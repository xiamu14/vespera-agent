"""Descriptor-bound file reads: keep and revalidate every traversed directory/link."""
from collections import deque
from contextlib import ExitStack
import errno
import os
from pathlib import Path
import stat
from secrets import token_hex


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns, info.st_ctime_ns, info.st_size


class CheckedRead:
    """Resolve links without following an unchecked component at the eventual open.

    Capture before awaiting a file lock; verify inside the I/O worker and again before
    returning data. Directory/link observations also cover the original alias spelling.
    """

    def __init__(self, path: str, *, directory=False, create_directories=False, follow_links=True):
        self.stack = ExitStack()
        self.observed = []
        self.missing = []
        self.directory = directory
        self.create_directories = create_directories
        self.follow_links = follow_links
        try:
            self._capture(path)
        except (OSError, ValueError):
            self.close()
            raise

    def _open(self, name, flags, *, parent=None):
        try:
            fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise PermissionError("文件路径在检查期间发生变化") from error
            raise
        self.stack.callback(os.close, fd)
        return fd

    def _capture(self, path):
        original = Path(path)
        if not original.is_absolute():
            raise ValueError("文件路径必须是绝对路径")
        root_fd = self._open("/", os.O_RDONLY | os.O_DIRECTORY)
        parents = [root_fd]
        names = []
        remaining = deque(original.parts[1:])
        links = 0
        while remaining:
            name = remaining.popleft()
            if name in {"", "."}:
                continue
            if name == "..":
                if names:
                    names.pop()
                    parents.pop()
                continue
            parent = parents[-1]
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                if not self.directory or not self.create_directories:
                    raise
                self.verify()
                try:
                    os.mkdir(name, dir_fd=parent)
                except FileExistsError:
                    pass  # A concurrent creator must still pass no-follow checks below.
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            self.observed.append((parent, name, _identity(info)))
            if stat.S_ISLNK(info.st_mode):
                if not self.follow_links:
                    raise PermissionError("已解析的路径被替换为符号链接")
                links += 1
                if links > 40:
                    raise OSError(errno.ELOOP, "符号链接层数过多", path)
                destination = Path(os.readlink(name, dir_fd=parent))
                if destination.is_absolute():
                    names = []
                    parents = [root_fd]
                    parts = destination.parts[1:]
                else:
                    parts = destination.parts
                remaining.extendleft(reversed(parts))
                continue
            is_directory = bool(remaining) or self.directory
            if not is_directory and not stat.S_ISREG(info.st_mode):
                raise OSError("不能读取非普通文件")
            flags = os.O_RDONLY | (os.O_DIRECTORY if is_directory else 0)
            fd = self._open(name, flags, parent=parent)
            opened = os.fstat(fd)
            if _identity(opened) != _identity(info):
                raise PermissionError("文件身份在打开期间发生变化")
            if not is_directory and not stat.S_ISREG(opened.st_mode):
                raise OSError("不能读取非普通文件")
            names.append(name)
            parents.append(fd)
        self.path = Path("/", *names)
        self.fd = parents[-1]
        info = os.fstat(self.fd)
        if self.directory:
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError(str(self.path))
        elif not stat.S_ISREG(info.st_mode):
            raise OSError("不能读取非普通文件")
        self.verify()

    def verify(self):
        for parent, name, expected in self.observed:
            try:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError) as error:
                raise PermissionError("文件路径在检查后发生变化") from error
            # Directory timestamps change when unrelated siblings are edited. Its identity,
            # unlike a file/link's complete state, must remain stable throughout the read.
            actual = _identity(current)
            compared = 3 if stat.S_ISDIR(expected[2]) else len(expected)
            if actual[:compared] != expected[:compared]:
                raise PermissionError("文件路径或目标在检查后发生变化")

        for parent, name in self.missing:
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise PermissionError("原本不存在的文件在检查后出现")

    def bind_file(self, name):
        try:
            info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            self.missing.append((self.fd, name))
            return 0o600
        if not stat.S_ISREG(info.st_mode):
            raise OSError("不能写入非普通文件")
        self.observed.append((self.fd, name, _identity(info)))
        return stat.S_IMODE(info.st_mode)

    def replace_bytes(self, name, data, mode):
        self.verify()
        temporary = ".ani-write-" + token_hex(16)
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as destination:
                destination.write(data)
                destination.flush()
                os.fsync(destination.fileno())
                os.fchmod(destination.fileno(), mode)
            self.verify()
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def read(self):
        self.verify()
        with os.fdopen(os.dup(self.fd), "rb") as source:
            result = source.read()
        self.verify()
        return result

    def close(self):
        self.stack.close()
