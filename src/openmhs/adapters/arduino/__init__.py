"""Arduino adapter for MHS.

Communicates with Arduino boards via serial (USB).
Supports digital/analog I/O, PWM, and custom sketches.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

from openmhs.core.device import (
    BaseDevice, DeviceCapability, DeviceMetadata, DeviceState, SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class ArduinoDevice(BaseDevice):
    """Arduino board controller."""

    def __init__(self, device_id: str, port: str = "/dev/ttyUSB0", baudrate: int = 9600):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="arduino",
            manufacturer="Arduino",
            model="Uno/Nano/Mega",
            capabilities=[
                DeviceCapability(
                    name="digital_write",
                    description="Set digital pin HIGH or LOW",
                    parameters={"pin": "0-13", "value": "0 or 1"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="digital_read",
                    description="Read digital pin state",
                    parameters={"pin": "0-13"},
                    read_only=True,
                ),
                DeviceCapability(
                    name="analog_read",
                    description="Read analog pin value",
                    parameters={"pin": "A0-A5"},
                    read_only=True,
                ),
                DeviceCapability(
                    name="analog_write",
                    description="PWM output on pin",
                    parameters={"pin": "3,5,6,9,10,11", "value": "0-255"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="custom",
                    description="Send custom command to Arduino",
                    parameters={"command": "string"},
                    read_only=False,
                ),
            ],
            safety_limits=[
                SafetyLimit("pin", 0, 13, "", "Digital pin range"),
                SafetyLimit("value", 0, 255, "", "PWM/Analog value range"),
            ],
            tags=["arduino", "microcontroller", "serial", "embedded"],
            natural_language_description=(
                "Arduino microcontroller board accessed via serial USB. "
                "Supports digital I/O, analog input, and PWM output."
            ),
            driver_class="arduino",
            connection_info={"port": port, "baudrate": baudrate},
        )
        super().__init__(metadata)
        self._port = port
        self._baudrate = baudrate
        self._serial = None
        self._simulation_mode = not HAS_SERIAL

    async def connect(self) -> bool:
        if self._simulation_mode:
            self._set_state(DeviceState.ONLINE)
            return True
        try:
            self._serial = serial.Serial(self._port, self._baudrate, timeout=1)
            await asyncio.sleep(2)  # Arduino reset delay
            self._set_state(DeviceState.ONLINE)
            return True
        except Exception as e:
            self._set_state(DeviceState.ERROR)
            return False

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        pin = params.get("pin", 0)
        if capability == "digital_read":
            if self._simulation_mode:
                return {"pin": pin, "value": 0, "simulated": True}
            self._serial.write(f"DR:{pin}\n".encode())
            response = self._serial.readline().decode().strip()
            return {"pin": pin, "value": int(response) if response.isdigit() else None}
        
        elif capability == "analog_read":
            if self._simulation_mode:
                return {"pin": pin, "value": 512, "simulated": True}
            pin_num = int(pin.replace("A", "")) if isinstance(pin, str) else pin
            self._serial.write(f"AR:{pin_num}\n".encode())
            response = self._serial.readline().decode().strip()
            return {"pin": pin, "value": int(response) if response.isdigit() else None}
        
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        pin = params.get("pin", 0)
        value = params.get("value", 0)
        if capability == "digital_write":
            if self._simulation_mode:
                return {"pin": pin, "value": value, "simulated": True}
            self._serial.write(f"DW:{pin}:{value}\n".encode())
            return {"pin": pin, "value": value}
        
        elif capability == "analog_write":
            if self._simulation_mode:
                return {"pin": pin, "value": value, "simulated": True}
            self._serial.write(f"AW:{pin}:{value}\n".encode())
            return {"pin": pin, "value": value}
        
        elif capability == "custom":
            cmd = params.get("command", "")
            if self._simulation_mode:
                return {"command": cmd, "response": "OK", "simulated": True}
            self._serial.write(f"{cmd}\n".encode())
            response = self._serial.readline().decode().strip()
            return {"command": cmd, "response": response}
        
        return {"error": f"Unknown capability: {capability}"}

    async def close(self) -> None:
        if self._serial:
            self._serial.close()
            self._serial = None
        await super().close()


@register_driver
class ArduinoDriver(Driver):
    """Driver for Arduino boards."""

    DRIVER_NAME = "arduino"
    SUPPORTED_DEVICES = ["arduino", "microcontroller"]

    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._port = config.connection_params.get("port", "/dev/ttyUSB0")
        self._baudrate = config.connection_params.get("baudrate", 9600)

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "arduino_001")
        self._device = ArduinoDevice(device_id, self._port, self._baudrate)
        result = await self._device.connect()
        self._connected = result
        return result
