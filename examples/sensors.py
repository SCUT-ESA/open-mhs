"""Example: Read sensor data with Open MHS."""

import asyncio
from openmhs.adapters.sensors import BME280Driver, HCSR04Driver
from openmhs.core.driver import DriverConfig

async def main():
    # Temperature/humidity/pressure sensor
    bme_config = DriverConfig(
        driver_name="bme280",
        connection_params={"device_id": "env_sensor", "i2c_bus": 1, "i2c_address": 0x76}
    )
    bme = BME280Driver(bme_config)
    await bme.connect()
    
    temp = await bme.device.read("temperature")
    humidity = await bme.device.read("humidity")
    pressure = await bme.device.read("pressure")
    
    print(f"Temperature: {temp['value']} {temp['unit']}")
    print(f"Humidity: {humidity['value']} {humidity['unit']}")
    print(f"Pressure: {pressure['value']} {pressure['unit']}")
    
    # Ultrasonic distance sensor
    dist_config = DriverConfig(
        driver_name="hcsr04",
        connection_params={"device_id": "distance_sensor", "trigger_pin": 23, "echo_pin": 24}
    )
    dist = HCSR04Driver(dist_config)
    await dist.connect()
    
    distance = await dist.device.read("distance")
    print(f"Distance: {distance['value']} {distance['unit']}")
    
    await bme.disconnect()
    await dist.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
