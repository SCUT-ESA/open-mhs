"""Fake-instrument tests for the UTG2062X adapter."""

import asyncio
import threading

import pytest

from openmhs.adapters.waveform_generator import WaveformGeneratorDevice
from openmhs.core.device import CapabilityError, DeviceState


class FakeInstrument:
    def __init__(self, identity="UNI-T,UTG2062X,SN,1.0"):
        self.identity = identity
        self.query_calls = []
        self.write_calls = []
        self.close_count = 0
        self.responses = {}
        self.timeout = None

    def query(self, command):
        self.query_calls.append(command)
        if command == "*IDN?":
            return self.identity
        return self.responses.get(command, "OFF")

    def write(self, command):
        self.write_calls.append(command)

    def close(self):
        self.close_count += 1


class FakeResourceManager:
    def __init__(self, instrument):
        self.instrument = instrument
        self.open_calls = 0
        self.close_count = 0

    def open_resource(self, resource):
        self.open_calls += 1
        return self.instrument

    def close(self):
        self.close_count += 1


@pytest.fixture
def connected():
    instrument = FakeInstrument()
    manager = FakeResourceManager(instrument)
    device = WaveformGeneratorDevice("wavegen-test", "USB::GEN::INSTR", lambda: manager)
    return device, instrument, manager


@pytest.mark.asyncio
async def test_connect_is_idn_only_and_close_is_idempotent(connected):
    device, instrument, manager = connected
    assert await device.connect()
    assert instrument.query_calls == ["*IDN?"]
    assert instrument.write_calls == []
    await device.close()
    await device.close()
    assert instrument.close_count == 1
    assert manager.close_count == 1
    assert device.state is DeviceState.OFFLINE


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled, command", [(True, "ON"), (False, "OFF")])
async def test_output_channel_two(connected, enabled, command):
    device, instrument, _ = connected
    await device.connect()
    result = await device.write("output", channel=2, enabled=enabled)
    assert instrument.write_calls == [f":CHANnel2:OUTPut {command}"]
    assert result["enabled"] is enabled


@pytest.mark.asyncio
async def test_reads_and_waveform_amplitude(connected):
    device, instrument, _ = connected
    await device.connect()
    instrument.responses[":CHANnel2:OUTPut?"] = "1"
    assert (await device.read("output_state", channel=2))["enabled"] is True
    await device.write("waveform", channel=1, waveform="SqUaRe")
    await device.write("amplitude", channel=1, amplitude=2.5)
    assert instrument.write_calls == [":CHANnel1:BASE:WAVe SQUare", ":CHANnel1:BASE:AMPLitude 2.5"]


@pytest.mark.asyncio
async def test_invalid_parameters_never_write(connected):
    device, instrument, _ = connected
    await device.connect()
    for channel in (0, 3, 1.5, True, "bad"):
        with pytest.raises(CapabilityError):
            await device.write("output", channel=channel, enabled=True)
    for amplitude in (-1, 0, float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CapabilityError):
            await device.write("amplitude", channel=1, amplitude=amplitude)
    with pytest.raises(CapabilityError):
        await device.write("waveform", channel=1, waveform="triangle")
    assert instrument.write_calls == []


@pytest.mark.asyncio
async def test_identity_mismatch_closes_without_writes():
    instrument = FakeInstrument("UNI-T,UPO6102N,SN,1.0")
    manager = FakeResourceManager(instrument)
    device = WaveformGeneratorDevice("wavegen-test", "USB::GEN::INSTR", lambda: manager)
    assert not await device.connect()
    assert instrument.query_calls == ["*IDN?"]
    assert instrument.write_calls == []
    assert instrument.close_count == 1
    assert manager.close_count == 1


@pytest.mark.asyncio
async def test_connect_cancellation_wins_when_cleanup_fails():
    started = threading.Event()
    release = threading.Event()

    class BlockingInstrument(FakeInstrument):
        def query(self, command):
            started.set()
            release.wait(timeout=2)
            return super().query(command)

        def close(self):
            self.close_count += 1
            raise RuntimeError("close failed")

    instrument = BlockingInstrument()
    manager = FakeResourceManager(instrument)
    device = WaveformGeneratorDevice("wavegen-test", "USB::GEN::INSTR", lambda: manager)
    connecting = asyncio.create_task(device.connect())
    await asyncio.to_thread(started.wait)
    connecting.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await connecting
    assert device.state is DeviceState.ERROR


@pytest.mark.asyncio
async def test_invalid_session_reopens_and_retries():
    first = FakeInstrument()
    second = FakeInstrument()
    managers = iter([FakeResourceManager(first), FakeResourceManager(second)])
    device = WaveformGeneratorDevice("wavegen-test", "USB::GEN::INSTR", lambda: next(managers))
    await device.connect()
    first.responses[":CHANnel1:OUTPut?"] = "OFF"
    original = first.query

    def invalid(command):
        if command != "*IDN?":
            raise RuntimeError("Invalid session")
        return original(command)

    first.query = invalid
    assert (await device.read("output_state", channel=1))["enabled"] is False
    assert second.query_calls == [":CHANnel1:OUTPut?"]
    assert first.close_count == 1
    assert second.close_count == 0
