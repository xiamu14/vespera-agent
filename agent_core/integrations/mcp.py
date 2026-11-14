"""Trusted MCP configuration, refreshed by the framework before each model request."""
import asyncio
import hashlib
import json
import os
import re
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from pathlib import Path

import httpx2
from fastmcp.client.transports import StdioTransport
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, DynamicToolset

from integrations.config_home import user_config
from integrations.network import NetworkDenied, NetworkPolicy, PolicyTransport
from integrations.network_data import check_data


class MCPConfigError(OSError):
    pass


class MCPConnection(MCPToolset):
    classify_public_args = False
    service_name = None
    network_fingerprint = None
    async def get_tools(self, ctx):
        tools = await super().get_tools(ctx)
        return {
            name: replace(tool, tool_def=replace(tool.tool_def, metadata={**(tool.tool_def.metadata or {}),
                                                                          "ani_mcp": True,
                                                                          "ani_mcp_service": self.service_name,
                                                                          "ani_mcp_policy": self.network_fingerprint,
                                                                          "ani_mcp_public_classification": self.classify_public_args}))
            for name, tool in tools.items()
        }

    async def __aenter__(self):
        try:
            return await super().__aenter__()
        except RuntimeError as exc:
            # FastMCP wraps transport failures; programming errors must still propagate.
            if isinstance(exc.__cause__, (OSError, httpx2.HTTPError)):
                raise MCPConfigError(f"MCP 连接失败：{exc.__cause__}") from exc
            raise


