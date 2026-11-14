"""Offline regressions for normalization and MCP HTTP header boundaries."""
import base64
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import httpx2

from integrations.network import NetworkDenied, NetworkPolicy, PolicyTransport
from integrations.network_privacy import check_content
from integrations.shell_http import prepare_curl
from integrations.web_http import fetch_http


def encoded(value):
    return base64.b64encode(value.encode()).decode()


def escaped(value):
    return "".join(f"\\u{ord(char):04x}" for char in value)


class NormalizationSecurityTests(unittest.TestCase):
    def test_json_escaped_base64_is_inspected(self):
        secret = encoded("password=private-value")
        values = [
            '{"data":"' + escaped(secret) + '"}',
            json.dumps({"data": secret}),
            json.dumps({"data": encoded(quote("password=private-value", safe=""))}),
            encoded(json.dumps({"data": secret})),
            json.dumps({"nested": json.dumps({"data": secret})}),
        ]
        for value in values:
            with self.subTest(value=value), self.assertRaises(NetworkDenied):
                check_content(value)

    def test_duplicate_json_keys_cannot_drop_encoded_credentials(self):
        value = '{"data":"' + escaped(encoded("password=private-value")) + '","data":"ordinary"}'
        with self.assertRaisesRegex(NetworkDenied, "重复"):
            check_content(value)

    def test_short_known_environment_secrets_and_urlsafe_encoding(self):
        for secret in ("canary12", "canary123", "canary1234", "known-secret-123"):
            with self.subTest(secret=secret), patch.dict(os.environ, {"REGRESSION_API_KEY": secret}):
                standard = encoded(secret)
                for value in (standard, standard.rstrip("="),
                              json.dumps({"data": standard}),
                              '{"data":"' + escaped(standard) + '"}'):
                    with self.subTest(value=value), self.assertRaises(NetworkDenied):
                        check_content(value)
        with patch.dict(os.environ, {"REGRESSION_API_KEY": "~~~~private"}):
            for encoder in (base64.b64encode, base64.urlsafe_b64encode):
                value = encoder(b"~~~~private").decode()
                for representation in (value, value.rstrip("=")):
                    with self.subTest(value=representation), self.assertRaises(NetworkDenied):
                        check_content(representation)

    def test_ordinary_text_json_and_identifiers_remain_allowed(self):
        for value in (
            "ordinary public query",
            "0123456789abcdef0123456789abcdef",
            "00000000000000000000000000000000",
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "a_long_project_identifier_with_underscores",
            "build_identifier= ordinary public value",
            json.dumps({"request_id": "abcdef0123456789abcdef0123456789", "message": "hello world"}),
            json.dumps({"items": ["ordinary text", "plain value"], "count": 2}),
            json.dumps({"data": encoded("ordinary public text")}),
            "a comment with {an incomplete expression",
        ):
            with self.subTest(value=value):
                check_content(value)

    def test_budget_exhaustion_fails_closed(self):
        with self.assertRaisesRegex(NetworkDenied, "预算"):
            check_content("too much content", max_bytes=3)
        with patch("integrations.network_privacy.MAX_NORMALIZATION_CANDIDATES", 5):
            with self.assertRaisesRegex(NetworkDenied, "候选数"):
                check_content(json.dumps(["first ordinary text", "second ordinary text", "third ordinary text"]))
        with patch("integrations.network_privacy.NORMALIZATION_BYTE_MULTIPLIER", 1):
            value = '{"message":"a normal string with escaped \\u0061"}'
            with self.assertRaisesRegex(NetworkDenied, "字节数"):
                check_content(value, max_bytes=len(value))
        value = "ordinary nested content"
        for _ in range(5):
            value = encoded(value)
        with patch("integrations.network_privacy.MAX_NORMALIZATION_DEPTH", 2):
            with self.assertRaisesRegex(NetworkDenied, "层数"):
                check_content(value)

    def test_normalized_binary_and_sensitive_path_are_rejected(self):
        for value in (b"\x00binary", b"\xffbinary", encoded(".env/private"),
                      json.dumps({"value": "\u0000binary"}),
                      "data:application/octet-stream;base64," + base64.b64encode(b"\x00binary data").decode()):
            with self.subTest(value=value), self.assertRaises(NetworkDenied):
                check_content(value)
        check_content(".env/public-example", protect_paths=False)

    def test_explicit_larger_model_budget_remains_supported(self):
        value = json.dumps({"message": "ordinary text " * 80000})
        with self.assertRaisesRegex(NetworkDenied, "预算"):
            check_content(value)
        check_content(value, max_bytes=16 * 1024 * 1024, forbid_binary=True)

    def test_cache_rechecks_credentials_and_input_budget(self):
        value = "cache-regression-value"
        check_content(value)
        with patch.dict(os.environ, {"NEW_REGRESSION_API_KEY": value}):
            with self.assertRaises(NetworkDenied):
                check_content(value)
        with self.assertRaisesRegex(NetworkDenied, "预算"):
            check_content(value, max_bytes=1)


class MCPHeaderSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = PolicyTransport(NetworkPolicy(["https://mcp.example"], []), 0,
                                         "https://mcp.example/mcp", check_payload=True)
        self.resolve = AsyncMock(return_value="93.184.216.34")
        self.send = AsyncMock(return_value=httpx2.Response(200, content=b"ok"))
        self.transport.policy.resolve = self.resolve
        await self.transport.transport.aclose()
        self.transport.transport = httpx2.MockTransport(self.send)

    async def asyncTearDown(self):
        await self.transport.aclose()

    async def request(self, headers=None, url="https://mcp.example/mcp", content=None):
        return await self.transport.handle_async_request(httpx2.Request(
            "POST", url, headers=headers,
            content=content or b'{"jsonrpc":"2.0","method":"ping"}',
        ))

    async def test_sensitive_plain_encoded_duplicate_headers_never_send(self):
        values = [
            {"X-Data": "password=private-value"},
            {"X-Data": encoded("password=private-value")},
            {"X-Data": '{"data":"' + escaped(encoded("password=private-value")) + '"}'},
            [("X-Data", "ordinary"), ("X-Data", "password=private-value")],
        ]
        with patch.dict(os.environ, {"HEADER_REGRESSION_API_KEY": "canary12"}):
            values.append({"X-Data": encoded("canary12")})
            for headers in values:
                with self.subTest(headers=headers), self.assertRaises(NetworkDenied):
                    await self.request(headers)
        self.resolve.assert_not_awaited()
        self.send.assert_not_awaited()

    async def test_public_headers_and_body_reach_mock_unchanged(self):
        response = await self.request({"X-Request-Id": "a_long_normal_identifier", "Accept": "application/json"})
        self.assertEqual(response.status_code, 200)
        request = self.send.call_args.args[0]
        self.assertEqual(request.headers["X-Request-Id"], "a_long_normal_identifier")
        self.assertEqual(request.headers["Host"], "mcp.example")
        self.assertEqual(await request.aread(), b'{"jsonrpc":"2.0","method":"ping"}')

    async def test_trusted_auth_requires_exact_value_and_original_origin(self):
        await self.transport.aclose()
        auth = {"Authorization": "Bearer configured-credential", "X-API-Key": "configured-key"}
        self.transport = PolicyTransport(NetworkPolicy(["https://mcp.example", "https://other.example"], []),
                                         0, "https://mcp.example/mcp", check_payload=True,
                                         trusted_auth_headers=auth)
        await self.transport.transport.aclose()
        self.transport.policy.resolve = self.resolve
        self.transport.transport = httpx2.MockTransport(self.send)
        await self.request(auth)
        self.assertEqual(self.send.await_count, 1)
        with self.assertRaises(NetworkDenied):
            await self.request({"Authorization": "Bearer changed-credential"})
        with self.assertRaises(NetworkDenied):
            await self.request(auth, url="https://other.example/mcp")
        self.assertEqual(self.send.await_count, 1)
        self.assertEqual(self.resolve.await_count, 1)

    async def test_declaring_normal_header_trusted_does_not_exempt_it(self):
        await self.transport.aclose()
        self.transport = PolicyTransport(NetworkPolicy(["https://mcp.example"], []), 0,
                                         "https://mcp.example", check_payload=True,
                                         trusted_auth_headers={"X-Data": "password=private-value"})
        await self.transport.transport.aclose()
        self.transport.policy.resolve = self.resolve
        self.transport.transport = httpx2.MockTransport(self.send)
        with self.assertRaises(NetworkDenied):
            await self.request({"X-Data": "password=private-value"})
        self.send.assert_not_awaited()

    async def test_untrusted_auth_and_sensitive_body_are_rejected(self):
        with self.assertRaises(NetworkDenied):
            await self.request({"Authorization": "Bearer arbitrary-credential"})
        body = '{"data":"' + escaped(encoded("password=private-value")) + '"}'
        with self.assertRaises(NetworkDenied):
            await self.request(content=body.encode())
        self.resolve.assert_not_awaited()
        self.send.assert_not_awaited()

    async def test_service_revocation_after_dns_still_blocks_send(self):
        self.transport.before_send = unittest.mock.Mock(side_effect=[None, NetworkDenied("revoked")])
        with self.assertRaisesRegex(NetworkDenied, "revoked"):
            await self.request({"X-Data": "ordinary value"})
        self.resolve.assert_awaited_once()
        self.send.assert_not_awaited()

    async def test_registry_binds_only_configured_protocol_auth(self):
        from integrations.mcp import Config, MCPRegistry, Network, Server
        with TemporaryDirectory() as directory:
            registry = MCPRegistry(Path(directory) / "mcp.json")
            config = Config(mcpServers={"demo": Server(
                url="https://mcp.example/mcp", defer_loading=False,
                headers={"Authorization": "Bearer ${MCP_REGRESSION_TOKEN}", "X-Public": "ordinary value"},
            )}, network=Network(allowed_origins=["https://mcp.example"]))
            with patch.dict(os.environ, {"MCP_REGRESSION_TOKEN": "configured-credential"}), \
                    patch("integrations.mcp.MCPConnection") as connection, \
                    patch("integrations.mcp.httpx2.AsyncClient", wraps=httpx2.AsyncClient) as client:
                async with AsyncExitStack() as stack:
                    await registry.build(config, stack)
                    transport = client.call_args.kwargs["transport"]
                    self.assertEqual(transport.trusted_auth_headers,
                                     {("authorization", "Bearer configured-credential")})
                    connection.assert_called_once()


class ControlledHTTPEncodingTests(unittest.IsolatedAsyncioTestCase):
    async def test_encoded_secret_is_blocked_before_any_http_send(self):
        body = '{"data":"' + escaped(encoded("password=private-value")) + '"}'
        with patch("httpx2.AsyncHTTPTransport.handle_async_request", new_callable=AsyncMock) as send, \
                patch("integrations.network.NetworkPolicy.resolve", new_callable=AsyncMock) as resolve:
            with self.assertRaises(NetworkDenied):
                request = prepare_curl(["https://example.com", "--json", body])
                await fetch_http(request.url, method=request.method, body=request.body, headers=request.headers)
            send.assert_not_awaited()
            resolve.assert_not_awaited()
