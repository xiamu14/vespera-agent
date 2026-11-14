"""Check an immutable branch snapshot before a restricted HTTPS push."""
import asyncio
import base64
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
from urllib.parse import urlsplit
from tempfile import TemporaryDirectory

from integrations.network import NetworkDenied, NetworkPolicy, origin
from integrations.network_privacy import MAX_PAYLOAD, check_content, public_https
from integrations.network_data import check_data
from integrations.protected_paths import is_protected_path

REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]*")
OID = re.compile(rb"(?:[a-f0-9]{40}|[a-f0-9]{64})")


@dataclass(frozen=True)
class Push:
    remote: str
    source: str
    destination: str
    force: bool = False
    delete: bool = False


def push_command(command: str) -> Push | None:
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if words[:2] not in (["git", "push"], ["/usr/bin/git", "push"]):
        return None
    arguments = words[2:]
    force = bool(arguments and arguments[0] == "--force")
    delete = bool(arguments and arguments[0] == "--delete")
    if force or delete:
        arguments = arguments[1:]
    if len(arguments) != 2:
        return None
    remote, refspec = arguments
    if delete and ":" in refspec:
        return None
    source, _, destination = refspec.partition(":")
    if not destination:
        destination = source
    if not all(REF.fullmatch(item) for item in (remote, source, destination)):
        return None
    if "/" in remote or source == "HEAD" and source == destination:
        return None
    def full_ref(value):
        return value if value.startswith(("refs/heads/", "refs/tags/")) else f"refs/heads/{value}"
    source = source if source == "HEAD" else full_ref(source)
    destination = full_ref(destination)
    return Push(remote, source, destination, force, delete)


