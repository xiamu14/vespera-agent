import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
import unittest
from unittest.mock import AsyncMock, patch

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.workspaces import LocalWorkspaceBackend
from vespera_agent.bridge import Bridge

from integrations.telemetry import UsageExporter, instrumentation


class LogfireStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_dotenv_enables_ani_trace_export(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ), \
             patch.object(Agent, "_instrument_default", False), \
             patch("vespera_agent.bridge.AgentCore"), patch("vespera_agent.bridge.Thread"), \
             patch.object(UsageExporter, "send", new_callable=AsyncMock) as send:
            os.environ.pop("LOGFIRE_TOKEN", None)
            os.environ.pop("LOGFIRE_BASE_URL", None)
            root = Path(directory)
            bridge = Bridge(root, root)
            bridge.loop.close()
            self.assertIs(Agent._instrument_default, False)

            (root / ".env").write_text("LOGFIRE_TOKEN=pylf_v1_us_ani_test\n")
            bridge = Bridge(root, root)
            bridge.loop.close()
            provider = instrumentation(service_name="ani-bot")["tracer_provider"]
            self.addCleanup(provider.shutdown)

            def read_file(path: Literal["private-tool-argument"]) -> str:
                return "private-tool-result"

            def private_mcp_tool() -> str:
                return "private-mcp-result"

            await Agent(TestModel(), tools=[read_file, private_mcp_tool]).run("private-ani-prompt")
            self.assertTrue(await asyncio.to_thread(provider.force_flush))
            payload = send.call_args.args[0]
            self.assertIn(b"ani-bot", payload)
            self.assertIn(b"gen_ai.usage.input_tokens", payload)
            self.assertNotIn(b"private-", payload)
            self.assertNotIn(b"private_mcp_tool", payload)
            batch = ExportTraceServiceRequest.FromString(payload)
            spans = {span.name: span for resource in batch.resource_spans
                     for scope in resource.scope_spans for span in scope.spans}
            root_span = spans["agent.run"]
            for name in ("model.request", "tool.call read_file", "tool.call"):
                self.assertEqual(spans[name].parent_span_id, root_span.span_id)
                self.assertEqual(spans[name].trace_id, root_span.trace_id)


class BundledToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_finder_path_reaches_bundled_tools_in_child_commands(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "bin"
            tools.mkdir()
            executable = tools / "rg"
            executable.write_text('#!/bin/sh\nprintf "bundled-rg"\n')
            executable.chmod(0o755)
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}), \
                 patch("vespera_agent.bridge.sysconfig.get_path", return_value=str(root)), \
                 patch("vespera_agent.bridge.AgentCore"), \
                 patch("vespera_agent.bridge.Thread"), \
                 patch("vespera_agent.bridge.load_dotenv"):
                bridge = Bridge(root, root)
                try:
                    backend = LocalWorkspaceBackend(root)
                    result = await backend.run(["/bin/sh", "-c", "rg --version"])
                    self.assertEqual(result.exit_code, 0, result.stderr)
                    self.assertEqual(result.stdout, "bundled-rg")
                    self.assertEqual(os.environ["PATH"], f"{tools}:/usr/bin:/bin")
                finally:
                    bridge.loop.close()
