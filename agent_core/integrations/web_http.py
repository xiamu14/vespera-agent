"""Bounded HTTP reads for WebFetch and the supported curl syntax."""
import httpx2

from integrations.network import NetworkDenied, NetworkPolicy, PolicyTransport, origin
from integrations.network_privacy import check_content, public_https
from integrations.network_data import check_data

MAX_RESPONSE = 1024 * 1024


async def fetch_http(url: str, *, method="GET", body=None, headers=None, category="public") -> tuple[str, bytes, str]:
    target = public_https(url)
    check_data(body or b"", destination="http", category=category)
    for key, value in (headers or {}).items():
        check_content(f"{key}: {value}")
        if key.lower() in {"authorization", "cookie", "host", "proxy-authorization"}:
            raise NetworkDenied("公开 HTTP 请求不允许注入认证或路由头")
    for _ in range(6):
        endpoint = origin(str(target))
        transport = PolicyTransport(NetworkPolicy([endpoint], []), 0, endpoint, reject_redirects=False)
        async with httpx2.AsyncClient(transport=transport, trust_env=False, timeout=30) as client:
            async with client.stream(method, target, content=body, headers=headers) as response:
                if response.is_redirect:
                    if method not in {"GET", "HEAD"}:
                        raise NetworkDenied("上传请求不跟随重定向")
                    redirected = public_https(str(target.join(response.headers["location"])))
                    if origin(str(redirected)) != endpoint and headers:
                        raise NetworkDenied("带自定义头的请求不允许跨 origin 重定向")
                    target = redirected
                    continue
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_RESPONSE:
                        raise NetworkDenied("响应超过读取预算")
                    data.extend(chunk)
                return str(target), bytes(data), response.headers.get("content-type", "")
    raise NetworkDenied("重定向次数超限")
