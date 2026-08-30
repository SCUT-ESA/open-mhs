"""Channel display capability tests without physical VISA hardware."""

import pytest

from openmhs.adapters.oscilloscope import OscilloscopeDevice
from openmhs.core.device import CapabilityError


class Instrument:
    def __init__(self):
        self.queries = []
        self.writes = []
        self.closed = 0
        self.timeout = None
        self.responses = {}

    def query(self, command):
        self.queries.append(command)
        if command == "*IDN?":
            return "UNI-T,UPO6102N,SN,1.0"
        return self.responses.get(command, "ON")

    def write(self, command):
        self.writes.append(command)

    def close(self):
        self.closed += 1


class Manager:
    def __init__(self, instrument):
        self.instrument = instrument
        self.closed = 0

    def open_resource(self, resource):
        return self.instrument

    def close(self):
        self.closed += 1


@pytest.fixture
def connected():
    instrument = Instrument()
    manager = Manager(instrument)
    device = OscilloscopeDevice("scope-test", "USB::SCOPE::INSTR", lambda: manager)
    return device, instrument


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled, command", [(True, "ON"), (False, "OFF")])
async def test_channel_display_write(connected, enabled, command):
    device, instrument = connected
    assert await device.connect()
    result = await device.write("channel_display", channel=1, enabled=enabled)
    assert instrument.writes == [f":CHANnel1:DISPlay {command}"]
    assert result == {"channel": 1, "enabled": enabled}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_response, expected_bool",
    [
        ("ON", True),
        ("1", True),
        ("TRUE", True),
        ("OFF", False),
        ("0", False),
        ("FALSE", False),
    ],
)
async def test_channel_display_state_read(connected, raw_response, expected_bool):
    device, instrument = connected
    await device.connect()
    instrument.responses[":CHANnel2:DISPlay?"] = raw_response
    result = await device.read("channel_display_state", channel=2)
    assert instrument.queries == ["*IDN?", ":CHANnel2:DISPlay?"]
    assert result == {"channel": 2, "enabled": expected_bool}


@pytest.mark.asyncio
async def test_channel_display_invalid_parameters(connected):
    device, instrument = connected
    await device.connect()
    # Invalid channel numbers
    for channel in (0, 3, 1.5, True, "bad"):
        with pytest.raises(CapabilityError):
            await device.write("channel_display", channel=channel, enabled=True)
        with pytest.raises(CapabilityError):
            await device.read("channel_display_state", channel=channel)

    # Invalid enabled types
    for enabled in ("yes", 1, 0, None, [True]):
        with pytest.raises(CapabilityError):
            await device.write("channel_display", channel=1, enabled=enabled)

    # Unrecognized query response
    instrument.responses[":CHANnel1:DISPlay?"] = "UNKNOWN"
    with pytest.raises(CapabilityError, match="Unrecognized display state"):
        await device.read("channel_display_state", channel=1)

    assert instrument.writes == []


@pytest.mark.asyncio
async def test_channel_display_state_is_read_only(connected):
    device, _ = connected
    await device.connect()
    with pytest.raises(CapabilityError, match="is read-only"):
        await device.write("channel_display_state", channel=1)
