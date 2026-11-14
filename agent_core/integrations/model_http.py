"""SDK transport: scan serialized model requests and bind auth to the configured origin."""
import ipaddress
import httpx
import httpx2

from integrations.network import NetworkDenied, NetworkPolicy, PolicyTransport, origin
from integrations.network_privacy import check_content
from integrations.network_data import check_data, payload_limit
from integrations.model_data import check_model_payload


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, response):
        self.response = response

    async def __aiter__(self):
        async for data in self.response.aiter_raw():
            yield data

    async def aclose(self):
        await self.response.aclose()


class ModelTransport(httpx.AsyncBaseTransport):
    def __init__(self, base_url: str, *, destination="model"):
        endpoint = origin(base_url)
        host = httpx2.URL(base_url).host.rstrip(".")
        try:
            local = ipaddress.ip_address(host).is_private
        except ValueError:
            local = host == "localhost" or host.endswith(".local")
        # Public model/search DNS must not silently acquire access to internal addresses.
        self.transport = PolicyTransport(NetworkPolicy([endpoint], [endpoint] if local else []), 0, endpoint)
        self.destination = destination

    async def handle_async_request(self, request):
        check_content(str(request.url), protect_paths=False)
        for key, value in request.headers.items():
            if key.lower() in {"authorization", "x-api-key", "api-key"}:
                continue  # SDK authentication is injected by trusted provider config and origin-bound below.
            if key.lower() in {"cookie", "proxy-authorization"}:
                raise NetworkDenied(f"模型与搜索请求不允许 {key.lower()} 请求头")
            check_content(f"{key}: {value}", protect_paths=False)
        data = bytearray()
        async for chunk in request.stream:
            if len(data) + len(chunk) > payload_limit(self.destination):
                raise NetworkDenied("模型请求超出外发检查预算")
            data.extend(chunk)
        inspected = bytes(data)
        if self.destination == "model":
            check_model_payload(inspected)
        else:
            check_data(inspected, destination=self.destination, category="public")
        if request.headers.get("content-encoding", "identity") != "identity":
            raise NetworkDenied("模型请求不支持压缩或不透明编码")
        try:
            response = await self.transport.handle_async_request(httpx2.Request(
                request.method, str(request.url), headers=request.headers.raw,
                content=bytes(data), extensions=request.extensions,
            ))
        except httpx2.RequestError:
            raise httpx.TransportError("受控模型连接失败", request=request) from None
        # Prevent the SDK's HTTP client from saving a gateway cookie for the next request.
        headers = [(key, value) for key, value in response.headers.raw if key.lower() != b"set-cookie"]
        return httpx.Response(response.status_code, headers=headers,
                              stream=ResponseStream(response), extensions=response.extensions)

    async def aclose(self):
        await self.transport.aclose()


def model_client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ModelTransport(base_url), trust_env=False, follow_redirects=False)
