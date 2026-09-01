import asyncio
import json
import math

import pytest

from openmhs.core.device import AccessLevel, BaseDevice, DeviceCapability, DeviceMetadata
from openmhs.core.protocol import Command, CommandType, MHSProtocol, Response
from openmhs.core.registry import DeviceRegistry


class Device(BaseDevice):
    def __init__(self, delay=0):
        super().__init__(DeviceMetadata("d", "fake", capabilities=[DeviceCapability("x")]))
        self._set_state(
            __import__("openmhs.core.device", fromlist=["DeviceState"]).DeviceState.ONLINE
        )
        self.delay = delay
        self.writes = 0

    async def _do_read(self, capability, **params):
        if self.delay:
            await asyncio.sleep(self.delay)
        return {"ok": True}

    async def _do_write(self, capability, **params):
        self.writes += 1
        return {"ok": True}


@pytest.mark.asyncio
async def test_protocol_rejects_invalid_command_type_without_unhashable_crash():
    protocol = MHSProtocol(DeviceRegistry())
    response = await protocol.execute(Command("bad", [], "missing"))
    assert not response.success
    assert response.error_code == "invalid_command"


@pytest.mark.asyncio
async def test_protocol_hook_value_and_type_errors_are_redacted_internal_errors():
    class ExplodingDevice(Device):
        async def _do_read(self, capability, **params):
            raise self.error_type("driver detail=secret")

    for error_type in (ValueError, TypeError):
        device = ExplodingDevice()
        device.error_type = error_type
        registry = DeviceRegistry()
        await registry.register(device)
        response = await MHSProtocol(registry).execute(Command("bad", CommandType.READ, "d", "x"))
        assert response.error_code == "internal_error"
        assert response.error_message == "Internal server error"
        assert "secret" not in response.to_json()


@pytest.mark.asyncio
async def test_protocol_timeout_and_unsupported_commands_have_typed_errors():
    registry = DeviceRegistry()
    await registry.register(Device(delay=0.1))
    protocol = MHSProtocol(registry)
    response = await protocol.execute(Command("1", CommandType.READ, "d", "x", timeout=0.001))
    assert not response.success
    assert response.error_code == "timeout"
    response = await protocol.execute(Command("2", CommandType.SCRIPT, "d"))
    assert not response.success
    assert response.error_code == "unsupported_command"


@pytest.mark.asyncio
async def test_protocol_defaults_to_read_and_denies_privileged_commands():
    registry = DeviceRegistry()
    await registry.register(Device())
    protocol = MHSProtocol(registry)

    assert (await protocol.execute(Command("read", CommandType.READ, "d", "x"))).success
    for command_type in (CommandType.WRITE, CommandType.RESET, CommandType.DISCONNECT):
        response = await protocol.execute(Command(command_type.name, command_type, "d", "x"))
        assert not response.success
        assert response.error_code == "permission_denied"


@pytest.mark.asyncio
async def test_protocol_redacts_unclassified_internal_errors():
    class ExplodingDevice(Device):
        async def _do_read(self, capability, **params):
            raise RuntimeError("database password=secret")

    registry = DeviceRegistry()
    await registry.register(ExplodingDevice())
    response = await MHSProtocol(registry).execute(
        Command("internal", CommandType.READ, "d", "x"), access_level=AccessLevel.READ
    )
    assert not response.success
    assert response.error_code == "internal_error"
    assert response.error_message == "Internal server error"
    assert "secret" not in response.to_json()


@pytest.mark.asyncio
async def test_protocol_enforces_capability_access():
    registry = DeviceRegistry()
    device = Device()
    device.metadata.capabilities[0].access = AccessLevel.ADMIN
    await registry.register(device)
    response = await MHSProtocol(registry).execute(
        Command("1", CommandType.READ, "d", "x"), access_level=AccessLevel.READ
    )
    assert not response.success
    assert response.error_code == "permission_denied"
    assert device.writes == 0


def test_command_rejects_nested_nonfinite_parameters_but_keeps_bool():
    with pytest.raises(ValueError):
        Command(
            "nested",
            CommandType.READ,
            "d",
            parameters={"payload": {"values": [1, float("nan")]}},
        )
    command = Command(
        "bool",
        CommandType.READ,
        "d",
        parameters={"payload": {"enabled": True}},
    )
    assert command.parameters["payload"]["enabled"] is True


@pytest.mark.asyncio
async def test_execute_rejects_nested_nonfinite_parameters_after_construction():
    registry = DeviceRegistry()
    await registry.register(Device())
    command = Command("nested", CommandType.READ, "d", "x")
    command.parameters = {"payload": {"values": [float("inf")]}}
    response = await MHSProtocol(registry).execute(command)
    assert response.error_code == "invalid_command"
    assert "inf" not in response.error_message.lower()


def test_auth_token_is_repr_private_and_json_rejects_nonfinite_values():
    command = Command("1", CommandType.READ, "d", auth_token="secret")
    assert "secret" not in repr(command)
    assert "auth_token" not in command.to_json()
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            Command("1", CommandType.READ, "d", parameters={"x": value}).to_json()
    with pytest.raises(ValueError):
        Command.from_json(
            '{"command_id":"1","command_type":"READ","device_id":"d","parameters":{"x":NaN}}'
        )


def test_response_error_code_is_round_tripped_last_field():
    response = Response("1", "d", False, error_message="bad", error_code="invalid_command")
    payload = json.loads(response.to_json())
    assert list(payload)[-1] == "error_code"
    assert Response.from_json(response.to_json()).error_code == "invalid_command"
