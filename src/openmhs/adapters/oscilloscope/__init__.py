"""Oscilloscope adapters for MHS.

Supports UNI-T UPO6102N devices.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

try:  # Optional dependency; importing the package must remain lightweight.
    import pyvisa
except ImportError:  # pragma: no cover - exercised without the extra installed
    pyvisa = None  # type: ignore[assignment]

from openmhs.core.device import (
    BaseDevice,
    DeviceCapability,
    DeviceMetadata,
    DeviceState,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class OscilloscopeDevice(BaseDevice):
    """An oscilloscope backed by a VISA instrument."""

    def __init__(self, device_id: str, visa_resource: str):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="oscilloscope",
            manufacturer="UNI-T",
            model="UPO6102N",
            capabilities=[
                DeviceCapability(
                    name="identify",
                    description="Query oscilloscope identity",
                    read_only=True,
                ),
                DeviceCapability(
                    name="run",
                    description="Start acquisition",
                    read_only=False,
                ),
                DeviceCapability(
                    name="stop",
                    description="Stop acquisition",
                    read_only=False,
                ),
                DeviceCapability(
                    name="measure_vpp",
                    description="Measure peak-to-peak voltage",
                    parameters={
                        "channel": {
                            "type": "integer",
                            "description": "Channel number",
                        }
                    },
                    read_only=True,
                ),
            ],
            tags=["oscilloscope"],
            natural_language_description=(
                "An oscilloscope device used for measuring electrical signals."
            ),
            driver_class="oscilloscope",
            connection_info={"visa_resource": visa_resource},
        )
        super().__init__(metadata)
        self._visa_resource = visa_resource
        self._resource_manager: Any = None
        self._instrument: Any = None
        self._visa_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Open the instrument once and verify it with an IDN query."""
        async with self._visa_lock:
            if self._instrument is not None:
                return self.state is DeviceState.ONLINE
            if pyvisa is None:
                self._set_state(DeviceState.ERROR)
                return False

            resource_manager = None
            instrument = None
            try:
                resource_manager, instrument = await asyncio.to_thread(
                    self._open_resources
                )
                identity = await asyncio.to_thread(instrument.query, "*IDN?")
                if not identity or not identity.strip():
                    raise RuntimeError("Oscilloscope returned an empty identity")
                self._resource_manager = resource_manager
                self._instrument = instrument
                self._set_state(DeviceState.ONLINE)
                return True
            except asyncio.CancelledError:
                await asyncio.to_thread(
                    self._close_resources, instrument, resource_manager
                )
                self._set_state(DeviceState.ERROR)
                raise
            except Exception:  # noqa: BLE001
                await asyncio.to_thread(
                    self._close_resources, instrument, resource_manager
                )
                self._set_state(DeviceState.ERROR)
                return False

    def _open_resources(self) -> tuple[Any, Any]:
        if pyvisa is None:
            raise RuntimeError("PyVISA is not installed")
        resource_manager = pyvisa.ResourceManager()
        try:
            instrument = resource_manager.open_resource(self._visa_resource)
            instrument.timeout = 2000
        except Exception:
            resource_manager.close()
            raise
        return resource_manager, instrument

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "identify":
            return {"value": await self._query("*IDN?")}
        if capability == "measure_vpp":
            channel = int(params.get("channel", 1))
            response = await self._query(f":MEASure:VPP? CHANnel{channel}")
            return {"channel": channel, "value": float(response), "unit": "V"}
        return {"value": None, "error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "run":
            await self._write(":RUN")
            return {"running": True}
        if capability == "stop":
            await self._write(":STOP")
            return {"running": False}
        return {"error": f"Unknown capability: {capability}"}

    async def _reopen_resources_locked(self) -> None:
        instrument = self._instrument
        resource_manager = self._resource_manager
        self._instrument = None
        self._resource_manager = None
        if instrument is not None:
            try:
                await asyncio.to_thread(instrument.close)
            except Exception:  # noqa: BLE001
                pass
        if resource_manager is not None:
            try:
                await asyncio.to_thread(resource_manager.close)
            except Exception:  # noqa: BLE001
                pass
        resource_manager, instrument = await asyncio.to_thread(
            self._open_resources
        )
        self._resource_manager = resource_manager
        self._instrument = instrument

    async def _query(self, command: str) -> str:
        async with self._visa_lock:
            if self._instrument is None:
                raise RuntimeError("Not connected")
            try:
                return await asyncio.to_thread(self._instrument.query, command)
            except Exception as exc:
                if "Invalid session" in str(exc) or "VI_ERROR_INV_OBJECT" in str(exc):
                    await self._reopen_resources_locked()
                    return await asyncio.to_thread(self._instrument.query, command)
                raise

    async def _write(self, command: str) -> None:
        async with self._visa_lock:
            if self._instrument is None:
                raise RuntimeError("Not connected")
            try:
                await asyncio.to_thread(self._instrument.write, command)
            except Exception as exc:
                if "Invalid session" in str(exc) or "VI_ERROR_INV_OBJECT" in str(exc):
                    await self._reopen_resources_locked()
                    await asyncio.to_thread(self._instrument.write, command)
                else:
                    raise

    async def close(self) -> None:
        """Close VISA handles exactly once and mark the device offline."""
        async with self._visa_lock:
            instrument = self._instrument
            resource_manager = self._resource_manager
            self._instrument = None
            self._resource_manager = None
            try:
                await asyncio.to_thread(
                    self._close_resources, instrument, resource_manager
                )
            finally:
                await super().close()

    @staticmethod
    def _close_resources(instrument: Any, resource_manager: Any) -> None:
        error = None
        if instrument is not None:
            try:
                instrument.close()
            except Exception as exc:  # noqa: BLE001
                error = exc
        if resource_manager is not None:
            try:
                resource_manager.close()
            except Exception as exc:  # noqa: BLE001
                if error is None:
                    error = exc
        if error is not None:
            raise error


@register_driver
class OscilloscopeDriver(Driver):
    """Driver for oscilloscope devices."""

    DRIVER_NAME = "oscilloscope"
    SUPPORTED_DEVICES: list[str] = ["oscilloscope"]  # noqa: RUF012

    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._visa_resource = cast(
            str, config.connection_params.get("visa_resource")
        )
        self._lifecycle_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Connect without replacing an already connected device."""
        async with self._lifecycle_lock:
            if self._device is not None and self._connected:
                return True
            device_id = self.config.connection_params.get(
                "device_id", "oscilloscope_001"
            )
            if self._device is None:
                self._device = OscilloscopeDevice(
                    device_id=device_id,
                    visa_resource=self._visa_resource,
                )
            device = cast(OscilloscopeDevice, self._device)
            self._connected = await device.connect()
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
