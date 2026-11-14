"""Network policy for MCP HTTP traffic; DNS validation is bound to the connection."""
import asyncio
import ipaddress
import socket

import httpx2


class NetworkDenied(PermissionError):
    pass


def origin(value: str) -> str:
    url = httpx2.URL(value)
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.fragment:
        raise ValueError("网络地址必须是无 userinfo、fragment 的 HTTP(S) URL")
    return str(url.copy_with(path="/", query=None))


class NetworkPolicy:
    def __init__(self, allowed: list[str], private: list[str]) -> None:
        self.allowed = {origin(value) for value in allowed}
        self.private = {origin(value) for value in private}
        if not self.private <= self.allowed:
            raise ValueError("allow_private_origins 必须包含在 allowed_origins 中")

    def check_url(self, url: httpx2.URL) -> None:
        key = origin(str(url))
        if key not in self.allowed:
            raise NetworkDenied("MCP 目标不在网络白名单中")
        if url.scheme == "http" and key not in self.private:
            raise NetworkDenied("HTTP 仅用于显式允许的本地或内网服务")

    async def resolve(self, url: httpx2.URL) -> str:
        self.check_url(url)
        addresses = await asyncio.get_running_loop().getaddrinfo(
            url.host, url.port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM,
        )
        ips = [ipaddress.ip_address(address[4][0]) for address in addresses]
        if not ips:
            raise NetworkDenied("MCP 域名没有可用地址")
        for address in ips:
            address = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
            # Metadata remains blocked even for an explicitly allowed private origin.
            if address.is_link_local or address in {
                ipaddress.ip_address(value) for value in (
                    "168.63.129.16", "100.100.100.200", "192.0.0.192",
                    "fd00:ec2::254", "fd00:ec2::23", "fd20:ce::254", "fd00:42::42",
                )
            }:
                raise NetworkDenied("禁止访问云元数据地址")
            if address.is_multicast or address.is_unspecified or (
                not address.is_global and origin(str(url)) not in self.private
            ):
                raise NetworkDenied("MCP 域名解析到未授权的非公网地址")
            # Reject transition addresses rather than permit an embedded-address bypass.
            if isinstance(address, ipaddress.IPv6Address) and (
                address.sixtofour or address.teredo or address in ipaddress.ip_network("64:ff9b::/96")
                or address in ipaddress.ip_network("64:ff9b:1::/48")
            ):
                raise NetworkDenied("不支持 IPv6 转换地址")
        return str(ips[0])


class PolicyTransport(httpx2.AsyncBaseTransport):
    def __init__(self, policy: NetworkPolicy, retries: int, endpoint: str, *, reject_redirects=True, check_payload=False,
                 before_send=None, trusted_auth_headers=None) -> None:
        self.policy = policy
        self.origin = origin(endpoint)
        self.reject_redirects = reject_redirects
        self.check_payload = check_payload
        self.before_send = before_send
        # Only exact protocol credentials supplied by trusted service config are exempt.
        # The origin check below runs before this exemption can be used.
        self.trusted_auth_headers = {
            (key.lower(), value) for key, value in httpx2.Headers(trusted_auth_headers or {}).multi_items()
            if key.lower() in {"authorization", "x-api-key", "api-key"}
        }
        # Only connection establishment is retried; requests already sent are never replayed here.
        self.transport: httpx2.AsyncBaseTransport = httpx2.AsyncHTTPTransport(retries=retries, trust_env=False)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.before_send is not None:
            self.before_send()
        if origin(str(request.url)) != self.origin:
            raise NetworkDenied("MCP 服务不能将请求转向另一个 origin")
        if self.check_payload:
            from integrations.network_privacy import MAX_PAYLOAD, check_content, check_values
            check_content(str(request.url))
            inspected_headers = [
                {key: value} for key, value in request.headers.multi_items()
                if (key.lower(), value) not in self.trusted_auth_headers
            ]
            # Preserve duplicate fields and inspect structured values under one budget.
            check_values(inspected_headers)
            body = bytearray()
            async for chunk in request.stream:
                if len(body) + len(chunk) > MAX_PAYLOAD:
                    raise NetworkDenied("请求超出外发检查预算")
                body.extend(chunk)
            check_content(bytes(body))
            request = httpx2.Request(request.method, request.url, headers=request.headers,
                                     content=bytes(body), extensions=request.extensions)
        async with asyncio.timeout(request.extensions.get("timeout", {}).get("connect", 5)):
            ip = await self.policy.resolve(request.url)
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        pinned = httpx2.Request(
            request.method, request.url.copy_with(host=ip), headers=headers, stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.host},
        )
        if self.before_send is not None:
            self.before_send()
        response = await self.transport.handle_async_request(pinned)
        if self.reject_redirects and 300 <= response.status_code < 400:
            await response.aclose()
            raise NetworkDenied("MCP 不允许 HTTP 重定向")
        return response

    async def aclose(self) -> None:
        await self.transport.aclose()
