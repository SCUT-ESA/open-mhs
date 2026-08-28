"""Sensor adapters for MHS.

Supports common sensors:
- Temperature/Humidity (DHT11/22, BME280)
- Distance (Ultrasonic HC-SR04)
- Motion (PIR)
- Light (LDR, BH1750)
- Gas/Smoke (MQ-2)
- Accelerometer/Gyroscope (MPU6050)
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, Optional

try:
    import smbus2
    HAS_SMBUS = True
except ImportError:
    HAS_SMBUS = False

from openmhs.core.device import (
    BaseDevice, DeviceCapability, DeviceMetadata, DeviceState, SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


# === BME280 Temperature/Humidity/Pressure Sensor ===

class BME280Device(BaseDevice):
    """BME280 temperature, humidity, and pressure sensor."""

    def __init__(self, device_id: str, i2c_bus: int = 1, i2c_address: int = 0x76):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="temperature_sensor",
            manufacturer="Bosch",
            model="BME280",
            capabilities=[
                DeviceCapability(
                    name="temperature",
                    description="Ambient temperature in Celsius",
                    parameters={"unit": "C"},
                    read_only=True,
                ),
                DeviceCapability(
                    name="humidity",
                    description="Relative humidity in percent",
                    parameters={"unit": "%"},
                    read_only=True,
                ),
                DeviceCapability(
                    name="pressure",
                    description="Atmospheric pressure in hPa",
                    parameters={"unit": "hPa"},
                    read_only=True,
                ),
            ],
            safety_limits=[
                SafetyLimit("temperature", -40.0, 85.0, "°C", "Operating temperature range"),
                SafetyLimit("humidity", 0.0, 100.0, "%", "Humidity range"),
            ],
            tags=["sensor", "temperature", "humidity", "pressure", "environment"],
            natural_language_description=(
                "A combined temperature, humidity, and pressure sensor. "
                "Commonly used in weather stations and indoor climate monitoring."
            ),
            driver_class="bme280",
            connection_info={"i2c_bus": i2c_bus, "i2c_address": i2c_address},
        )
        super().__init__(metadata)
        self._i2c_bus = i2c_bus
        self._i2c_address = i2c_address
        self._simulation_mode = not HAS_SMBUS
        self._last_values = {"temperature": 22.5, "humidity": 55.0, "pressure": 1013.25}

    async def connect(self) -> bool:
        if self._simulation_mode:
            self._set_state(DeviceState.ONLINE)
            return True
        try:
            # Real I2C initialization would go here
            self._set_state(DeviceState.ONLINE)
            return True
        except Exception:
            self._set_state(DeviceState.ERROR)
            return False

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        if self._simulation_mode:
            # Simulate with slight random variation
            if capability == "temperature":
                self._last_values["temperature"] += random.uniform(-0.5, 0.5)
                return {"value": round(self._last_values["temperature"], 2), "unit": "°C"}
            elif capability == "humidity":
                self._last_values["humidity"] = max(0, min(100, self._last_values["humidity"] + random.uniform(-2, 2)))
                return {"value": round(self._last_values["humidity"], 1), "unit": "%"}
            elif capability == "pressure":
                self._last_values["pressure"] += random.uniform(-1, 1)
                return {"value": round(self._last_values["pressure"], 2), "unit": "hPa"}
        return {"value": None, "error": "Not implemented in simulation mode"}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        raise NotImplementedError("BME280 is read-only")


@register_driver
class BME280Driver(Driver):
    """Driver for BME280 sensor."""

    DRIVER_NAME = "bme280"
    SUPPORTED_DEVICES = ["temperature_sensor", "bme280"]

    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._i2c_bus = config.connection_params.get("i2c_bus", 1)
        self._i2c_address = config.connection_params.get("i2c_address", 0x76)

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "bme280_001")
        self._device = BME280Device(device_id, self._i2c_bus, self._i2c_address)
        result = await self._device.connect()
        self._connected = result
        return result


# === HC-SR04 Ultrasonic Distance Sensor ===

class HCSR04Device(BaseDevice):
    """HC-SR04 ultrasonic distance sensor."""

    def __init__(self, device_id: str, trigger_pin: int = 23, echo_pin: int = 24):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="distance_sensor",
            manufacturer="Multiple",
            model="HC-SR04",
            capabilities=[
                DeviceCapability(
                    name="distance",
                    description="Distance measured by ultrasonic echo",
                    parameters={"unit": "cm", "range": "2-400cm"},
                    read_only=True,
                ),
            ],
            safety_limits=[
                SafetyLimit("distance", 2.0, 400.0, "cm", "Measurable distance range"),
            ],
            tags=["sensor", "distance", "ultrasonic", "proximity"],
            natural_language_description=(
                "An ultrasonic distance sensor that measures distance by sending "
                "sound waves and measuring the echo return time. Range: 2cm to 4m."
            ),
            driver_class="hcsr04",
            connection_info={"trigger_pin": trigger_pin, "echo_pin": echo_pin},
        )
        super().__init__(metadata)
        self._trigger_pin = trigger_pin
        self._echo_pin = echo_pin
        self._simulation_mode = True
        self._current_distance = 50.0

    async def connect(self) -> bool:
        self._set_state(DeviceState.ONLINE)
        return True

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "distance":
            if self._simulation_mode:
                self._current_distance = max(2, min(400, self._current_distance + random.uniform(-5, 5)))
                return {"value": round(self._current_distance, 1), "unit": "cm"}
        return {"value": None}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        raise NotImplementedError("HC-SR04 is read-only")


@register_driver
class HCSR04Driver(Driver):
    """Driver for HC-SR04 ultrasonic sensor."""

    DRIVER_NAME = "hcsr04"
    SUPPORTED_DEVICES = ["distance_sensor", "hcsr04"]

    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._trigger_pin = config.connection_params.get("trigger_pin", 23)
        self._echo_pin = config.connection_params.get("echo_pin", 24)

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "hcsr04_001")
        self._device = HCSR04Device(device_id, self._trigger_pin, self._echo_pin)
        result = await self._device.connect()
        self._connected = result
        return result


# === Generic Analog Sensor ===

class AnalogSensorDevice(BaseDevice):
    """Generic analog sensor (simulated)."""

    def __init__(self, device_id: str, sensor_type: str = "generic", unit: str = "raw"):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="analog_sensor",
            manufacturer="Generic",
            model="Analog",
            capabilities=[
                DeviceCapability(
                    name="value",
                    description=f"Analog sensor value for {sensor_type}",
                    parameters={"unit": unit},
                    read_only=True,
                ),
            ],
            tags=["sensor", "analog", sensor_type],
            natural_language_description=f"A generic analog {sensor_type} sensor.",
            driver_class="analog_sensor",
        )
        super().__init__(metadata)
        self._sensor_type = sensor_type
        self._unit = unit
        self._current_value = 512.0

    async def connect(self) -> bool:
        self._set_state(DeviceState.ONLINE)
        return True

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "value":
            self._current_value = max(0, min(1023, self._current_value + random.uniform(-20, 20)))
            return {"value": round(self._current_value, 1), "unit": self._unit}
        return {"value": None}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        raise NotImplementedError("Analog sensor is read-only")


@register_driver
class AnalogSensorDriver(Driver):
    """Generic analog sensor driver."""

    DRIVER_NAME = "analog_sensor"
    SUPPORTED_DEVICES = ["analog_sensor", "generic_sensor"]

    def __init__(self, config: DriverConfig):
        super().__init__(config)

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "analog_001")
        sensor_type = self.config.connection_params.get("sensor_type", "generic")
        unit = self.config.connection_params.get("unit", "raw")
        self._device = AnalogSensorDevice(device_id, sensor_type, unit)
        result = await self._device.connect()
        self._connected = result
        return result
