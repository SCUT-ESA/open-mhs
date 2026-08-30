"""Frequency capability tests without physical VISA hardware."""

import pytest

from openmhs.adapters.oscilloscope import OscilloscopeDevice
from openmhs.core.device import CapabilityError


class Instrument:
    def __init__(self):
        self.queries = []
        self.writes = []
        self.closed = 0
        self.timeout = None

    def query(self, command):
        self.queries.append(command)
        return "UNI-T,UPO6102N,SN,1.0" if command == "*IDN?" else "123.5"

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
async def test_measure_frequency_uses_channel_scpi_and_hz(connected):
    device, instrument = connected
    assert await device.connect()
    result = await device.read("measure_frequency", channel=2)
    default_result = await device.read("measure_frequency")
    assert instrument.queries == [
        "*IDN?",
        ":MEASure:FREQuency? CHANnel2",
        ":MEASure:FREQuency? CHANnel1",
    ]
    assert result == {"channel": 2, "value": 123.5, "unit": "Hz"}
    assert default_result == {"channel": 1, "value": 123.5, "unit": "Hz"}


@pytest.mark.asyncio
async def test_frequency_bad_channels_are_rejected_before_query(connected):
    device, instrument = connected
    await device.connect()
    for channel in (0, 3, 1.5, True, "bad"):
        with pytest.raises(CapabilityError):
            await device.read("measure_frequency", channel=channel)
    assert instrument.queries == ["*IDN?"]


@pytest.mark.asyncio
async def test_frequency_is_read_only(connected):
    device, _ = connected
    await device.connect()
    with pytest.raises(CapabilityError):
        await device.write("measure_frequency", channel=1)
