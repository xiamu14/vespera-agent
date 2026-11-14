"""Nonblocking process leases; keep lock files so every contender uses the same inode."""
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import os
from pathlib import Path


class ExecutionBusyError(RuntimeError):
    pass


def delegation_lease(database: Path, conversation_id: str):
    return _lease(
        database.resolve().parent / "execution-locks", "delegations:" + conversation_id,
        shared=False, message="该 Super 会话已有打开的交互外壳，请先关闭后再操作。",
    )


@contextmanager
def _lease(directory: Path, key: str, *, shared: bool, message: str):
    if os.name != "posix":
        raise OSError("当前平台不支持会话执行锁，拒绝运行")
    import fcntl

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = sha256(key.encode()).hexdigest() + ".lock"
    fd = os.open(directory / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExecutionBusyError(message) from None
        yield
    finally:
        os.close(fd)


@contextmanager
def workspace_lease(database: Path, workspace: Path, *, shared: bool = False):
    """Lock overlapping workspace access without reserving a conversation."""
    directory = database.resolve().parent / "execution-locks"
    workspace = workspace.resolve(strict=True)
    with ExitStack() as stack:
        # Shared ancestor leases let sibling directories run, but exclude a parent writer.
        for parent in reversed(workspace.parents):
            stack.enter_context(_lease(
                directory, "workspace:" + str(parent), shared=True,
                message="上级工作目录正在执行任务，请使用独立目录或等待结束。",
            ))
        stack.enter_context(_lease(
            directory, "workspace:" + str(workspace), shared=shared,
            message="该工作目录或其子目录正在执行任务，请使用独立目录或等待结束。",
        ))
        yield


def session_lease(database: Path, conversation_id: str):
    return _lease(
        database.resolve().parent / "execution-locks", "session:" + conversation_id,
        shared=False, message="该会话已有运行中的请求，请等待结束后重试。",
    )
