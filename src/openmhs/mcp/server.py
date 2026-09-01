"""MCP server integration for MHS."""

from __future__ import annotations

import copy
import json
import logging
import weakref
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
from openmhs.transport.security import validate_host_security

logger = logging.getLogger(__name__)


class MHSMcpServer:
    """Expose the current registry catalog through standard MCP transports."""

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
        self._sessions: weakref.WeakSet[Any] = weakref.WeakSet()
        self._remove_discovery_listener: Any | None = None
        self._remove_registry_listener: Any | None = None

        if HAS_MCP:
            self._subscription_bus = InMemorySubscriptionBus()
            self._listen_handler = ListenHandler(self._subscription_bus)
            self._remove_discovery_listener = self._register_discovery_listener()
            self._remove_registry_listener = self.registry.add_change_listener(
                self._on_catalog_changed
            )
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
    def _schema(capability: Any) -> dict[str, Any]:
        parameters = getattr(capability, "parameters", {})
        schema = getattr(capability, "schema", None)
        if isinstance(schema, Mapping) and "properties" in schema:
            return copy.deepcopy(dict(schema))

        properties: dict[str, Any] = {}
        for name in sorted(parameters):
            value = parameters[name]
            if isinstance(value, Mapping):
                properties[name] = copy.deepcopy(dict(value))
            elif isinstance(value, str):
                properties[name] = {"type": "string", "description": value}
            else:
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
                schema = cls._schema(capability)
                required = getattr(capability, "required", None)
                if required:
                    schema["required"] = list(required)
                elif not capability.read_only and getattr(capability, "parameters", None):
                    schema["required"] = sorted(capability.parameters)

                tools.append(
                    {
                        "name": tool_name,
                        "description": (
                            f"{capability.description} on {metadata.manufacturer} "
                            f"{metadata.model} ({metadata.device_id})"
                        ),
                        "inputSchema": schema,
                    }
                )
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
            (
                candidate
                for candidate in device.metadata.capabilities
                if candidate.name == capability_name
            ),
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
        if ctx.session is not None:
            self._sessions.add(ctx.session)

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
        """Notify active sessions and subscription bus of tool list change."""
        for session in tuple(self._sessions):
            try:
                if hasattr(session, "send_tool_list_changed"):
                    await session.send_tool_list_changed()
            except Exception:
                logger.debug("Failed to notify session of tool list change", exc_info=True)
        if self._subscription_bus is not None:
            await self._subscription_bus.publish(ToolsListChanged())

    @asynccontextmanager
    async def _discovery_lifespan(self) -> AsyncIterator[None]:
        """Share discovery startup and cleanup across MCP transports."""
        if self.discovery_manager is not None:
            self.discovery_manager.start()
        try:
            yield
        finally:
            if self._listen_handler is not None:
                self._listen_handler.close()
            self._sessions.clear()
            if self._remove_discovery_listener is not None:
                self._remove_discovery_listener()
                self._remove_discovery_listener = None
            if self._remove_registry_listener is not None:
                self._remove_registry_listener()
                self._remove_registry_listener = None
            if self.discovery_manager is not None:
                await self.discovery_manager.close()

    @asynccontextmanager
    async def _lifespan(self, _server: Any) -> AsyncIterator[dict[str, Any]]:
        async with self._discovery_lifespan():
            yield {}

    async def run_stdio(self) -> None:
        """Run the low-level MCP server over stdio."""
        if not HAS_MCP:
            raise ImportError("mcp package not installed. Install with: pip install openmhs[mcp]")
        assert self._server is not None
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(NotificationOptions(tools_changed=True)),
            )

    async def run_http(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        """Run standard MCP Streamable HTTP server."""
        if not HAS_MCP:
            raise ImportError("mcp package not installed. Install with: pip install openmhs[mcp]")
        assert self._server is not None
        validate_host_security(host)

        import uvicorn

        app = self._server.streamable_http_app(
            streamable_http_path="/mcp",
            host=host,
        )
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()


__all__ = ["HAS_MCP", "MHSMcpServer"]
