"""Deterministic checks for outbound request data."""
import ipaddress
from collections import OrderedDict
import hashlib
import base64
import binascii
import json
import os
import re
from urllib.parse import unquote

import httpx2

from integrations.network import NetworkDenied, origin

MAX_PAYLOAD = 1024 * 1024
SECRET_FIELD = re.compile(
    r'(?i)(?:\b(?:api[_-]?key|access[_-]?token|token|password|secret|authorization|cookie)\b)'
    r'["\s]*[:=]\s*["\']?[^\s"\'&,;}]+'
)
SECRET_VALUE = re.compile(
    r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{20,}'
    r'|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}'
    r'|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b'
)
ENCODED = re.compile(r'(?<![A-Za-z0-9_+/-])[A-Za-z0-9_+/-]{11,}={0,2}(?![A-Za-z0-9_+/-])')
SENSITIVE_PATH = re.compile(r'(?:^|[/\s])(?:\.env(?:[./\s?&]|$)|\.ssh(?:/|$)|\.aws(?:/|$))')
SCANNER_VERSION = "6"
MAX_SCANNED = 4096
MAX_NORMALIZATION_DEPTH = 6
MAX_NORMALIZATION_CANDIDATES = 16384
NORMALIZATION_BYTE_MULTIPLIER = 4
MODEL_BINARY = re.compile(rb'data:[^"\s]+;base64,|"kind":"binary(?:-content)?"')
_SCANNED: OrderedDict[tuple, None] = OrderedDict()


def check_content(value: str | bytes, *, protect_paths=True, max_bytes=MAX_PAYLOAD, forbid_binary=False) -> None:
    if isinstance(value, str):
        value = value.encode()
    if len(value) > max_bytes:
        raise NetworkDenied("请求超出外发检查预算")
    secrets = tuple(sorted(secret for key, secret in os.environ.items()
                           if len(secret) >= 8 and re.search(r"KEY|TOKEN|PASSWORD|SECRET", key, re.I)))
    credential_version = hashlib.sha256(json.dumps(secrets).encode()).digest()
    cache_key = (SCANNER_VERSION, protect_paths, forbid_binary, max_bytes, credential_version,
                 hashlib.sha256(value).digest())
    if cache_key in _SCANNED:
        _SCANNED.move_to_end(cache_key)
        return
    _scan_content(value, secrets, protect_paths, forbid_binary, max_bytes=max_bytes)
    _SCANNED[cache_key] = None
    if len(_SCANNED) > MAX_SCANNED:
        _SCANNED.popitem(last=False)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NetworkDenied("请求 JSON 字段重复，无法完整检查")
        result[key] = value
    return result


def _scan_content(value: bytes, secrets: tuple[str, ...], protect_paths: bool, forbid_binary: bool,
                  *, max_bytes=MAX_PAYLOAD) -> None:
    try:
        content = value.decode("utf-8")
    except UnicodeDecodeError:
        raise NetworkDenied("请求内容无法完整检查") from None
    pending = []
    seen = set()
    scanned_bytes = 0
    candidates = 0

    def spend_candidate():
        nonlocal candidates
        candidates += 1
        if candidates > MAX_NORMALIZATION_CANDIDATES:
            raise NetworkDenied("请求规范化候选数超出检查预算")

    def enqueue(text, depth):
        nonlocal scanned_bytes
        # Digests avoid retaining a second copy of every normalized representation.
        try:
            raw = text.encode("utf-8")
        except UnicodeEncodeError:
            raise NetworkDenied("请求内容无法完整检查") from None
        key = hashlib.sha256(raw).digest()
        if key in seen:
            return
        if depth > MAX_NORMALIZATION_DEPTH:
            raise NetworkDenied("请求编码层数超出检查预算")
        spend_candidate()
        scanned_bytes += len(raw)
        if scanned_bytes > max_bytes * NORMALIZATION_BYTE_MULTIPLIER:
            raise NetworkDenied("请求规范化字节数超出检查预算")
        seen.add(key)
        pending.append((text, raw, depth))

    enqueue(content, 0)
    while pending:
        content, raw, depth = pending.pop()
        if forbid_binary and MODEL_BINARY.search(raw):
            raise NetworkDenied("附件需要对应格式的外发扫描")
        if any(ord(char) < 32 and char not in "\t\r\n" for char in content):
            raise NetworkDenied("不支持不透明或二进制请求")
        if SECRET_FIELD.search(content) or SECRET_VALUE.search(content) or (protect_paths and SENSITIVE_PATH.search(content)):
            raise NetworkDenied("请求含受保护的数据")
        if any(secret in content for secret in secrets):
            raise NetworkDenied("请求含已知凭据")
        decoded = unquote(content)
        if decoded != content:
            enqueue(decoded, depth + 1)
        if content.lstrip().startswith(("{", "[", '"')):
            try:
                parsed = json.loads(content, object_pairs_hook=_json_object)
            except ValueError:
                parsed = None  # Ordinary text can start with a brace or quote.
            except RecursionError:
                raise NetworkDenied("请求 JSON 层数超出检查预算") from None
            if parsed is not None:
                enqueue(json.dumps(parsed, ensure_ascii=False), depth + 1)
                nodes = [parsed]
                while nodes:
                    spend_candidate()
                    node = nodes.pop()
                    if isinstance(node, str):
                        enqueue(node, depth + 1)
                    elif isinstance(node, dict):
                        nodes.extend(node.keys())
                        nodes.extend(node.values())
                    elif isinstance(node, list):
                        nodes.extend(node)
        for match in ENCODED.finditer(content):
            spend_candidate()
            encoded = match.group()
            marked = content[max(0, match.start() - 8):match.start()] == ";base64,"
            try:
                decoded_bytes = base64.b64decode(encoded + "=" * (-len(encoded) % 4),
                                                 altchars=b"-_", validate=True)
            except binascii.Error:
                if marked:
                    raise NetworkDenied("不支持不透明的编码载荷") from None
                continue  # An identifier followed by '=' need not be valid Base64.
            explicit = marked or encoded.endswith("=")
            try:
                decoded_text = decoded_bytes.decode("utf-8")
            except UnicodeDecodeError:
                if explicit:
                    raise NetworkDenied("不支持不透明的编码载荷") from None
                continue  # A long identifier alone is not evidence of encoded binary.
            if not explicit and any(ord(char) < 32 and char not in "\t\r\n" for char in decoded_text):
                continue
            enqueue(decoded_text, depth + 1)


def check_values(value: object) -> None:
    check_content(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def public_https(url: str) -> httpx2.URL:
    try:
        target = httpx2.URL(url)
        origin(str(target))
    except (ValueError, httpx2.InvalidURL):
        raise NetworkDenied("请求需要有效的 HTTPS 地址") from None
    if target.scheme != "https":
        raise NetworkDenied("公开网页请求仅允许 HTTPS")
    try:
        address = ipaddress.ip_address(target.host)
    except ValueError:
        if "." not in target.host.rstrip(".") or target.host.rstrip(".").endswith((".localhost", ".local")):
            raise NetworkDenied("公开网页请求需要公网地址") from None
    else:
        if not address.is_global or address.is_multicast:
            raise NetworkDenied("公开网页请求需要公网地址")
    check_content(str(target))
    return target
