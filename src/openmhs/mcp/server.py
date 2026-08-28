"""MCP server integration for MHS.

Exposes MHS devices as MCP tools that AI agents can use.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

try:
    from mcp.server import Server
    from mcp.types import TextContent, Tool
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from openmhs.core.protocol import Command, CommandType, MHSProtocol
from openmhs.core.registry import DeviceRegistry


class MHSMcpServer:
    """MCP server that exposes MHS devices as tools."""

    def __init__(self, registry: Optional[DeviceRegistry] = None, name: str = "mhs"):
        self.registry = registry or DeviceRegistry()
        self.protocol = MHSProtocol(self.registry)
        self.name = name
        self._server: Optional[Any] = None

    async def list_tools(self) -> List[Dict[str, Any]]:
        """List available MHS tools for MCP."""
        tools = []
        
        # Global discovery tool
        tools.append({
            "name": "mhs_discover_devices",
            "description": "Discover all available hardware devices",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        })

        # Per-device tools
        for device_id in self.registry.list_devices():
            device = self.registry.get_device(device_id)
            if device is None:
                continue
            meta = device.metadata
            
            for cap in meta.capabilities:
                tool_name = f"mhs_{device_id}_{cap.name}"
                desc = f"{cap.description} on {meta.manufacturer} {meta.model} ({device_id})"
                schema = {
                    "type": "object",
                    "properties": cap.parameters.copy(),
                }
                if not cap.read_only:
                    schema["required"] = list(cap.parameters.keys())
                
                tools.append({
                    "name": tool_name,
                    "description": desc,
                    "inputSchema": schema,
                })
        
        return tools

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Execute an MHS tool call."""
        if name == "mhs_discover_devices":
            summary = self.registry.get_metadata_summary()
            return [{"type": "text", "text": str(summary)}]

        # Parse tool name: mhs_{device_id}_{capability}
        parts = name.split("_", 2)
        if len(parts) < 3 or parts[0] != "mhs":
            return [{"type": "text", "text": f"Unknown tool: {name}"}]

        device_id = parts[1]
        capability = parts[2]

        # Determine if read or write
        device = self.registry.get_device(device_id)
        if device is None:
            return [{"type": "text", "text": f"Device not found: {device_id}"}]

        cap = None
        for c in device.metadata.capabilities:
            if c.name == capability:
                cap = c
                break

        if cap is None:
            return [{"type": "text", "text": f"Capability not found: {capability}"}]

        cmd_type = CommandType.READ if cap.read_only else CommandType.WRITE

        command = Command(
            command_id=f"mcp_{asyncio.get_event_loop().time()}",
            command_type=cmd_type,
            device_id=device_id,
            capability=capability,
            parameters=arguments,
        )

        response = await self.protocol.execute(command)
        
        if response.success:
            return [{"type": "text", "text": str(response.data)}]
        else:
            return [{"type": "text", "text": f"Error: {response.error_message}"}]

    async def run_stdio(self) -> None:
        """Run MCP server over stdio."""
        if not HAS_MCP:
            raise ImportError("mcp package not installed. Install with: pip install openmhs[mcp]")
        
        from mcp.server.stdio import stdio_server

        server = Server(self.name)
        self._server = server

        @server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            tools = await self.list_tools()
            return [Tool(**t) for t in tools]

        @server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            results = await self.call_tool(name, arguments)
            return [TextContent(**r) for r in results]

        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    async def run_http(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Run MCP server over HTTP (simplified)."""
        from aiohttp import web

        async def tools_handler(request):
            tools = await self.list_tools()
            return web.json_response({"tools": tools})

        async def call_handler(request):
            data = await request.json()
            name = data.get("name", "")
            arguments = data.get("arguments", {})
            results = await self.call_tool(name, arguments)
            return web.json_response({"content": results})

        app = web.Application()
        app.router.add_get("/tools", tools_handler)
        app.router.add_post("/call", call_handler)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        print(f"MHS MCP HTTP server running on http://{host}:{port}")
        
        while True:
            await asyncio.sleep(3600)