class Server(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str | None = Field(default=None, min_length=1)
    command: str | None = Field(default=None, min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    allowed_tools: list[str] | None = None
    defer_loading: bool = True
    classify_public_args: bool = False
    """Opt in only when tool arguments may also be sent to the configured classifier."""

    @model_validator(mode="after")
    def transport_fields(self):
        if (self.url is None) == (self.command is None):
            raise ValueError("必须且只能提供 url 或 command")
        if self.url is not None and (self.args or self.env or self.cwd is not None):
            raise ValueError("HTTP 配置不能包含 args、env 或 cwd")
        if self.command is not None and (not self.command.strip() or self.headers):
            raise ValueError("stdio command 不能为空白，也不能包含 HTTP headers")
        return self


class Network(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    allowed_origins: list[str] = Field(default_factory=list)
    allow_private_origins: list[str] = Field(default_factory=list)
    connect_timeout: float = Field(default=5, gt=0, le=60)
    init_timeout: float = Field(default=5, gt=0, le=60)
    read_timeout: float = Field(default=30, gt=0, le=300)
    call_timeout: float = Field(default=60, gt=0, le=600)
    connect_retries: int = Field(default=1, ge=0, le=2)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mcpServers: dict[str, Server] = Field(default_factory=dict)
    network: Network = Field(default_factory=Network)


def network_fingerprint(server, network):
    data = json.dumps([server.model_dump(), network.model_dump()], sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def expand_env(value: str) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise MCPConfigError(f"MCP 环境变量未设置：{name}")
        return os.environ[name]
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)


class MCPRegistry(AbstractCapability):
    id = "mcp"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path.expanduser().resolve() if path is not None else user_config("CODER_MCP_CONFIG", "mcp.json")
        self.authorize_stdio = None
        self.allow_stdio = lambda: True

    def get_instructions(self) -> str:
        return json.dumps({"mcp_config_path": str(self.path)}, ensure_ascii=False)

    def check_service(self, name, expected):
        config = self.read()
        server = config.mcpServers.get(name)
        if server is None or not server.enabled or network_fingerprint(server, config.network) != expected:
            raise NetworkDenied("MCP 服务未登记或策略已变化，请重新加载工具")

    def read(self) -> Config:
        try:
            data = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if "CODER_MCP_CONFIG" in os.environ:
                raise MCPConfigError("指定的 MCP 配置不存在") from None
            return Config()
        except UnicodeError:
            raise MCPConfigError("MCP 配置必须是 UTF-8") from None
        try:
            return Config.model_validate(json.loads(data))
        except (ValidationError, json.JSONDecodeError) as exc:
            # Validation errors may contain credential values; report locations only.
            locations = [".".join(map(str, error["loc"])) for error in exc.errors()] if isinstance(exc, ValidationError) else ["JSON"]
            raise MCPConfigError("MCP 配置无效：" + ", ".join(locations)) from None

    @asynccontextmanager
    async def toolsets(self):
        async with AsyncExitStack() as stack:
            previous = None
            current: AbstractToolset | None = None

            async def refresh(ctx):
                nonlocal previous, current
                config = self.read()
                state = (config, self.allow_stdio())
                if state != previous:
                    selected = config.model_copy(update={"mcpServers": {
                        name: server for name, server in config.mcpServers.items()
                        if server.command is None or state[1]
                    }})
                    current = await self.build(selected, stack)
                    previous = state
                return current

            # DynamicToolset owns session changes; clients stay open through session teardown.
            yield [DynamicToolset(refresh)]

    async def build(self, config: Config, stack: AsyncExitStack) -> AbstractToolset:
        network = config.network
        try:
            policy = NetworkPolicy(network.allowed_origins, network.allow_private_origins)
            for server in config.mcpServers.values():
                if server.enabled and server.url is not None:
                    policy.check_url(httpx2.URL(server.url))
        except (ValueError, httpx2.InvalidURL):
            raise MCPConfigError("MCP 网络配置无效") from None

        async def call(ctx, call_tool, name, tool_args):
            check_data(json.dumps(tool_args, ensure_ascii=False), destination="mcp", category="project")
            async with asyncio.timeout(network.call_timeout):
                return await call_tool(name, tool_args)

        toolsets = []
        for name, server in config.mcpServers.items():
            if not server.enabled:
                continue
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
                raise MCPConfigError("MCP 名称须以字母开头，仅包含字母、数字、下划线和连字符")
            client: httpx2.AsyncClient | None = None
            snapshot = network_fingerprint(server, network)
            def validate(name=name, snapshot=snapshot):
                self.check_service(name, snapshot)
            transport: str | StdioTransport
            if server.url is not None:
                headers = {key: expand_env(value) for key, value in server.headers.items()}
                client = await stack.enter_async_context(httpx2.AsyncClient(
                    transport=PolicyTransport(policy, network.connect_retries, server.url, check_payload=True,
                                              before_send=validate, trusted_auth_headers=headers), headers=headers,
                    timeout=httpx2.Timeout(network.read_timeout, connect=network.connect_timeout),
                    trust_env=False, follow_redirects=False,
                ))
                transport = server.url
            else:
                assert server.command is not None
                cwd = Path(server.cwd).expanduser() if server.cwd is not None else self.path.parent
                cwd = (self.path.parent / cwd).resolve()
                if not cwd.is_dir():
                    raise MCPConfigError("stdio MCP 的 cwd 不是有效目录")
                command, args = os.path.expanduser(server.command), server.args
                execution_env = {}
                if self.authorize_stdio is not None:
                    command, args, execution_env = await self.authorize_stdio(name, server, cwd, stack)
                # The framework owns process teardown; stderr must not corrupt the TUI.
                stderr = stack.enter_context(tempfile.TemporaryFile(mode="w+", encoding="utf-8"))
                transport = StdioTransport(
                    command=command, args=args,
                    env={**execution_env, **{key: expand_env(value) for key, value in server.env.items()}},
                    cwd=str(cwd), keep_alive=False, log_file=stderr,
                )
            toolset: AbstractToolset = MCPConnection(
                transport, id=name, http_client=client, init_timeout=network.init_timeout,
                read_timeout=network.read_timeout, process_tool_call=call,
                include_instructions=False, max_retries=0, tool_error_behavior="failed",
            )
            toolset.classify_public_args = server.classify_public_args
            toolset.service_name = name
            toolset.network_fingerprint = snapshot
            if server.allowed_tools is not None:
                allowed = frozenset(server.allowed_tools)
                toolset = toolset.filtered(lambda ctx, tool, allowed=allowed: tool.name in allowed)
            if server.defer_loading:
                toolset = toolset.defer_loading()
            toolsets.append(toolset.prefixed(name))
        return CombinedToolset(toolsets)
