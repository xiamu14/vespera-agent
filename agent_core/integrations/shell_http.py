"""Freeze supported curl requests; never execute the command or reopen payload files."""
from dataclasses import dataclass
from pathlib import Path
import os
import hashlib
import shlex
import stat
from urllib.parse import urlencode

from integrations.network import NetworkDenied
from integrations.network_privacy import MAX_PAYLOAD, check_content, public_https
from integrations.network_data import check_data
from integrations.protected_paths import is_protected_path


@dataclass(frozen=True)
class CurlRequest:
    url: str
    method: str
    body: bytes | None
    header_items: tuple[tuple[str, str], ...] = ()
    category: str = "public"

    @property
    def headers(self):
        return dict(self.header_items)


def curl_args(command: str) -> list[str] | None:
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or words[0] not in {"curl", "/usr/bin/curl"}:
        return None
    return words[1:]


def payload_file(name: str, root: Path | None, forbidden=(), protected=()) -> bytes:
    if root is None or name == "-":
        raise NetworkDenied("文件上传需要工作区内的明确文件，不支持未固定的 stdin")
    root = root.resolve()
    path = root / name
    relative = path.relative_to(root) if path.is_relative_to(root) else None
    if relative is None or ".." in relative.parts or not relative.parts:
        raise NetworkDenied("上传文件不能离开工作区")
    if any(path.is_relative_to(item) for item in forbidden) or is_protected_path(relative.as_posix(), protected):
        raise NetworkDenied("禁止上传受保护文件")
    # Walk with no-follow directory descriptors so symlink swaps cannot widen the read scope.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (*root.parts[1:], *relative.parts[:-1]):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        source = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise NetworkDenied("上传仅支持普通文件")
            content = file.read(MAX_PAYLOAD + 1)
        check_content(content)
        check_content(str(relative))
        return content
    finally:
        os.close(fd)


def prepare_curl(args: list[str], root: Path | None = None, *, forbidden=(), protected=()) -> CurlRequest:
    url = None
    method = None
    data = []
    data_kind = None
    forms = []
    upload = None
    headers = {}
    as_query = False
    has_files = False
    tokens = iter(args)

    def value():
        result = next(tokens, None)
        if result is None:
            raise NetworkDenied("curl 参数缺少值")
        return result

    def read_file(name):
        nonlocal has_files
        content = payload_file(name, root, forbidden, protected)
        has_files = True
        return content

    for token in tokens:
        if token in {"-s", "-S", "-sS", "-f", "--silent", "--show-error", "--fail"}:
            continue
        if token in {"-I", "--head"} and method is None:
            method = "HEAD"
        elif token in {"-X", "--request"} and method is None:
            method = value().upper()
        elif token in {"-G", "--get"}:
            as_query = True
        elif token in {"-H", "--header"}:
            header = value()
            key, separator, content = header.partition(":")
            key = key.strip().lower()
            if not separator or not key or not all(c.isalnum() or c == "-" for c in key):
                raise NetworkDenied("无效请求头")
            if key in {"host", "authorization", "proxy-authorization", "cookie", "content-length",
                       "transfer-encoding", "connection", "content-encoding", "expect", "trailer"}:
                raise NetworkDenied("不允许由命令设置认证、路由或编码请求头")
            if "\r" in content or "\n" in content:
                raise NetworkDenied("不允许多行请求头")
            check_content(header)
            headers[key] = content.strip()
        elif token in {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--json"}:
            kind = "json" if token == "--json" else "data"
            if data_kind is not None and data_kind != kind:
                raise NetworkDenied("不能混合 --json 与 --data 请求体参数")
            data_kind = kind
            raw = value()
            if raw.startswith("@") and token not in {"--data-raw", "--data-urlencode"}:
                content = read_file(raw[1:])
                if token in {"-d", "--data"}:
                    content = content.replace(b"\r", b"").replace(b"\n", b"")
            elif token == "--data-urlencode":
                key, separator, text = raw.partition("=")
                if not separator or "@" in key:
                    raise NetworkDenied("data-urlencode 需要明确的 name=value")
                content = urlencode({key: text}).encode()
            else:
                content = raw.encode()
            check_content(content)
            data.append(content)
            if token == "--json":
                headers.setdefault("content-type", "application/json")
                headers.setdefault("accept", "application/json")
        elif token in {"-T", "--upload-file"} and upload is None:
            upload = read_file(value())
        elif token in {"-F", "--form", "--form-string"}:
            name, separator, raw = value().partition("=")
            if not separator or any(c in name for c in '\r\n";') or not name:
                raise NetworkDenied("无效表单字段")
            check_content(name)
            filename = None
            if token != "--form-string" and raw.startswith(("@", "<")):
                if ";" in raw or "," in raw:
                    raise NetworkDenied("表单文件需要单个明确路径")
                content = read_file(raw[1:])
                filename = Path(raw[1:]).name if raw[0] == "@" else None
                if filename and any(c in filename for c in '\r\n"'):
                    raise NetworkDenied("无效上传文件名")
            else:
                content = raw.encode()
                check_content(content)
            forms.append((name, filename, content))
        elif token.startswith("https://") and url is None:
            url = str(public_https(token))
        else:
            raise NetworkDenied("不支持此 curl 参数或命令组合")
    if sum((bool(data), bool(forms), upload is not None)) > 1:
        raise NetworkDenied("不能混合请求体、表单和文件上传")
    body = (b"" if data_kind == "json" else b"&").join(data) if data else upload
    if forms:
        boundary = "ani-" + hashlib.sha256(repr(forms).encode()).hexdigest()
        chunks = []
        for name, filename, content in forms:
            disposition = f'form-data; name="{name}"'
            if filename is not None:
                disposition += f'; filename="{filename}"'
            chunks.append(f"--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n".encode()
                          + content + b"\r\n")
        body = b"".join(chunks) + f"--{boundary}--\r\n".encode()
        headers["content-type"] = "multipart/form-data; boundary=" + boundary
    if as_query:
        if forms or upload is not None or method not in {None, "GET", "HEAD"}:
            raise NetworkDenied("GET 查询不能混合上传")
        if url is not None and body:
            url += ("&" if "?" in url else "?") + body.decode("utf-8")
        body = None
        method = method or "GET"
    method = method or ("PUT" if upload is not None else "POST" if body is not None else "GET")
    if url is None or method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
        raise NetworkDenied("不支持此 curl 请求形态")
    if body is not None and method in {"GET", "HEAD"}:
        raise NetworkDenied("GET/HEAD 不支持请求体")
    if body is not None:
        headers.setdefault("content-type", "application/x-www-form-urlencoded")
    url = str(public_https(url))
    category = "attachment" if has_files else "public"
    check_data(body or b"", destination="http", category=category)
    return CurlRequest(url, method, body, tuple(headers.items()), category)
