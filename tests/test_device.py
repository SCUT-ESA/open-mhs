import unittest

import openmhs
from openmhs import core
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


class StrictDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_busy_and_simulated_state_gate_operations(self):
        device = ExampleDevice()
        for state in (
            DeviceState.UNKNOWN,
            DeviceState.BUSY,
            DeviceState.ERROR,
            DeviceState.MAINTENANCE,
        ):
            device._set_state(state)
            with self.assertRaises(DeviceError):
                await device.read("measurement")
            with self.assertRaises(DeviceError):
                await device.write("level", level=1)
            with self.assertRaises(DeviceError):
                await device.reset()

        device._set_state(DeviceState.SIMULATED)
        self.assertEqual((await device.read("measurement"))["value"], 42)
        self.assertEqual((await device.write("level", level=1))["params"], {"level": 1})
        self.assertTrue(await device.reset())
        self.assertEqual(device.state, DeviceState.SIMULATED)

    async def test_close_is_idempotent_and_allowed_as_cleanup(self):
        device = ExampleDevice()
        await device.close()
        await device.close()
        self.assertEqual(device.state, DeviceState.OFFLINE)

    async def test_capability_validation_happens_before_hook(self):
        class ValidationDevice(ExampleDevice):
            def __init__(self):
                super().__init__()
                self.hook_calls = 0
                self._metadata.capabilities.append(
                    DeviceCapability(
                        "strict",
                        schema={
                            "count": {"type": "integer"},
                            "label": {"type": "string"},
                        },
                        required=["count"],
                    )
                )

            async def _do_write(self, capability, **params):
                if capability == "strict":
                    self.hook_calls += 1
                return await super()._do_write(capability, **params)

        device = ValidationDevice()
        device._set_state(DeviceState.ONLINE)
        with self.assertRaises(CapabilityError):
            await device.write("strict", count=1, extra=True)
        with self.assertRaises(CapabilityError):
            await device.write("strict", count=True)
        self.assertEqual(device.hook_calls, 0)
        await device.write("strict", count=1, label="ok")
        self.assertEqual(device.hook_calls, 1)

    async def test_invalid_capability_schema_and_required_are_rejected(self):
        with self.assertRaises(TypeError):
            DeviceCapability("x", parameters={"value": {"type": "integer"}}, required="value")
        with self.assertRaises(ValueError):
            DeviceCapability(
                "x", parameters={"value": {"type": "integer"}}, required=["value", "value"]
            )
        with self.assertRaises(ValueError):
            DeviceCapability("x", parameters={"value": {"type": "integer"}}, required=["missing"])
        with self.assertRaises(ValueError):
            DeviceCapability(
                "x",
                parameters={"value": {"type": "integer"}},
                schema={"other": {"type": "string"}},
            )
        with self.assertRaises(ValueError):
            DeviceCapability("x", read_only=True, writable=True)

    async def test_nested_nonfinite_parameters_are_rejected_without_schema(self):
        class LegacyDevice(ExampleDevice):
            def __init__(self):
                super().__init__()
                self.hook_calls = 0
                self._metadata.capabilities.append(DeviceCapability("legacy"))

            async def _do_write(self, capability, **params):
                if capability == "legacy":
                    self.hook_calls += 1
                return await super()._do_write(capability, **params)

        device = LegacyDevice()
        device._set_state(DeviceState.ONLINE)
        with self.assertRaises(CapabilityError):
            await device.write("legacy", payload={"values": [float("-inf")]})
        assert device.hook_calls == 0
        await device.write("legacy", payload={"enabled": True})
        assert device.hook_calls == 1

    async def test_invalid_safety_values_are_rejected(self):
        device = ExampleDevice()
        device._set_state(DeviceState.ONLINE)
        for value in (True, "5", float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(SafetyError):
                await device.write("level", level=value)

        for bound in (True, "0", float("nan"), float("inf"), float("-inf")):
            with self.assertRaises((TypeError, ValueError)):
                SafetyLimit("level", min_value=bound)


if __name__ == "__main__":
    unittest.main()
