import asyncio
import json
import sys

import pytest

pytest.importorskip("mcp")
from mcp.client import Client
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_client_server_memory_streams

from openmhs.core.device import DeviceCapability, DeviceMetadata, DeviceState
from openmhs.core.registry import DeviceRegistry
from openmhs.mcp.server import MHSMcpServer


class FakeDevice:
    def __init__(self) -> None:
        self.metadata = DeviceMetadata(
            device_id="scope_with_under",
            device_type="scope",
            capabilities=[
                DeviceCapability("read_with_under", "read", {"channel": "channel number"}, True),
                DeviceCapability("write_with_under", "write", {"value": {"type": "number"}}),
            ],
        )
        self.state = DeviceState.ONLINE
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def read(self, capability: str, **params: object) -> dict[str, object]:
        self.calls.append(("read", capability, params))
        return {"capability": capability, "params": params}

    async def write(self, capability: str, **params: object) -> dict[str, object]:
        self.calls.append(("write", capability, params))
        return {"capability": capability, "params": params}


@pytest.mark.asyncio
async def test_catalog_normalizes_schema_and_dispatches_exact_names() -> None:
    registry = DeviceRegistry()
    device = FakeDevice()
    await registry.register(device)
    server = MHSMcpServer(registry)

    tools = await server.list_tools()
    assert [tool["name"] for tool in tools] == [
        "mhs_discover_devices",
        "mhs_scope_with_under_read_with_under",
        "mhs_scope_with_under_write_with_under",
    ]
    read_schema = tools[1]["inputSchema"]
    assert read_schema["properties"] == {
        "channel": {"type": "string", "description": "channel number"}
    }
    assert read_schema["additionalProperties"] is False
    assert tools[2]["inputSchema"]["required"] == ["value"]

    text, is_error = await server._call_tool_result(
        "mhs_scope_with_under_read_with_under", {"channel": 2}
    )
    assert not is_error
    assert json.loads(text)["params"] == {"channel": 2}
    assert device.calls[-1][0:2] == ("read", "read_with_under")

    _, is_error = await server._call_tool_result("mhs_scope_with_under_missing", {})
    assert is_error


@pytest.mark.asyncio
async def test_stdio_client_can_initialize_list_and_call() -> None:
    registry = DeviceRegistry()
    await registry.register(FakeDevice())
    server = MHSMcpServer(registry)

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        serving = asyncio.create_task(
            server._server.run(
                server_streams[0],
                server_streams[1],
                server._server.create_initialization_options(),
            )
        )
        async with ClientSession(client_streams[0], client_streams[1]) as client:
            await client.initialize()
            result = await client.list_tools()
            assert any(tool.name == "mhs_discover_devices" for tool in result.tools)
            call = await client.call_tool("mhs_discover_devices", {})
            assert not call.is_error
            assert json.loads(call.content[0].text)[0]["device_id"] == "scope_with_under"
        await serving


@pytest.mark.asyncio
async def test_cli_stdio_client_can_initialize_and_call() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "openmhs.cli.main",
            "serve",
            "--mode",
            "stdio",
            "--discovery-interval",
            "5",
        ],
    )
    async with stdio_client(parameters) as streams, ClientSession(*streams) as client:
        await client.initialize()
        result = await client.list_tools()
        assert [tool.name for tool in result.tools] == ["mhs_discover_devices"]
        call = await client.call_tool("mhs_discover_devices", {})
        assert not call.is_error
        assert json.loads(call.content[0].text) == []


@pytest.mark.asyncio
async def test_modern_subscription_receives_catalog_change() -> None:
    server = MHSMcpServer(DeviceRegistry())
    async with (
        Client(server._server, mode="2026-07-28") as client,
        client.listen(tools_list_changed=True) as subscription,
    ):
        await server._on_catalog_changed()
        event = await subscription.__anext__()
        assert event.__class__.__name__ == "ToolsListChanged"


