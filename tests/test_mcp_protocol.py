import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe():
    parameters = StdioServerParameters(command=sys.executable, args=["mcp_server.py"])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert "list_products" in names
            assert "save_listing_draft" in names
            result = await session.call_tool("list_products", {})
            assert not result.isError


def test_stdio_protocol():
    asyncio.run(probe())
