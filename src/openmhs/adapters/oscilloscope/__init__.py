"""Oscilloscope adapters for MHS.

Supports UNI-T UPO6102N devices.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

try:  # Optional dependency; importing the package must remain lightweight.
    import pyvisa
except ImportError:  # pragma: no cover - exercised without the extra installed
    pyvisa = None  # type: ignore[assignment]

from openmhs.adapters.visa import VisaSession
from openmhs.core.device import (
    BaseDevice,
    CapabilityError,
    DeviceCapability,
    DeviceMetadata,
    DeviceState,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


def _channel(params: dict[str, Any]) -> int:
    """Validate a physical channel without truncating unsafe values."""
    value = params.get("channel", 1)
    if isinstance(value, bool):
        raise CapabilityError("channel must be an integer from 1 to 2")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CapabilityError("channel must be an integer from 1 to 2") from exc
    if isinstance(value, float) and value != parsed:
        raise CapabilityError("channel must be an integer from 1 to 2")
    if isinstance(value, str) and str(parsed) != value.strip():
        raise CapabilityError("channel must be an integer from 1 to 2")
    if parsed not in (1, 2):
        raise CapabilityError("channel must be an integer from 1 to 2")
    return parsed


class OscilloscopeDevice(BaseDevice):
    """An oscilloscope backed by a serialized VISA session."""

    def __init__(self, device_id: str, visa_resource: str, resource_manager_factory: Callable[[], Any] | None = None):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="oscilloscope",
            manufacturer="UNI-T",
            model="UPO6102N",
            capabilities=[
                DeviceCapability("identify", "Query oscilloscope identity", read_only=True),
                DeviceCapability("run", "Start acquisition"),
                DeviceCapability("stop", "Stop acquisition"),
                DeviceCapability(
                    "channel_display_state",
                    "Read display state for the specified channel",
                    {
                        "channel": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 2,
                            "description": "Channel number",
                        }
                    },
                    True,
                ),
                DeviceCapability(
                    "channel_display",
                    "Enable or disable display for the specified channel",
                    {
                        "channel": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 2,
                            "description": "Channel number",
                        },
                        "enabled": {"type": "boolean", "description": "Display state"},
                    },
                ),
                DeviceCapability(
                    "measure_vpp", "Measure peak-to-peak voltage",
                    {"channel": {"type": "integer", "minimum": 1, "maximum": 2,
                                 "description": "Channel number"}}, True,
                ),
                DeviceCapability(
                    "measure_frequency", "Measure frequency",
                    {"channel": {"type": "integer", "minimum": 1, "maximum": 2,
                                 "description": "Channel number"}}, True,
                ),
            ],
            tags=["oscilloscope", "visa"],
            natural_language_description="An oscilloscope device used for measuring electrical signals.",
            driver_class="oscilloscope",
            connection_info={"visa_resource": visa_resource},
        )
        super().__init__(metadata)
        factory = resource_manager_factory or (pyvisa.ResourceManager if pyvisa is not None else None)
        self._session = VisaSession(visa_resource, factory)

    async def connect(self) -> bool:
        """Open the instrument and verify it using only an IDN query."""
        if self._session.is_open:
            return self.state is DeviceState.ONLINE
        try:
            await self._session.open()
            identity = await self._session.query("*IDN?")
            if not identity or not identity.strip():
                raise RuntimeError("Oscilloscope returned an empty identity")
            self._set_state(DeviceState.ONLINE)
            return True
        except asyncio.CancelledError:
            await self._session.close()
            self._set_state(DeviceState.ERROR)
            raise
        except Exception:  # noqa: BLE001
            await self._session.close()
            self._set_state(DeviceState.ERROR)
            return False

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "identify":
            return {"value": await self._session.query("*IDN?")}
        if capability == "channel_display_state":
            channel = _channel(params)
            response = (await self._session.query(f":CHANnel{channel}:DISPlay?")).strip().upper()
            if response in {"ON", "TRUE", "1"}:
                state = True
            elif response in {"OFF", "FALSE", "0"}:
                state = False
            else:
                raise CapabilityError(f"Unrecognized display state: {response}")
            return {"channel": channel, "enabled": state}
        if capability == "measure_vpp":
            channel = _channel(params)
            response = await self._session.query(f":MEASure:VPP? CHANnel{channel}")
            return {"channel": channel, "value": float(response), "unit": "V"}
        if capability == "measure_frequency":
            channel = _channel(params)
            response = await self._session.query(f":MEASure:FREQuency? CHANnel{channel}")
            return {"channel": channel, "value": float(response), "unit": "Hz"}
        return {"value": None, "error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "run":
            await self._session.write(":RUN")
            return {"running": True}
        if capability == "stop":
            await self._session.write(":STOP")
            return {"running": False}
        if capability == "channel_display":
            channel = _channel(params)
            enabled = params.get("enabled")
            if not isinstance(enabled, bool):
                raise CapabilityError("enabled must be a boolean")
            await self._session.write(f":CHANnel{channel}:DISPlay {'ON' if enabled else 'OFF'}")
            return {"channel": channel, "enabled": enabled}
        return {"error": f"Unknown capability: {capability}"}

    async def close(self) -> None:
        """Close VISA handles idempotently and mark the device offline."""
        try:
            await self._session.close()
        finally:
            await super().close()


@register_driver
class OscilloscopeDriver(Driver):
    """Driver for oscilloscope devices."""

    DRIVER_NAME = "oscilloscope"
    SUPPORTED_DEVICES: list[str] = ["oscilloscope"]  # noqa: RUF012

    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._visa_resource = cast(str, config.connection_params.get("visa_resource"))
        self._lifecycle_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Connect without replacing an already connected device."""
        async with self._lifecycle_lock:
            if self._device is not None and self._connected:
                return True
            device_id = self.config.connection_params.get("device_id", "oscilloscope_001")
            if self._device is None:
                self._device = OscilloscopeDevice(
                    device_id, self._visa_resource,
                    self.config.connection_params.get("resource_manager_factory"),
                )
            self._connected = await cast(OscilloscopeDevice, self._device).connect()
            return self._connected

    async def disconnect(self) -> None:
        """Disconnect idempotently while serializing with connect."""
        async with self._lifecycle_lock:
            device = self._device
            self._connected = False
            if device is None:
                return
            try:
                await device.close()
            finally:
                self._device = None


__all__ = ["OscilloscopeDevice", "OscilloscopeDriver"]