@pytest.mark.asyncio
async def test_waveform_generator_capabilities_are_dynamic_tools() -> None:
    class Generator:
        def __init__(self) -> None:
            self.metadata = DeviceMetadata(
                device_id="wavegen-a1", device_type="waveform_generator",
                manufacturer="UNI-T", model="UTG2062X",
                capabilities=[
                    DeviceCapability("identify", read_only=True),
                    DeviceCapability("output_state", parameters={"channel": {"type": "integer"}}, read_only=True),
                    DeviceCapability("output", parameters={"channel": {"type": "integer"}, "enabled": {"type": "boolean"}}),
                    DeviceCapability("waveform", parameters={"channel": {"type": "integer"}, "waveform": {"type": "string"}}),
                    DeviceCapability("amplitude", parameters={"channel": {"type": "integer"}, "amplitude": {"type": "number"}}),
                ],
            )
            self.state = DeviceState.ONLINE
            self.calls = []

        async def read(self, capability, **params):
            self.calls.append(("read", capability, params))
            return {"ok": True}

        async def write(self, capability, **params):
            self.calls.append(("write", capability, params))
            return {"ok": True}

    registry = DeviceRegistry()
    generator = Generator()
    await registry.register(generator)
    server = MHSMcpServer(registry)
    tools = await server.list_tools()
    names = {tool["name"] for tool in tools}
    assert "mhs_wavegen-a1_identify" in names
    assert "mhs_wavegen-a1_output_state" in names
    assert "mhs_wavegen-a1_output" in names
    assert "mhs_wavegen-a1_waveform" in names
    assert "mhs_wavegen-a1_amplitude" in names
    output_schema = next(tool["inputSchema"] for tool in tools if tool["name"].endswith("_output"))
    assert output_schema["required"] == ["channel", "enabled"]
    await server._call_tool_result("mhs_wavegen-a1_output", {"channel": 2, "enabled": True})
    await server._call_tool_result("mhs_wavegen-a1_output_state", {"channel": 2})
    assert generator.calls == [("write", "output", {"channel": 2, "enabled": True}), ("read", "output_state", {"channel": 2})]


@pytest.mark.asyncio
async def test_oscilloscope_display_capabilities_are_dynamic_tools() -> None:
    from openmhs.adapters.oscilloscope import OscilloscopeDevice

    class Instrument:
        def __init__(self):
            self.queries = []
            self.writes = []
            self.closed = 0
            self.timeout = None

        def query(self, command):
            self.queries.append(command)
            return "UNI-T,UPO6102N,SN,1.0" if command == "*IDN?" else "ON"

        def write(self, command):
            self.writes.append(command)

        def close(self):
            self.closed += 1

    class Manager:
        def __init__(self, instrument):
            self.instrument = instrument

        def open_resource(self, resource):
            return self.instrument

    instrument = Instrument()
    device = OscilloscopeDevice("scope-001", "USB::SCOPE::INSTR", lambda: Manager(instrument))
    assert await device.connect()

    registry = DeviceRegistry()
    await registry.register(device)
    server = MHSMcpServer(registry)

    tools = await server.list_tools()
    names = {tool["name"] for tool in tools}
    assert "mhs_scope-001_channel_display" in names
    assert "mhs_scope-001_channel_display_state" in names

    display_schema = next(tool["inputSchema"] for tool in tools if tool["name"] == "mhs_scope-001_channel_display")
    assert display_schema["required"] == ["channel", "enabled"]

    text, is_error = await server._call_tool_result("mhs_scope-001_channel_display", {"channel": 1, "enabled": False})
    assert not is_error
    assert json.loads(text) == {"channel": 1, "enabled": False}
    assert instrument.writes == [":CHANnel1:DISPlay OFF"]

    text, is_error = await server._call_tool_result("mhs_scope-001_channel_display_state", {"channel": 1})
    assert not is_error
    assert json.loads(text) == {"channel": 1, "enabled": True}
    assert instrument.queries == ["*IDN?", ":CHANnel1:DISPlay?"]
