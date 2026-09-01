"""Arduino adapter for MHS.

Communicates with Arduino boards via serial (USB).
Supports digital/analog I/O, PWM, and custom sketches.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

try:
    import serial  # noqa: F401

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

from openmhs.core.backend import BackendDevice, BackendPolicy
from openmhs.core.device import (
    CapabilityError,
    DeviceCapability,
    DeviceMetadata,
    DeviceState,
    SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class ArduinoDevice(BackendDevice):
    """Arduino board controller."""

    def __init__(
        self,
        device_id: str,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 9600,
        *,
        simulation: bool = False,
        backend_factory: Any = None,
        protocol_verified: bool = False,
    ):
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
        super().__init__(
            metadata,
            BackendPolicy(
                simulation=simulation,
                backend_factory=backend_factory,
                backend_name="arduino",
                supported=protocol_verified,
            ),
        )
        self._port = port
        self._baudrate = baudrate
        self._serial: Any = None
        self._simulation_mode = self.simulated
        self._protocol_verified = protocol_verified
        self._sim_values: dict[Any, Any] = {}

    def _strict_validate(self, capability_name: str, params: dict[str, Any]) -> None:
        super()._strict_validate(capability_name, params)
        pin = params.get("pin")
        if capability_name in {"digital_read", "digital_write"} and (
            not isinstance(pin, int) or isinstance(pin, bool) or not 0 <= pin <= 13
        ):
            raise CapabilityError("Arduino digital pin must be an integer from 0 to 13")
        if capability_name == "analog_read" and pin not in {f"A{i}" for i in range(6)}:
            raise CapabilityError("Arduino analog pin must be A0 through A5")
        if capability_name == "analog_write" and pin not in {3, 5, 6, 9, 10, 11}:
            raise CapabilityError("Arduino PWM pin is unsupported")
        if capability_name in {"digital_write", "analog_write"}:
            value = params.get("value")
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= (1 if capability_name == "digital_write" else 255)
            ):
                raise CapabilityError("Arduino output value is out of range")
        if capability_name == "custom" and (
            not isinstance(params.get("command"), str) or not params["command"].strip()
        ):
            raise CapabilityError("Arduino custom command must be non-empty")

    async def connect(self) -> bool:
        if self._simulation_mode:
            return await super().connect()
        if not self._protocol_verified or self.policy.backend_factory is None:
            self._set_state(DeviceState.ERROR)
            return False
        try:
            self._serial = await asyncio.to_thread(self.policy.backend_factory)
            handshake = getattr(self._serial, "handshake", None)
            if handshake is None:
                await self.close()
                self._set_state(DeviceState.ERROR)
                return False
            verified = await asyncio.to_thread(handshake)
            if inspect.isawaitable(verified):
                verified = await verified
            if not verified:
                await self.close()
                self._set_state(DeviceState.ERROR)
                return False
            self._backend = self._serial
            self._set_state(DeviceState.ONLINE)
            return True
        except asyncio.CancelledError:
            await asyncio.shield(self.close())
            raise
        except Exception:
            await self.close()
            self._set_state(DeviceState.ERROR)
            return False

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        pin = params.get("pin", 0)
        if capability == "digital_read":
            if self._simulation_mode:
                return {
                    "pin": pin,
                    "value": self._sim_values.get(("digital", pin), 0),
                    "simulated": True,
                }
            self._serial.write(f"DR:{pin}\n".encode())
            response = self._serial.readline().decode().strip()
            return {"pin": pin, "value": int(response) if response.isdigit() else None}

        elif capability == "analog_read":
            if self._simulation_mode:
                return {
                    "pin": pin,
                    "value": self._sim_values.get(("analog", pin), 512),
                    "simulated": True,
                }
            pin_num = int(pin.replace("A", "")) if isinstance(pin, str) else pin
            self._serial.write(f"AR:{pin_num}\n".encode())
            response = self._serial.readline().decode().strip()
            return {"pin": pin, "value": int(response) if response.isdigit() else None}

        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        pin = params.get("pin", 0)
        value = params.get("value", 0)
        if capability == "digital_write":
            if self._simulation_mode:
                self._sim_values[("digital", pin)] = value
                return {"pin": pin, "value": value, "simulated": True}
            self._serial.write(f"DW:{pin}:{value}\n".encode())
            return {"pin": pin, "value": value}

        elif capability == "analog_write":
            if self._simulation_mode:
                self._sim_values[("analog", pin)] = value
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
        serial_backend, self._serial = self._serial, None
        self._backend = None
        if serial_backend is not None:
            await asyncio.to_thread(serial_backend.close)
        await super().close()


@register_driver
class ArduinoDriver(Driver):
    """Driver for Arduino boards."""

    def __init__(
        self, config: DriverConfig, *, simulation: bool | None = None, backend_factory: Any = None
    ):
        super().__init__(config, simulation=simulation, backend_factory=backend_factory)
        self._port = config.connection_params.get("port", "/dev/ttyUSB0")
        self._baudrate = config.connection_params.get("baudrate", 9600)

    DRIVER_NAME = "arduino"
    SUPPORTED_DEVICES = ["arduino", "microcontroller"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "arduino_001")
        verified = self.config.connection_params.get("protocol_verified", False)
        candidate = ArduinoDevice(
            device_id,
            self._port,
            self._baudrate,
            simulation=self.simulation,
            backend_factory=self.backend_factory,
            protocol_verified=verified,
        )
        return await self._connect_candidate(candidate)