def git_environment() -> dict[str, str]:
    return {"PATH": os.defpath, "HOME": "/var/empty", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1"}


async def run_git(root: Path, *args: str, limit=MAX_PAYLOAD, config=()) -> bytes:
    executable = shutil.which("git")
    if not executable:
        raise NetworkDenied("无法定位 Git")
    environment = git_environment()
    environment["GIT_CONFIG_COUNT"] = str(len(config))
    for index, (key, value) in enumerate(config):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    process = await asyncio.create_subprocess_exec(
        executable, "--no-optional-locks", "-c", "core.hooksPath=/dev/null",
        "-C", str(root), *args, env=environment, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(30):
            chunks = bytearray()
            while chunk := await process.stdout.read(65536):
                chunks.extend(chunk)
                if len(chunks) > limit:
                    raise NetworkDenied("Git 外发对象超出检查预算")
            if await process.wait():
                raise NetworkDenied("Git 操作失败，请检查分支、远端和认证")
            return bytes(chunks)
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def prepare_push(root: Path, push: Push) -> tuple[str, str]:
    urls = (await run_git(root, "remote", "get-url", "--push", "--all", push.remote)).splitlines()
    if len(urls) != 1:
        raise NetworkDenied("推送需要唯一的远端地址")
    url = str(public_https(urls[0].decode("utf-8")))
    if urlsplit(url).query:
        raise NetworkDenied("Git 远端地址不支持查询参数")
    await run_git(root, "check-ref-format", push.destination)
    if push.delete:
        return url, ""
    object_type = "object" if push.destination.startswith("refs/tags/") else "commit"
    commit = (await run_git(root, "rev-parse", "--verify", f"{push.source}^{{{object_type}}}")).strip()
    if not OID.fullmatch(commit):
        raise NetworkDenied("无法固定待推送提交")
    return url, commit.decode("ascii")


async def send_push(root: Path, push: Push, prepared: tuple[str, str], *, protected: tuple[str, ...] | None = None) -> str:
    if protected is None:
        from agent.permissions.config import config_path, load_settings
        protected = tuple(load_settings(config_path())[0].protected_paths)
    url, commit = prepared
    if await prepare_push(root, push) != prepared:
        raise NetworkDenied("待推送的分支或远端已变化")
    target = public_https(url)
    endpoint = origin(url)
    # Copy only the immutable commit's reachable objects into an app-owned bare repository.
    with TemporaryDirectory(prefix="ani-push-") as directory:
        repo = Path(directory)
        object_format = "sha256" if len(commit) == 64 else "sha1"
        await run_git(repo, "init", "--bare", f"--object-format={object_format}", "--template=")
        if commit:
            await prepare_objects(root, repo, commit, protected=protected)
        check_content(push.destination)
        ip = await NetworkPolicy([endpoint], []).resolve(target)
        if ":" in ip:
            ip = f"[{ip}]"
        settings = (
            "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
            "-c", "http.followRedirects=false", "-c", "http.proxy=",
            "-c", f"http.curloptResolve={target.host}:{target.port or 443}:{ip}",
            "-c", "push.negotiate=false",
        )
        # Require DNS pinning support before the app-owned Git process may connect.
        supported = await run_git(repo, "help", "--config")
        if b"http.curloptResolve" not in supported.splitlines():
            raise NetworkDenied("当前 Git 不支持固定已检查的远端地址")
        authentication = ()
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if target.host == "github.com" and token:
            credential = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
            authentication = ((f"http.{url}.extraHeader", f"Authorization: Basic {credential}"),)
        refspec = f"{'+' if push.force else ''}{commit}:{push.destination}"
        await run_git(repo, *settings, "push", "--no-verify", "--no-follow-tags",
                      url, refspec, config=authentication)
    return f"已推送到 {push.destination}"


async def prepare_objects(root: Path, repo: Path, commit: str, *, protected: tuple[str, ...]) -> None:
    # Read objects locally without invoking the source repository's upload-pack or filters.
    objects_dir = Path((await run_git(root, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
                       .decode("utf-8").strip())
    (repo / "objects" / "info" / "alternates").write_text(f"{objects_dir}\n")
    await run_git(repo, "update-ref", "refs/ani/outbound", commit)
    await run_git(repo, "repack", "-a", "-d")
    (repo / "objects" / "info" / "alternates").unlink()
    await run_git(repo, "fsck", "--strict", "--no-reflogs")
    listing = await run_git(repo, "rev-list", "--objects", "--no-object-names", commit)
    objects = listing.splitlines()
    if len(objects) > 2000 or any(not OID.fullmatch(item) for item in objects):
        raise NetworkDenied("Git 对象清单无法完整检查")
    kinds = await object_types(repo, set(objects))
    budget = 8 * MAX_PAYLOAD
    contents = {}
    for item in objects:
        data = await run_git(repo, "cat-file", "-p", item.decode("ascii"), limit=min(MAX_PAYLOAD, budget))
        if data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
            raise NetworkDenied("LFS 对象尚无受控上传流程，拒绝推送不完整交付")
        contents[item] = data
        budget -= len(data)
    await check_object_paths(repo, commit.encode("ascii"), kinds, contents, protected)
    for data in contents.values():
        check_data(data, destination="git", category="project")


async def object_types(repo: Path, objects: set[bytes]) -> dict[bytes, bytes]:
    listing = await run_git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)")
    kinds = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 2 or not OID.fullmatch(fields[0]) or fields[1] not in {b"commit", b"tree", b"blob", b"tag"}:
            raise NetworkDenied("Git 对象类型无法完整检查")
        if fields[0] in kinds:
            raise NetworkDenied("Git 对象类型清单含重复项")
        kinds[fields[0]] = fields[1]
    if kinds.keys() != objects:
        raise NetworkDenied("Git 对象类型与待发送对象不一致")
    return kinds


def object_reference(data: bytes, header: bytes) -> bytes:
    headers = data.split(b"\n\n", 1)[0].splitlines()
    matches = [line[len(header) + 1:] for line in headers if line.startswith(header + b" ")]
    if len(matches) != 1 or not OID.fullmatch(matches[0]):
        raise NetworkDenied("Git 对象的文件来源无法确定")
    return matches[0]


async def check_object_paths(repo, source, kinds, contents, protected):
    roots = {object_reference(contents[item], b"tree") for item, kind in kinds.items() if kind == b"commit"}
    seen = set()
    while kinds.get(source) == b"tag":
        if source in seen:
            raise NetworkDenied("Git 标签来源存在循环")
        seen.add(source)
        source = object_reference(contents[source], b"object")
    if kinds.get(source) == b"tree":
        roots.add(source)
    elif kinds.get(source) != b"commit":
        raise NetworkDenied("Git 对象缺少可检查的历史文件路径")
    pending = [(tree, "") for tree in roots]
    cache = {}
    entries_left = 10000
    bytes_left = 8 * MAX_PAYLOAD
    while pending:
        tree, prefix = pending.pop()
        if kinds.get(tree) != b"tree":
            raise NetworkDenied("Git 历史文件树不完整")
        if tree not in cache:
            listing = await run_git(repo, "ls-tree", "-z", tree.decode("ascii"))
            if listing and not listing.endswith(b"\0"):
                raise NetworkDenied("Git 历史文件路径清单不完整")
            cache[tree] = listing.split(b"\0")[:-1]
        for entry in cache[tree]:
            metadata, separator, raw_name = entry.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 3 or not OID.fullmatch(fields[2]):
                raise NetworkDenied("Git 历史文件路径无法解析")
            try:
                name = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                raise NetworkDenied("Git 历史文件名无法完整检查") from None
            if name in {"", ".", ".."} or "/" in name:
                raise NetworkDenied("Git 历史文件路径无效")
            path = f"{prefix}/{name}" if prefix else name
            entries_left -= 1
            bytes_left -= len(path.encode("utf-8"))
            if entries_left < 0 or bytes_left < 0 or path.count("/") > 128:
                raise NetworkDenied("Git 历史文件路径超出检查预算")
            if is_protected_path(path, protected):
                raise NetworkDenied("禁止推送包含受保护文件的 Git 历史")
            check_data(path, destination="git", category="project")
            if fields[1] == b"tree":
                if fields[0] != b"040000":
                    raise NetworkDenied("Git 历史目录类型不一致")
                pending.append((fields[2], path))
            elif fields[1] == b"blob":
                if fields[0] not in {b"100644", b"100755", b"120000"} or kinds.get(fields[2]) != b"blob":
                    raise NetworkDenied("Git 历史文件类型不一致")
            elif fields[1] != b"commit" or fields[0] != b"160000":
                raise NetworkDenied("Git 历史文件类型不受支持")
