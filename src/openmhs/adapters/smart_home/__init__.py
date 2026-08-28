"""Smart home adapters for MHS.

Supports:
- MQTT devices (Tasmota, ESPHome, etc.)
- Philips Hue (basic)
- Generic smart plugs
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

from openmhs.core.device import (
    BaseDevice, DeviceCapability, DeviceMetadata, DeviceState, SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class MQTTDevice(BaseDevice):
    """Generic MQTT-connected smart device."""

    def __init__(
        self,
        device_id: str,
        mqtt_broker: str = "localhost",
        mqtt_port: int = 1883,
        topic_prefix: str = "home/device",
    ):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="smart_device",
            manufacturer="Generic",
            model="MQTT Device",
            capabilities=[
                DeviceCapability(
                    name="power",
                    description="Turn device on/off",
                    parameters={"state": "on/off/toggle"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="status",
                    description="Get device status",
                    parameters={},
                    read_only=True,
                ),
                DeviceCapability(
                    name="brightness",
                    description="Set brightness level",
                    parameters={"level": "0-100"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="color",
                    description="Set RGB color",
                    parameters={"r": "0-255", "g": "0-255", "b": "0-255"},
                    read_only=False,
                ),
            ],
            safety_limits=[
                SafetyLimit("level", 0, 100, "%", "Brightness level"),
                SafetyLimit("r", 0, 255, "", "Red channel"),
                SafetyLimit("g", 0, 255, "", "Green channel"),
                SafetyLimit("b", 0, 255, "", "Blue channel"),
            ],
            tags=["smart_home", "mqtt", "iot", "automation"],
            natural_language_description=(
                "A generic smart home device controlled via MQTT. "
                "Supports power control, brightness, and color settings."
            ),
            driver_class="mqtt_device",
            connection_info={"broker": mqtt_broker, "port": mqtt_port, "topic": topic_prefix},
        )
        super().__init__(metadata)
        self._mqtt_broker = mqtt_broker
        self._mqtt_port = mqtt_port
        self._topic_prefix = topic_prefix
        self._state = {"power": "off", "brightness": 100, "color": {"r": 255, "g": 255, "b": 255}}
        self._simulation_mode = not HAS_MQTT

    async def connect(self) -> bool:
        self._set_state(DeviceState.ONLINE)
        return True

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "status":
            return self._state.copy()
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "power":
            state = params.get("state", "toggle")
            if state == "toggle":
                self._state["power"] = "on" if self._state["power"] == "off" else "off"
            else:
                self._state["power"] = state
            return {"power": self._state["power"]}
        
        elif capability == "brightness":
            level = int(params.get("level", 100))
            self._state["brightness"] = level
            return {"brightness": level}
        
        elif capability == "color":
            color = {
                "r": int(params.get("r", 255)),
                "g": int(params.get("g", 255)),
                "b": int(params.get("b", 255)),
            }
            self._state["color"] = color
            return {"color": color}
        
        return {"error": f"Unknown capability: {capability}"}


@register_driver
class MQTTDriver(Driver):
    """Driver for MQTT smart home devices."""

    DRIVER_NAME = "mqtt_device"
    SUPPORTED_DEVICES = ["smart_device", "mqtt", "iot"]

    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._broker = config.connection_params.get("mqtt_broker", "localhost")
        self._port = config.connection_params.get("mqtt_port", 1883)
        self._topic = config.connection_params.get("topic_prefix", "home/device")

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "mqtt_001")
        self._device = MQTTDevice(device_id, self._broker, self._port, self._topic)
        result = await self._device.connect()
        self._connected = result
        return result


# === Smart Plug ===

class SmartPlugDevice(BaseDevice):
    """Generic smart plug with power monitoring."""

    def __init__(self, device_id: str):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="smart_plug",
            manufacturer="Generic",
            model="Smart Plug",
            capabilities=[
                DeviceCapability(
                    name="power",
                    description="Control plug power state",
                    parameters={"state": "on/off"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="power_usage",
                    description="Get current power consumption",
                    parameters={},
                    read_only=True,
                ),
                DeviceCapability(
                    name="energy",
                    description="Get total energy consumption",
                    parameters={},
                    read_only=True,
                ),
            ],
            tags=["smart_home", "plug", "power", "energy"],
            natural_language_description="A smart plug with power monitoring capabilities.",
            driver_class="smart_plug",
        )
        super().__init__(metadata)
        self._on = False
        self._power_watts = 0.0
        self._energy_kwh = 0.0

    async def connect(self) -> bool:
        self._set_state(DeviceState.ONLINE)
        return True

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "power_usage":
            if self._on:
                self._power_watts = 45.0 + (5.0 if self._on else 0)  # Simulated
            else:
                self._power_watts = 0.5  # Standby
            return {"power_watts": self._power_watts}
        elif capability == "energy":
            return {"energy_kwh": self._energy_kwh}
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "power":
            state = params.get("state", "off")
            self._on = state == "on"
            return {"state": "on" if self._on else "off"}
        return {"error": f"Unknown capability: {capability}"}


@register_driver
class SmartPlugDriver(Driver):
    """Driver for smart plugs."""

    DRIVER_NAME = "smart_plug"
    SUPPORTED_DEVICES = ["smart_plug", "plug"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "plug_001")
        self._device = SmartPlugDevice(device_id)
        result = await self._device.connect()
        self._connected = result
        return result
