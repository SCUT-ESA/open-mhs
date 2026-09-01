"""Example: Smart home automation with Open MHS (simulation mode)."""

from __future__ import annotations

import asyncio

from openmhs.adapters.smart_home import MQTTDriver, SmartPlugDriver
from openmhs.core.driver import DriverConfig


async def main():
    # Smart plug in simulation mode
    plug_config = DriverConfig(
        driver_name="smart_plug",
        connection_params={"device_id": "living_room_plug"},
        simulation=True,
    )
    plug = SmartPlugDriver(plug_config)
    await plug.connect()

    # MQTT light in simulation mode
    mqtt_config = DriverConfig(
        driver_name="mqtt_device",
        connection_params={
            "device_id": "bedroom_light",
            "mqtt_broker": "192.168.1.100",
            "topic_prefix": "home/bedroom/light",
        },
        simulation=True,
    )
    light = MQTTDriver(mqtt_config)
    await light.connect()

    try:
        if plug.device:
            # Turn on
            result = await plug.device.write("power", state="on")
            print(f"Plug: {result}")

            # Check power usage
            usage = await plug.device.read("power_usage")
            print(f"Power usage: {usage['power_watts']}W")

        if light.device:
            # Turn on and set color
            await light.device.write("power", state="on")
            await light.device.write("brightness", level=75)
            await light.device.write("color", r=255, g=200, b=150)

            status = await light.device.read("status")
            print(f"Light status: {status}")

    finally:
        await plug.disconnect()
        await light.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
