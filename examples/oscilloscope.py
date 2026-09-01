"""Example: Read oscilloscope data with Open MHS."""

from __future__ import annotations

import asyncio

import pyvisa

from openmhs.adapters.oscilloscope import OscilloscopeDriver
from openmhs.core.driver import DriverConfig


def discover_oscilloscope() -> str:
    rm = pyvisa.ResourceManager()

    try:
        resources = rm.list_resources("?*::INSTR")

        for resource_name in resources:
            instrument = None

            try:
                instrument = rm.open_resource(resource_name)
                instrument.timeout = 2000

                identity = instrument.query("*IDN?").strip()
                print(f"Found {resource_name}: {identity}")

                fields = [field.strip().upper() for field in identity.split(",")]

                if len(fields) >= 2 and ("UNI-T" in fields[0] and fields[1] == "UPO6102N"):
                    return resource_name

            except Exception:
                pass

            finally:
                if instrument is not None:
                    instrument.close()

    finally:
        rm.close()

    raise RuntimeError("No UNI-T UPO6102N oscilloscope found")


async def main():
    # Discover the oscilloscope
    try:
        visa_resource = discover_oscilloscope()
    except Exception as exc:
        print(f"Oscilloscope discovery: {exc}")
        return

    # Oscilloscope
    osc_config = DriverConfig(
        driver_name="oscilloscope",
        connection_params={"device_id": "oscilloscope", "visa_resource": visa_resource},
    )
    osc = OscilloscopeDriver(osc_config)
    ok = await osc.connect()
    if not ok or osc.device is None:
        print("Failed to connect to oscilloscope.")
        return

    try:
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

    finally:
        await osc.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
