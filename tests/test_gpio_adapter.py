import pytest

from openmhs.adapters.raspberry_pi import GPIODevice
from openmhs.core.device import CapabilityError, DeviceState


class FakeLED:
    def __init__(self, pin: int):
        self.pin = pin
        self.lit = False
        self.closed = False

    def on(self):
        self.lit = True

    def off(self):
        self.lit = False

    @property
    def is_lit(self):
        return self.lit

    def close(self):
        self.closed = True


class FakeButton:
    def __init__(self, pin: int):
        self.pin = pin
        self.pressed = True
        self.closed = False

    @property
    def is_pressed(self):
        return self.pressed

    def close(self):
        self.closed = True


class FakePWMLED:
    def __init__(self, pin: int):
        self.pin = pin
        self.value = 0.0
        self.closed = False

    def close(self):
        self.closed = True


class FakeGPIOZero:
    def __init__(self):
        self.LED = FakeLED
        self.Button = FakeButton
        self.PWMLED = FakePWMLED


@pytest.mark.asyncio
async def test_gpio_simulation():
    dev = GPIODevice("gpio_sim", simulation=True)
    assert await dev.connect()
    assert dev.state is DeviceState.SIMULATED

    # Write digital pin
    w = await dev.write("digital_write", pin=17, value=1)
    assert w["value"] == 1
    assert w["simulated"] is True

    # Read back digital pin
    r = await dev.read("digital_read", pin=17)
    assert r["value"] == 1
    assert r["simulated"] is True

    # PWM
    pwm = await dev.write("pwm", pin=18, duty=50.0)
    assert pwm["duty"] == 50.0

    # Pin mode
    mode = await dev.write("pin_mode", pin=22, mode="in")
    assert mode["mode"] == "in"

    # Validation
    with pytest.raises(CapabilityError):
        await dev.write("digital_write", pin=30, value=1)
    with pytest.raises(CapabilityError):
        await dev.write("digital_write", pin=17, value=5)
    with pytest.raises(CapabilityError):
        await dev.write("pwm", pin=18, duty=150)
    with pytest.raises(CapabilityError):
        await dev.write("pin_mode", pin=22, mode="analog")


@pytest.mark.asyncio
async def test_gpio_real_with_fake_backend():
    fake_gpio = FakeGPIOZero()
    dev = GPIODevice("gpio_real", simulation=False, backend_factory=lambda: fake_gpio)
    assert await dev.connect()
    assert dev.state is DeviceState.ONLINE

    # Output mode
    await dev.write("pin_mode", pin=17, mode="out")
    await dev.write("digital_write", pin=17, value=1)
    r_out = await dev.read("digital_read", pin=17)
    assert r_out["value"] == 1

    # Input mode reads Button.is_pressed
    await dev.write("pin_mode", pin=18, mode="in")
    r_in = await dev.read("digital_read", pin=18)
    assert r_in["value"] == 1

    # Close resets and closes all pins
    await dev.close()
    assert dev.state is DeviceState.OFFLINE
