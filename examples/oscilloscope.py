"""Example: Read oscilloscope data with Open MHS."""

import asyncio
from openmhs.adapters.oscilloscope import OscilloscopeDriver
from openmhs.core.driver import DriverConfig

import pyvisa

async def main():

    # Discover the oscilloscope
    visa_resource = discover_oscilloscope()
    
    # Oscilloscope
    osc_config = DriverConfig(
        driver_name="oscilloscope",
        connection_params={"device_id": "oscilloscope", "visa_resource": visa_resource}
    )
    osc = OscilloscopeDriver(osc_config)
    await osc.connect()
    
    # Identify the oscilloscope
    identity = await osc.device.read("identify")
    print(f"Oscilloscope Identity: {identity['value']}")
    
    # Start acquisition
    await osc.device.write("run")
    print("Acquisition started.")
    
    # Measure peak-to-peak voltage on channel 1
    vpp = await osc.device.read("measure_vpp", channel=1)
    print(f"Peak-to-Peak Voltage (Channel {vpp['channel']}): {vpp['value']} {vpp['unit']}")
    
    # Stop acquisition
    await osc.device.write("stop")
    print("Acquisition stopped.")
    
    await osc.disconnect()

def discover_oscilloscope() -> str:
    rm = pyvisa.ResourceManager()

    try:
        # ?*::INSTR 会列出 VISA 发现的仪器资源
        resources = rm.list_resources("?*::INSTR")

        for resource_name in resources:
            instrument = None

            try:
                instrument = rm.open_resource(resource_name)
                instrument.timeout = 2000

                identity = instrument.query("*IDN?").strip()
                print(f"Found {resource_name}: {identity}")

                # 你的设备返回：
                # UNI-T Technologies,UPO6102N,APXA326100149,1. 01. 0027
                fields = [field.strip().upper() for field in identity.split(",")]

                if len(fields) >= 2 and (
                    "UNI-T" in fields[0] and fields[1] == "UPO6102N"
                ):
                    return resource_name

            except Exception:
                # 某些被发现的资源可能不是可查询的仪器
                pass

            finally:
                if instrument is not None:
                    instrument.close()

    finally:
        rm.close()

    raise RuntimeError("No UNI-T UPO6102N oscilloscope found")

if __name__ == "__main__":
    asyncio.run(main())