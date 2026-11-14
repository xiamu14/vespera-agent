"""Run-scoped MCP toolsets for Super clones, closed after their run drains."""
from contextlib import AsyncExitStack

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import CombinedToolset


class RunMCP(AbstractCapability):
    def __init__(self, registry):
        self.registry = registry
        self.stack = AsyncExitStack()
        self.tools = None

    def get_toolset(self):
        async def tools(ctx):
            if self.tools is None:
                values = await self.stack.enter_async_context(self.registry.toolsets())
                self.tools = CombinedToolset(values)
            return self.tools
        return tools

    async def wrap_run(self, ctx, *, handler):
        try:
            return await handler()
        finally:
            await self.stack.aclose()
