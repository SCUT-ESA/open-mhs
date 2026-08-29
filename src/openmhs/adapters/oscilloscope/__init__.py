"""Oscilloscope adapters for MHS.

Supports devices:
- UNI-T (UPO6102N)
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, Optional

import pyvisa

from openmhs.core.device import (
    BaseDevice, DeviceCapability, DeviceMetadata, DeviceState, SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver

class OscilloscopeDevice(BaseDevice):
    """Oscilloscope device."""
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
                      parameters={"channel": "Channel number"},
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
        self._instrument = None

    async def connect(self) -> bool:
        try:
            def open_instrument():
                rm = pyvisa.ResourceManager()
                return rm.open_resource(self._visa_resource)

            self._instrument = await asyncio.to_thread(open_instrument)

            identity = await self._query("*IDN?")
            self._set_state(DeviceState.ONLINE)
            return bool(identity)
        
        except Exception:
            self._set_state(DeviceState.ERROR)
            return False

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "identify":
            return {"value": await self._query("*IDN?")}
        elif capability == "measure_vpp":
            channel = int(params.get("channel", "1"))
            response = await self._query(f":MEASure:VPP? CHANnel{channel}")
            return {"channel": channel, "value": float(response), "unit": "V"}

        return {"value": None, "error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "run":
            await self._write(":RUN")
            return {"running": True}
        elif capability == "stop":
            await self._write(":STOP")
            return {"running": False}

        return {"error": f"Unknown capability: {capability}"}

    async def _query(self, command: str) -> str:
        if self._instrument is None:
            raise Exception("Not connected")
        return await asyncio.to_thread(self._instrument.query, command)

    async def _write(self, command: str) -> None:
        if self._instrument is None:
            raise Exception("Not connected")
        await asyncio.to_thread(self._instrument.write, command)

@register_driver
class OscilloscopeDriver(Driver):
    """Driver for oscilloscope devices."""

    DRIVER_NAME = "oscilloscope"
    SUPPORTED_DEVICES = ["oscilloscope"]
    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._visa_resource = config.connection_params.get("visa_resource")

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "oscilloscope_001")
        self._device = OscilloscopeDevice(device_id = device_id, visa_resource = self._visa_resource)
        result = await self._device.connect()
        self._connected = result
        return result
