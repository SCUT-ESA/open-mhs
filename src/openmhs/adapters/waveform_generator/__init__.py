"""UNI-T UTG2062X waveform-generator adapter."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from typing import Any, ClassVar, cast

try:
    import pyvisa
except ImportError:  # pragma: no cover
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

logger = logging.getLogger(__name__)

_WAVEFORMS = {
    "sine": "SINe",
    "square": "SQUare",
    "pulse": "PULSe",
    "ramp": "RAMP",
    "arb": "ARB",
    "noise": "NOISe",
    "dc": "DC",
    "harmonic": "HARMonic",
    "exp": "EXP",
}


def _channel(params: dict[str, Any], default: int | None = None) -> int:
    value = params.get("channel", default)
    if isinstance(value, bool):
        raise CapabilityError("channel must be an integer from 1 to 2")
    try:
        channel = int(cast(str | int | float, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise CapabilityError("channel must be an integer from 1 to 2") from exc
    if isinstance(value, float) and value != channel:
        raise CapabilityError("channel must be an integer from 1 to 2")
    if isinstance(value, str) and value.strip() != str(channel):
        raise CapabilityError("channel must be an integer from 1 to 2")
    if channel not in (1, 2):
        raise CapabilityError("channel must be an integer from 1 to 2")
    return channel


class WaveformGeneratorDevice(BaseDevice):
    """UTG2062X device; setters never run during connect/discovery."""

    def __init__(
        self,
        device_id: str,
        visa_resource: str,
        resource_manager_factory: Callable[[], Any] | None = None,
    ):
        capabilities = [
            DeviceCapability("identify", "Query waveform generator identity", read_only=True),
            DeviceCapability(
                "output_state",
                "Read output state for the specified channel",
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
                "output",
                "Enable or disable output for the specified channel",
                {
                    "channel": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2,
                        "description": "Channel number",
                    },
                    "enabled": {"type": "boolean", "description": "Output state"},
                },
            ),
            DeviceCapability(
                "waveform",
                "Set waveform type for the specified channel",
                {
                    "channel": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2,
                        "description": "Channel number",
                    },
                    "waveform": {"type": "string", "description": "Waveform name"},
                },
            ),
            DeviceCapability(
                "amplitude",
                "Set amplitude for the specified channel in the instrument's current voltage unit",
                {
                    "channel": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2,
                        "description": "Channel number",
                    },
                    "amplitude": {
                        "type": "number",
                        "description": "Positive amplitude in the instrument's current voltage unit",
                    },
                },
            ),
        ]
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="waveform_generator",
            manufacturer="UNI-T",
            model="UTG2062X",
            capabilities=capabilities,
            tags=["waveform_generator", "visa"],
            driver_class="waveform_generator",
            connection_info={"visa_resource": visa_resource},
            natural_language_description="A UNI-T UTG2062X waveform generator.",
        )
        super().__init__(metadata)
        factory = resource_manager_factory or (
            pyvisa.ResourceManager if pyvisa is not None else None
        )
        self._session = VisaSession(visa_resource, factory)

    async def connect(self) -> bool:
        if self._session.is_open:
            return self.state is DeviceState.ONLINE
        try:
            await self._session.open()
            identity = await self._session.query("*IDN?")
            fields = [field.strip() for field in identity.split(",")]
            if (
                len(fields) < 2
                or " ".join(fields[0].split()).casefold() not in {"uni-t", "uni-t technologies"}
                or " ".join(fields[1].split()).casefold() != "utg2062x"
            ):
                raise RuntimeError(f"Unsupported waveform generator identity: {identity.strip()}")
            self._set_state(DeviceState.ONLINE)
            return True
        except asyncio.CancelledError:
            try:
                await self._session.close()
            except BaseException:
                logger.exception("Failed to clean up cancelled waveform-generator connect")
            self._set_state(DeviceState.ERROR)
            raise
        except Exception as primary:  # noqa: BLE001
            try:
                await self._session.close()
            except BaseException:
                logger.exception("Failed to clean up failed waveform-generator connect")
                logger.error("Connect failure was: %s", primary)
            self._set_state(DeviceState.ERROR)
            return False

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "identify":
            return {"value": await self._session.query("*IDN?")}
        channel = _channel(params, default=1 if capability == "output_state" else None)
        if capability == "output_state":
            response = (await self._session.query(f":CHANnel{channel}:OUTPut?")).strip().upper()
            if response in {"ON", "TRUE", "1"}:
                state = True
            elif response in {"OFF", "FALSE", "0"}:
                state = False
            else:
                raise CapabilityError(f"Unrecognized output state: {response}")
            return {"channel": channel, "enabled": state}
        return {"value": None, "error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        channel = _channel(params)
        if capability == "output":
            enabled = params.get("enabled")
            if not isinstance(enabled, bool):
                raise CapabilityError("enabled must be a boolean")
            await self._session.write(f":CHANnel{channel}:OUTPut {'ON' if enabled else 'OFF'}")
            return {"channel": channel, "enabled": enabled}
        if capability == "waveform":
            value = params.get("waveform")
            if not isinstance(value, str) or value.lower() not in _WAVEFORMS:
                raise CapabilityError(f"Unsupported waveform: {value}")
            await self._session.write(f":CHANnel{channel}:BASE:WAVe {_WAVEFORMS[value.lower()]}")
            return {"channel": channel, "waveform": value.lower()}
        if capability == "amplitude":
            value = params.get("amplitude")
            if isinstance(value, bool):
                raise CapabilityError("amplitude must be a finite positive number")
            try:
                amplitude = float(cast(str | int | float, value))
            except (TypeError, ValueError, OverflowError) as exc:
                raise CapabilityError("amplitude must be a finite positive number") from exc
            if not math.isfinite(amplitude) or amplitude <= 0:
                raise CapabilityError("amplitude must be a finite positive number")
            await self._session.write(f":CHANnel{channel}:BASE:AMPLitude {amplitude}")
            return {"channel": channel, "amplitude": amplitude}
        return {"error": f"Unknown capability: {capability}"}

    async def close(self) -> None:
        try:
            await self._session.close()
        finally:
            await super().close()


@register_driver
class WaveformGeneratorDriver(Driver):
    """Driver for UNI-T UTG2062X waveform generators."""

    DRIVER_NAME = "waveform_generator"
    SUPPORTED_DEVICES: ClassVar[list[str]] = ["waveform_generator"]

    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._visa_resource = cast(str, config.connection_params.get("visa_resource"))
        self._lifecycle_lock = asyncio.Lock()

    async def connect(self) -> bool:
        async with self._lifecycle_lock:
            if self._device is not None and self._connected:
                return True
            device_id = self.config.connection_params.get("device_id", "wavegen_001")
            if self._device is None:
                self._device = WaveformGeneratorDevice(
                    device_id,
                    self._visa_resource,
                    self.config.connection_params.get("resource_manager_factory"),
                )
            self._connected = await cast(WaveformGeneratorDevice, self._device).connect()
            return self._connected

    async def disconnect(self) -> None:
        async with self._lifecycle_lock:
            device = self._device
            self._connected = False
            if device is None:
                return
            try:
                await device.close()
            finally:
                self._device = None


__all__ = ["WaveformGeneratorDevice", "WaveformGeneratorDriver"]
