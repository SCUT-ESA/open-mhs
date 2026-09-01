"""Raspberry Pi adapters for MHS.

Supports:
- GPIO control
- Camera Module
- I2C/SPI devices
- PWM output
"""

from __future__ import annotations

import asyncio
from typing import Any

try:
    import gpiozero  # noqa: F401

    HAS_GPIOZERO = True
except ImportError:
    HAS_GPIOZERO = False

from openmhs.core.backend import BackendDevice, BackendPolicy
from openmhs.core.device import (
    CapabilityError,
    DeviceCapability,
    DeviceMetadata,
    DeviceState,
    SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class GPIODevice(BackendDevice):
    """Raspberry Pi GPIO controller."""

    def __init__(self, device_id: str, *, simulation: bool = False, backend_factory: Any = None):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="gpio_controller",
            manufacturer="Raspberry Pi",
            model="GPIO",
            capabilities=[
                DeviceCapability(
                    name="digital_write",
                    description="Set a GPIO pin high or low",
                    parameters={"pin": "GPIO pin number", "value": "0 or 1"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="digital_read",
                    description="Read a GPIO pin state",
                    parameters={"pin": "GPIO pin number"},
                    read_only=True,
                ),
                DeviceCapability(
                    name="pwm",
                    description="Set PWM duty cycle on a pin",
                    parameters={"pin": "GPIO pin number", "duty": "0-100"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="pin_mode",
                    description="Configure pin mode",
                    parameters={"pin": "GPIO pin number", "mode": "in/out/pwm"},
                    read_only=False,
                ),
            ],
            safety_limits=[
                SafetyLimit("pin", 0, 27, "", "Valid GPIO pin numbers for Pi"),
                SafetyLimit("duty", 0, 100, "%", "PWM duty cycle"),
                SafetyLimit("value", 0, 1, "", "Digital value"),
            ],
            tags=["raspberry_pi", "gpio", "embedded", "io"],
            natural_language_description=(
                "Raspberry Pi GPIO controller for digital I/O and PWM. "
                "Controls pins 0-27 with input, output, and PWM modes."
            ),
            driver_class="raspberry_pi_gpio",
        )
        super().__init__(
            metadata,
            BackendPolicy(
                simulation=simulation,
                backend_factory=backend_factory,
                backend_name="gpiozero",
                supported=True,
            ),
        )
        self._pins: dict[int, Any] = {}
        self._simulation_mode = self.simulated
        self._gpio: Any = None
        self._sim_values: dict[int, Any] = {}

    def _strict_validate(self, capability_name: str, params: dict[str, Any]) -> None:
        super()._strict_validate(capability_name, params)
        pin = params.get("pin")
        if not isinstance(pin, int) or isinstance(pin, bool) or not 0 <= pin <= 27:
            raise CapabilityError("GPIO pin must be an integer from 0 to 27")
        if capability_name == "digital_write" and params.get("value") not in {0, 1}:
            raise CapabilityError("digital value must be 0 or 1")
        if capability_name == "pwm" and (
            not isinstance(params.get("duty"), (int, float))
            or isinstance(params.get("duty"), bool)
            or not 0 <= params["duty"] <= 100
        ):
            raise CapabilityError("PWM duty must be between 0 and 100")
        if capability_name == "pin_mode" and params.get("mode") not in {"in", "out", "pwm"}:
            raise CapabilityError("GPIO mode must be in, out, or pwm")

    async def connect(self) -> bool:
        if self._simulation_mode:
            return await super().connect()
        if self.policy.backend_factory is None:
            self._set_state(DeviceState.ERROR)
            return False
        try:
            self._gpio = await asyncio.to_thread(self.policy.backend_factory)
            self._backend = self._gpio
            self._set_state(DeviceState.ONLINE)
            return True
        except Exception:
            self._set_state(DeviceState.ERROR)
            return False

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        pin = int(params.get("pin", 0))
        if capability == "digital_read":
            if self._simulation_mode:
                return {"pin": pin, "value": self._sim_values.get(pin, 0)}
            if pin in self._pins:
                obj = self._pins[pin]

                def read_value() -> Any:
                    if hasattr(obj, "is_pressed"):
                        return obj.is_pressed
                    if hasattr(obj, "is_lit"):
                        return obj.is_lit
                    return getattr(obj, "value", 0)

                value = await asyncio.to_thread(read_value)
                return {"pin": pin, "value": 1 if value else 0}
            return {"error": f"Pin {pin} not configured"}
        return {"error": f"Unknown capability: {capability}"}

    async def _replace_pin(self, pin: int, obj: Any) -> None:
        previous = self._pins.get(pin)
        if previous is not None and previous is not obj:
            close = getattr(previous, "close", None)
            if close is not None:
                await asyncio.to_thread(close)
        self._pins[pin] = obj

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        pin = int(params.get("pin", 0))
        if capability == "digital_write":
            value = int(params.get("value", 0))
            if self._simulation_mode:
                self._sim_values[pin] = value
                return {"pin": pin, "value": value, "simulated": True}
            if pin not in self._pins:
                await self._replace_pin(pin, await asyncio.to_thread(self._gpio.LED, pin))
            if value:
                await asyncio.to_thread(self._pins[pin].on)
            else:
                await asyncio.to_thread(self._pins[pin].off)
            return {"pin": pin, "value": value}

        elif capability == "pwm":
            duty = float(params.get("duty", 0))
            if self._simulation_mode:
                self._sim_values[pin] = duty
                return {"pin": pin, "duty": duty, "simulated": True}
            if pin not in self._pins:
                await self._replace_pin(pin, await asyncio.to_thread(self._gpio.PWMLED, pin))
            await asyncio.to_thread(setattr, self._pins[pin], "value", duty / 100.0)
            return {"pin": pin, "duty": duty}

        elif capability == "pin_mode":
            mode = params.get("mode", "in")
            if self._simulation_mode:
                return {"pin": pin, "mode": mode, "simulated": True}
            if mode == "out":
                await self._replace_pin(pin, await asyncio.to_thread(self._gpio.LED, pin))
            elif mode == "in":
                await self._replace_pin(pin, await asyncio.to_thread(self._gpio.Button, pin))
            elif mode == "pwm":
                await self._replace_pin(pin, await asyncio.to_thread(self._gpio.PWMLED, pin))
            else:
                raise ValueError("mode must be in, out, or pwm")
            return {"pin": pin, "mode": mode}

        return {"error": f"Unknown capability: {capability}"}

    async def close(self) -> None:
        pins, self._pins = self._pins, {}
        for pin in pins.values():
            try:
                if hasattr(pin, "off"):
                    await asyncio.to_thread(pin.off)
                if hasattr(pin, "value"):
                    await asyncio.to_thread(setattr, pin, "value", 0)
                close = getattr(pin, "close", None)
                if close is not None:
                    await asyncio.to_thread(close)
            except Exception:
                pass
        self._gpio = None
        self._backend = None
        await super().close()


@register_driver
class GPIODriver(Driver):
    """Driver for Raspberry Pi GPIO."""

    def __init__(
        self, config: DriverConfig, *, simulation: bool | None = None, backend_factory: Any = None
    ):
        super().__init__(config, simulation=simulation, backend_factory=backend_factory)

    DRIVER_NAME = "raspberry_pi_gpio"
    SUPPORTED_DEVICES = ["gpio_controller", "raspberry_pi", "gpio"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "pi_gpio_001")
        candidate = GPIODevice(
            device_id, simulation=self.simulation, backend_factory=self.backend_factory
        )
        return await self._connect_candidate(candidate)


# === Raspberry Pi Camera ===


class PiCameraDevice(BackendDevice):
    """Raspberry Pi Camera Module."""

    def __init__(self, device_id: str, *, simulation: bool = False, backend_factory: Any = None):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="camera",
            manufacturer="Raspberry Pi",
            model="Camera Module",
            capabilities=[
                DeviceCapability(
                    name="frame",
                    description="Capture an image",
                    parameters={"resolution": "WxH", "format": "jpg"},
                    read_only=True,
                ),
                DeviceCapability(
                    name="video",
                    description="Record video",
                    parameters={"duration": "seconds", "resolution": "WxH"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="settings",
                    description="Camera settings",
                    parameters={"brightness": "0-100", "contrast": "0-100", "iso": "100-800"},
                    read_only=False,
                ),
            ],
            tags=["raspberry_pi", "camera", "vision"],
            natural_language_description="Raspberry Pi Camera Module for image and video capture.",
            driver_class="raspberry_pi_camera",
        )
        super().__init__(
            metadata,
            BackendPolicy(
                simulation=simulation,
                backend_factory=backend_factory,
                backend_name="picamera",
                supported=False,
            ),
        )

    async def connect(self) -> bool:
        return await super().connect()
        return True

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "frame":
            return {"captured": True, "format": params.get("format", "jpg"), "simulated": True}
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "video":
            return {"recording": True, "duration": params.get("duration", 10)}
        elif capability == "settings":
            return {"configured": True, "settings": params}
        return {"error": f"Unknown capability: {capability}"}


@register_driver
class PiCameraDriver(Driver):
    """Driver for Raspberry Pi Camera."""

    def __init__(
        self, config: DriverConfig, *, simulation: bool | None = None, backend_factory: Any = None
    ):
        super().__init__(config, simulation=simulation, backend_factory=backend_factory)

    DRIVER_NAME = "raspberry_pi_camera"
    SUPPORTED_DEVICES = ["pi_camera", "raspberry_pi_camera"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "pi_cam_001")
        candidate = PiCameraDevice(
            device_id, simulation=self.simulation, backend_factory=self.backend_factory
        )
        return await self._connect_candidate(candidate)
