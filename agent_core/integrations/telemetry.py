"""Export allowlisted operation metadata, numeric usage, and timing."""
import asyncio
from functools import lru_cache
import os
import math
import json

import httpx2
import logfire
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import NoOpTracerProvider, Status

from integrations.network import NetworkDenied, NetworkPolicy, PolicyTransport, origin
from integrations.network_privacy import public_https
from integrations.network_data import check_data

NUMERIC = frozenset({
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_creation.input_tokens", "gen_ai.usage.details.cached_tokens",
    "gen_ai.usage.details.reasoning_tokens", "gen_ai.usage.requests", "operation.cost",
})
OPERATIONS = {"invoke_agent": "agent.run", "chat": "model.request", "execute_tool": "tool.call"}
# Only built-in identifiers may leave the process. MCP and custom names can contain private data.
BUILTIN_TOOLS = frozenset({
    "answer_task", "ask_parent", "ask_user_question", "cancel_task", "delegate_task",
    "delete_memory", "edit_file", "find_files", "grep", "list_files", "list_tasks",
    "load_capability", "message_task", "read_file", "read_memory", "review_diff",
    "search_conversation_history", "search_memory", "search_tools", "shell",
    "web_fetch", "web_search", "write_file", "write_memory", "write_plan",
})


def project_span(span, service_name="ani-bot"):
    source = span.attributes or {}
    attributes = {key: value for key, value in source.items()
                  if key in NUMERIC and type(value) in {int, float} and value >= 0 and math.isfinite(value)}
    operation = source.get("gen_ai.operation.name")
    name = "agent.operation"
    if isinstance(operation, str) and operation in OPERATIONS:
        name = OPERATIONS[operation]
        attributes["gen_ai.operation.name"] = operation
        if operation == "execute_tool":
            tool = source.get("gen_ai.tool.name")
            if isinstance(tool, str) and tool in BUILTIN_TOOLS:
                attributes["gen_ai.tool.name"] = tool
                name = f"{name} {tool}"
    check_data(json.dumps(attributes), destination="telemetry", category="metrics")
    return ReadableSpan(
        name=name, context=span.context, parent=span.parent, kind=span.kind,
        resource=Resource({"service.name": service_name}), attributes=attributes,
        status=Status(span.status.status_code), start_time=span.start_time, end_time=span.end_time,
    )


class UsageExporter(SpanExporter):
    def __init__(self, token, endpoint, service_name="ani-bot"):
        self.token = token
        self.endpoint = endpoint
        self.service_name = service_name

    def export(self, spans):
        payload = encode_spans([project_span(span, self.service_name) for span in spans]).SerializeToString()
        try:
            asyncio.run(self.send(payload))
        except (NetworkDenied, httpx2.HTTPError, OSError, TimeoutError):
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    async def send(self, payload):
        endpoint = str(public_https(self.endpoint))
        policy = NetworkPolicy([origin(endpoint)], [])
        async with httpx2.AsyncClient(transport=PolicyTransport(policy, 0, endpoint),
                                      trust_env=False, follow_redirects=False, timeout=10) as client:
            response = await client.post(endpoint, content=payload, headers={
                "Authorization": self.token, "Content-Type": "application/x-protobuf",
            })
            response.raise_for_status()


@lru_cache(maxsize=4)
def _provider(token, base_url, service_name):
    if not token:
        return NoOpTracerProvider()
    endpoint = logfire.AdvancedOptions(base_url=base_url or None).generate_base_url(token).rstrip("/") + "/v1/traces"
    provider = TracerProvider(resource=Resource({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(UsageExporter(token, endpoint, service_name)))
    return provider


def instrumentation(*, service_name="ani-bot"):
    return {
        "tracer_provider": _provider(os.getenv("LOGFIRE_TOKEN", ""), os.getenv("LOGFIRE_BASE_URL", ""), service_name),
        "meter_provider": NoOpMeterProvider(), "include_content": False,
        "include_binary_content": False, "include_model_request_parameters": False,
    }
