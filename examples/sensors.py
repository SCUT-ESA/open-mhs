"""Example: Read simulated sensor data with Open MHS."""

from __future__ import annotations

import asyncio

from openmhs.adapters.sensors import BME280Driver, HCSR04Driver
from openmhs.core.driver import DriverConfig


async def main():
    # Temperature/humidity/pressure sensor in simulation mode
    bme_config = DriverConfig(
        driver_name="bme280",
        connection_params={"device_id": "env_sensor", "i2c_bus": 1, "i2c_address": 0x76},
        simulation=True,
    )
    bme = BME280Driver(bme_config)
    await bme.connect()

    # Ultrasonic distance sensor in simulation mode
    dist_config = DriverConfig(
        driver_name="hcsr04",
        connection_params={"device_id": "distance_sensor", "trigger_pin": 23, "echo_pin": 24},
        simulation=True,
    )
    dist = HCSR04Driver(dist_config)
    await dist.connect()

    try:
        if bme.device:
            temp = await bme.device.read("temperature")
            humidity = await bme.device.read("humidity")
            pressure = await bme.device.read("pressure")

            print(f"Temperature: {temp['value']} {temp['unit']}")
            print(f"Humidity: {humidity['value']} {humidity['unit']}")
            print(f"Pressure: {pressure['value']} {pressure['unit']}")

        if dist.device:
            distance = await dist.device.read("distance")
            print(f"Distance: {distance['value']} {distance['unit']}")

    finally:
        await bme.disconnect()
        await dist.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
