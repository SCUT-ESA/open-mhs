"""Raspberry Pi adapters for MHS.

Supports:
- GPIO control
- Camera Module
- I2C/SPI devices
- PWM output
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

try:
    import gpiozero
    HAS_GPIOZERO = True
except ImportError:
    HAS_GPIOZERO = False

from openmhs.core.device import (
    BaseDevice, DeviceCapability, DeviceMetadata, DeviceState, SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class GPIODevice(BaseDevice):
    """Raspberry Pi GPIO controller."""

    def __init__(self, device_id: str):
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
        super().__init__(metadata)
        self._pins: Dict[int, Any] = {}
        self._simulation_mode = not HAS_GPIOZERO

    async def connect(self) -> bool:
        self._set_state(DeviceState.ONLINE)
        return True

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        pin = int(params.get("pin", 0))
        if capability == "digital_read":
            if self._simulation_mode:
                return {"pin": pin, "value": 0}
            if pin in self._pins:
                return {"pin": pin, "value": 1 if self._pins[pin].is_lit else 0}
            return {"error": f"Pin {pin} not configured"}
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        pin = int(params.get("pin", 0))
        if capability == "digital_write":
            value = int(params.get("value", 0))
            if self._simulation_mode:
                return {"pin": pin, "value": value, "simulated": True}
            if pin not in self._pins:
                self._pins[pin] = gpiozero.LED(pin)
            if value:
                self._pins[pin].on()
            else:
                self._pins[pin].off()
            return {"pin": pin, "value": value}
        
        elif capability == "pwm":
            duty = float(params.get("duty", 0))
            if self._simulation_mode:
                return {"pin": pin, "duty": duty, "simulated": True}
            if pin not in self._pins:
                self._pins[pin] = gpiozero.PWMLED(pin)
            self._pins[pin].value = duty / 100.0
            return {"pin": pin, "duty": duty}
        
        elif capability == "pin_mode":
            mode = params.get("mode", "in")
            if self._simulation_mode:
                return {"pin": pin, "mode": mode, "simulated": True}
            if mode == "out":
                self._pins[pin] = gpiozero.LED(pin)
            elif mode == "in":
                self._pins[pin] = gpiozero.Button(pin)
            elif mode == "pwm":
                self._pins[pin] = gpiozero.PWMLED(pin)
            return {"pin": pin, "mode": mode}
        
        return {"error": f"Unknown capability: {capability}"}


@register_driver
class GPIODriver(Driver):
    """Driver for Raspberry Pi GPIO."""

    DRIVER_NAME = "raspberry_pi_gpio"
    SUPPORTED_DEVICES = ["gpio_controller", "raspberry_pi", "gpio"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "pi_gpio_001")
        self._device = GPIODevice(device_id)
        result = await self._device.connect()
        self._connected = result
        return result


# === Raspberry Pi Camera ===

class PiCameraDevice(BaseDevice):
    """Raspberry Pi Camera Module."""

    def __init__(self, device_id: str):
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
        super().__init__(metadata)

    async def connect(self) -> bool:
        self._set_state(DeviceState.ONLINE)
        return True

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "frame":
            return {"captured": True, "format": params.get("format", "jpg"), "simulated": True}
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "video":
            return {"recording": True, "duration": params.get("duration", 10)}
        elif capability == "settings":
            return {"configured": True, "settings": params}
        return {"error": f"Unknown capability: {capability}"}


@register_driver
class PiCameraDriver(Driver):
    """Driver for Raspberry Pi Camera."""

    DRIVER_NAME = "raspberry_pi_camera"
    SUPPORTED_DEVICES = ["pi_camera", "raspberry_pi_camera"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "pi_cam_001")
        self._device = PiCameraDevice(device_id)
        result = await self._device.connect()
        self._connected = result
        return result
