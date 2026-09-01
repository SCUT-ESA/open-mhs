import pytest

from openmhs.adapters.arduino import ArduinoDevice
from openmhs.core.device import CapabilityError, DeviceState


class FakeSerial:
    def __init__(self, handshake_ok: bool = True):
        self.handshake_ok = handshake_ok
        self.closed = False
        self.written = []

    def handshake(self) -> bool:
        return self.handshake_ok

    def write(self, data: bytes):
        self.written.append(data)

    def readline(self) -> bytes:
        return b"1\n"

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_arduino_simulation():
    dev = ArduinoDevice("ard_sim", simulation=True)
    assert await dev.connect()
    assert dev.state is DeviceState.SIMULATED

    # Digital write and read
    await dev.write("digital_write", pin=13, value=1)
    r = await dev.read("digital_read", pin=13)
    assert r["value"] == 1
    assert r["simulated"] is True

    # Analog write and read
    await dev.write("analog_write", pin=3, value=128)
    ar = await dev.read("analog_read", pin="A0")
    assert ar["value"] == 512

    # Custom command
    cust = await dev.write("custom", command="STATUS")
    assert cust["response"] == "OK"

    # Validation
    with pytest.raises(CapabilityError):
        await dev.write("digital_write", pin=20, value=1)
    with pytest.raises(CapabilityError):
        await dev.read("analog_read", pin="A9")
    with pytest.raises(CapabilityError):
        await dev.write("custom", command="")


@pytest.mark.asyncio
async def test_arduino_real_without_verified_protocol_fails():
    # Without protocol_verified, real mode must fail closed
    dev = ArduinoDevice("ard_real", simulation=False, protocol_verified=False)
    assert not await dev.connect()
    assert dev.state is DeviceState.ERROR


@pytest.mark.asyncio
async def test_arduino_real_with_verified_handshake():
    fake_ser = FakeSerial(handshake_ok=True)
    dev = ArduinoDevice(
        "ard_real",
        simulation=False,
        protocol_verified=True,
        backend_factory=lambda: fake_ser,
    )
    assert await dev.connect()
    assert dev.state is DeviceState.ONLINE

    await dev.write("digital_write", pin=13, value=1)
    assert b"DW:13:1\n" in fake_ser.written

    await dev.close()
    assert fake_ser.closed is True
    assert dev.state is DeviceState.OFFLINE
