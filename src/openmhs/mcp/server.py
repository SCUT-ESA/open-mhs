"""MCP server integration for MHS."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

try:
    from mcp.server import Server
    from mcp.server.context import ServerRequestContext
    from mcp.server.lowlevel.server import NotificationOptions
    from mcp.server.stdio import stdio_server
    from mcp.server.subscriptions import (
        InMemorySubscriptionBus,
        ListenHandler,
        ToolsListChanged,
    )
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        PaginatedRequestParams,
        SubscriptionsListenRequestParams,
        SubscriptionsListenResult,
        TextContent,
        Tool,
    )

    HAS_MCP = True
except ImportError:  # pragma: no cover - exercised without the optional extra
    HAS_MCP = False

from openmhs.core.device import Device
from openmhs.core.protocol import MHSProtocol
from openmhs.core.registry import DeviceRegistry


class MHSMcpServer:
    """Expose the current registry catalog through MCP stdio."""

    def __init__(
        self,
        registry: DeviceRegistry | None = None,
        name: str = "mhs",
        discovery_manager: Any | None = None,
    ) -> None:
        self.registry = registry or DeviceRegistry()
        self.protocol = MHSProtocol(self.registry)
        self.name = name
        self.discovery_manager = discovery_manager
        self._server: Any | None = None
        self._subscription_bus: Any | None = None
        self._listen_handler: Any | None = None
        self._legacy_sessions: dict[int, tuple[Any, str]] = {}
        self._remove_discovery_listener: Any | None = None

        if HAS_MCP:
            self._subscription_bus = InMemorySubscriptionBus()
            self._listen_handler = ListenHandler(self._subscription_bus)
            self._remove_discovery_listener = self._register_discovery_listener()
            self._server = Server(
                self.name,
                lifespan=self._lifespan,
                on_list_tools=self.on_list_tools,
                on_call_tool=self.on_call_tool,
                on_subscriptions_listen=self._listen_handler,
            )

    def _register_discovery_listener(self) -> Any | None:
        if self.discovery_manager is None:
            return None
        return self.discovery_manager.add_change_listener(self._on_catalog_changed)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def _schema(parameters: Mapping[str, Any]) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for name in sorted(parameters):
            value = parameters[name]
            if isinstance(value, Mapping):
                properties[name] = copy.deepcopy(dict(value))
            elif isinstance(value, str):
                properties[name] = {"type": "string", "description": value}
            else:
                # Keep malformed legacy metadata valid without guessing a type.
                properties[name] = {"type": "string", "description": str(value)}
        return {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }

    @classmethod
    def _catalog(
        cls, devices: tuple[Device, ...]
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
        tools: list[dict[str, Any]] = [
            {
                "name": "mhs_discover_devices",
                "description": "Discover all available hardware devices",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }
        ]
        bindings: dict[str, tuple[str, str]] = {}
        for device in sorted(devices, key=lambda item: item.metadata.device_id):
            metadata = device.metadata
            for capability in sorted(metadata.capabilities, key=lambda item: item.name):
                tool_name = f"mhs_{metadata.device_id}_{capability.name}"
                tools.append(
                    {
                        "name": tool_name,
                        "description": (
                            f"{capability.description} on {metadata.manufacturer} "
                            f"{metadata.model} ({metadata.device_id})"
                        ),
                        "inputSchema": cls._schema(capability.parameters),
                    }
                )
                if not capability.read_only:
                    tools[-1]["inputSchema"]["required"] = sorted(capability.parameters)
                bindings[tool_name] = (metadata.device_id, capability.name)
        tools.sort(key=lambda item: item["name"])
        return tools, bindings

    @staticmethod
    def _metadata(devices: tuple[Device, ...]) -> list[dict[str, Any]]:
        result = []
        for device in sorted(devices, key=lambda item: item.metadata.device_id):
            metadata = device.metadata
            result.append(
                {
                    "device_id": metadata.device_id,
                    "device_type": metadata.device_type,
                    "manufacturer": metadata.manufacturer,
                    "model": metadata.model,
                    "state": device.state.name,
                    "capabilities": sorted(cap.name for cap in metadata.capabilities),
                    "tags": sorted(metadata.tags),
                    "location": metadata.location,
                }
            )
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the business-level tool dictionaries for compatibility."""
        devices = await self.registry.snapshot()
        return self._catalog(devices)[0]

    async def _call_tool_result(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        devices = await self.registry.snapshot()
        _, bindings = self._catalog(devices)
        if name == "mhs_discover_devices":
            return self._json(self._metadata(devices)), False

        binding = bindings.get(name)
        if binding is None:
            return self._json({"error": f"Unknown or stale tool: {name}"}), True

        device_id, capability_name = binding
        device = next(
            (candidate for candidate in devices if candidate.metadata.device_id == device_id),
            None,
        )
        if device is None:
            return self._json({"error": f"Device not found: {device_id}"}), True
        capability = next(
            (candidate for candidate in device.metadata.capabilities if candidate.name == capability_name),
            None,
        )
        if capability is None:
            return self._json({"error": f"Capability not found: {capability_name}"}), True

        try:
            params = arguments or {}
            if capability.read_only:
                value = await device.read(capability_name, **params)
            else:
                value = await device.write(capability_name, **params)
            return self._json(value), False
        except Exception as exc:  # noqa: BLE001 - MCP boundary converts failures to results
            return self._json({"error": str(exc)}), True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a business-level call, retaining the legacy dictionary shape."""
        text, _ = await self._call_tool_result(name, arguments)
        return [{"type": "text", "text": text}]

    def _remember_session(self, ctx: ServerRequestContext[Any, Any]) -> None:
        self._legacy_sessions[id(ctx.session)] = (ctx.session, ctx.protocol_version)

    async def on_list_tools(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        """Low-level typed ``tools/list`` callback."""
        self._remember_session(ctx)
        dictionaries = await self.list_tools()
        return ListToolsResult(
            tools=[Tool(**tool) for tool in dictionaries],
        )

    async def on_call_tool(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        """Low-level typed ``tools/call`` callback."""
        self._remember_session(ctx)
        text, is_error = await self._call_tool_result(params.name, params.arguments or {})
        return CallToolResult(content=[TextContent(text=text)], is_error=is_error)

    async def on_subscriptions_listen(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: SubscriptionsListenRequestParams,
    ) -> SubscriptionsListenResult:
        """Delegate modern subscriptions to the single retained handler."""
        if self._listen_handler is None:
            raise RuntimeError("MCP subscriptions are unavailable")
        return cast(SubscriptionsListenResult, await self._listen_handler(ctx, params))

    async def _on_catalog_changed(self) -> None:
        """Notify both protocol generations after a committed add/remove."""
        stale: list[int] = []
        for key, (session, version) in tuple(self._legacy_sessions.items()):
            if version == "2026-07-28":
                continue
            try:
                await session.send_tool_list_changed()
            except Exception:  # noqa: BLE001 - disconnected clients are discarded
                stale.append(key)
        for key in stale:
            self._legacy_sessions.pop(key, None)
        if self._subscription_bus is not None:
            await self._subscription_bus.publish(ToolsListChanged())

    @asynccontextmanager
    async def _lifespan(self, _server: Any) -> AsyncIterator[dict[str, Any]]:
        if self.discovery_manager is not None:
            self.discovery_manager.start()
        try:
            yield {}
        finally:
            if self._listen_handler is not None:
                self._listen_handler.close()
            if self.discovery_manager is not None:
                await self.discovery_manager.close()
            if self._remove_discovery_listener is not None:
                self._remove_discovery_listener()
                self._remove_discovery_listener = None
            self._legacy_sessions.clear()

    async def run_stdio(self) -> None:
        """Run the low-level MCP server over stdio."""
        if not HAS_MCP:
            raise ImportError("mcp package not installed. Install with: pip install openmhs[mcp]")
        assert self._server is not None
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(
                    NotificationOptions(tools_changed=True)
                ),
            )

    async def run_http(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Run the legacy custom HTTP gateway (not standard MCP transport)."""
        from aiohttp import web  # type: ignore[import-not-found]

        async def tools_handler(request: Any) -> Any:
            return web.json_response({"tools": await self.list_tools()})

        async def call_handler(request: Any) -> Any:
            data = await request.json()
            results = await self.call_tool(data.get("name", ""), data.get("arguments", {}))
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


__all__ = ["HAS_MCP", "MHSMcpServer"]
