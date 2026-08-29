import unittest

import openmhs
import openmhs.core as core
from openmhs.core.device import (
    BaseDevice,
    CapabilityError,
    Device,
    DeviceCapability,
    DeviceError,
    DeviceMetadata,
    DeviceState,
    SafetyError,
    SafetyLimit,
)


class ExampleDevice(BaseDevice):
    def __init__(self):
        super().__init__(
            DeviceMetadata(
                device_id="example",
                device_type="example_device",
                capabilities=[
                    DeviceCapability("measurement", read_only=True),
                    DeviceCapability("level"),
                ],
                safety_limits=[SafetyLimit("level", 0, 10)],
            )
        )

    async def _do_read(self, capability: str, **params):
        if capability == "measurement":
            return {"value": 42}
        return {"capability": capability, "params": params}

    async def _do_write(self, capability: str, **params):
        return {"capability": capability, "params": params}


class DeviceModuleTests(unittest.TestCase):
    def test_device_types_are_explicitly_imported(self):
        for name in (
            "AccessLevel",
            "BaseDevice",
            "CapabilityError",
            "Device",
            "DeviceCapability",
            "DeviceError",
            "DeviceMetadata",
            "DeviceState",
            "SafetyError",
            "SafetyLimit",
        ):
            self.assertFalse(hasattr(core, name), name)

        self.assertIs(openmhs.Device, Device)
        self.assertIs(openmhs.DeviceCapability, DeviceCapability)
        self.assertIs(openmhs.DeviceMetadata, DeviceMetadata)
        self.assertIs(openmhs.SafetyLimit, SafetyLimit)


class BaseDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.device = ExampleDevice()
        self.device._set_state(DeviceState.ONLINE)

    async def test_discover_read_and_write(self):
        self.assertEqual(
            await self.device.discover(),
            ["measurement", "level"],
        )
        self.assertEqual(
            await self.device.read("measurement"),
            {"value": 42},
        )
        self.assertEqual(
            await self.device.write("level", level=5),
            {"capability": "level", "params": {"level": 5}},
        )

    async def test_invalid_capabilities_and_read_only_write(self):
        with self.assertRaises(CapabilityError):
            await self.device.read("missing")

        with self.assertRaises(CapabilityError):
            await self.device.write("missing")

        with self.assertRaises(CapabilityError):
            await self.device.write("measurement")

    async def test_safety_limits(self):
        await self.device.write("level", level=0)
        await self.device.write("level", level=10)

        with self.assertRaises(SafetyError):
            await self.device.write("level", level=11)

    async def test_state_checks_and_lifecycle(self):
        self.device._set_state(DeviceState.MAINTENANCE)
        with self.assertRaises(DeviceError):
            await self.device.write("level", level=5)

        self.device._set_state(DeviceState.ONLINE)
        health = await self.device.health_check()
        self.assertTrue(health["healthy"])
        self.assertEqual(health["state"], "ONLINE")

        self.assertTrue(await self.device.reset())
        self.assertEqual(self.device.state, DeviceState.ONLINE)

        await self.device.close()
        self.assertEqual(self.device.state, DeviceState.OFFLINE)
        self.assertFalse((await self.device.health_check())["healthy"])

        with self.assertRaises(DeviceError):
            await self.device.read("measurement")


if __name__ == "__main__":
    unittest.main()
