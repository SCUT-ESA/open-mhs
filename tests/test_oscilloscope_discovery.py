"""Tests for oscilloscope discovery without physical hardware."""

import unittest
from unittest.mock import MagicMock, patch

try:
    import pyvisa
except ImportError:
    pyvisa = None

if pyvisa is not None:
    from openmhs.adapters.oscilloscope import OscilloscopeDriver
    from openmhs.adapters.oscilloscope.discovery import (
        OscilloscopeDiscoveryManager,
    )
    from openmhs.core.device import DeviceMetadata, DeviceState
    from openmhs.core.driver import DriverConfig
    from openmhs.core.registry import DeviceRegistry


class FakeDevice:
    def __init__(self, device_id):
        self.metadata = DeviceMetadata(device_id, "oscilloscope")
        self.state = DeviceState.ONLINE
        self.close_count = 0

    async def close(self):
        self.close_count += 1
        self.state = DeviceState.OFFLINE


class FakeDriver:
    def __init__(self, config, connected=True):
        self.config = config
        self.device = FakeDevice(config.connection_params["device_id"])
        self.connected = connected
        self.connect_count = 0
        self.disconnect_count = 0

    async def connect(self):
        self.connect_count += 1
        return self.connected

    async def disconnect(self):
        self.disconnect_count += 1
        await self.device.close()


@unittest.skipIf(pyvisa is None, "pyvisa optional dependency is not installed")
class OscilloscopeDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def make_resource_manager(self, resources):
        resource_manager = MagicMock()
        resource_manager.list_resources.return_value = tuple(resources)
        return resource_manager

    async def test_scan_registers_once_and_close_is_idempotent(self):
        resource = "USB0::SCOPE::INSTR"
        resource_manager = self.make_resource_manager([resource])
        instrument = MagicMock()
        instrument.query.return_value = "UNI-T Technologies,UPO6102N,SN123,1.0"
        resource_manager.open_resource.return_value = instrument

        drivers = []

        def driver_factory(config):
            driver = FakeDriver(config)
            drivers.append(driver)
            return driver

        registry = DeviceRegistry()
        manager = OscilloscopeDiscoveryManager(
            registry,
            resource_manager_factory=lambda: resource_manager,
            driver_factory=driver_factory,
        )

        first = await manager.scan_once()
        second = await manager.scan_once()

        self.assertEqual(len(first.added), 1)
        self.assertEqual(second.added, ())
        self.assertEqual(registry.list_devices(), [first.added[0].device_id])
        self.assertNotIn("_", first.added[0].device_id)
        self.assertEqual(len(drivers), 1)
        self.assertEqual(drivers[0].connect_count, 1)
        self.assertEqual(instrument.close.call_count, 2)
        self.assertEqual(resource_manager.close.call_count, 2)

        await manager.close()
        await manager.close()

        self.assertEqual(registry.list_devices(), [])
        self.assertEqual(drivers[0].disconnect_count, 1)
        self.assertEqual(drivers[0].device.state, DeviceState.OFFLINE)

    async def test_scan_continues_after_resource_error(self):
        bad_resource = "USB0::BAD::INSTR"
        other_resource = "USB0::OTHER::INSTR"
        scope_resource = "USB0::SCOPE::INSTR"
        resource_manager = self.make_resource_manager(
            [bad_resource, other_resource, scope_resource]
        )

        bad = MagicMock()
        bad.query.side_effect = TimeoutError("query timed out")
        other = MagicMock()
        other.query.return_value = "OTHER,MODEL,SN,1.0"
        scope = MagicMock()
        scope.query.return_value = "UNI-T,UPO6102N,SN,1.0"
        resource_manager.open_resource.side_effect = [bad, other, scope]

        registry = DeviceRegistry()
        manager = OscilloscopeDiscoveryManager(
            registry,
            resource_manager_factory=lambda: resource_manager,
            driver_factory=FakeDriver,
        )

        result = await manager.scan_once()

        self.assertEqual(len(result.added), 1)
        self.assertEqual(result.added[0].resource, scope_resource)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].resource, bad_resource)
        bad.close.assert_called_once_with()
        other.close.assert_called_once_with()
        scope.close.assert_called_once_with()
        resource_manager.close.assert_called_once_with()

        await manager.close()

    async def test_failed_driver_is_not_registered(self):
        resource = "USB0::SCOPE::INSTR"
        resource_manager = self.make_resource_manager([resource])
        instrument = MagicMock()
        instrument.query.return_value = "UNI-T,UPO6102N,SN,1.0"
        resource_manager.open_resource.return_value = instrument

        drivers = []

        def driver_factory(config):
            driver = FakeDriver(config, connected=False)
            drivers.append(driver)
            return driver

        registry = DeviceRegistry()
        manager = OscilloscopeDiscoveryManager(
            registry,
            resource_manager_factory=lambda: resource_manager,
            driver_factory=driver_factory,
        )

        result = await manager.scan_once()

        self.assertEqual(result.added, ())
        self.assertEqual(registry.list_devices(), [])
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(drivers[0].disconnect_count, 1)

    async def test_oscilloscope_device_releases_visa_resources(self):
        resource_manager = MagicMock()
        instrument = MagicMock()
        instrument.query.return_value = "UNI-T,UPO6102N,SN,1.0"
        resource_manager.open_resource.return_value = instrument
        config = DriverConfig(
            driver_name="oscilloscope",
            connection_params={
                "device_id": "scope-test",
                "visa_resource": "USB0::SCOPE::INSTR",
            },
        )

        with patch(
            "openmhs.adapters.oscilloscope.pyvisa.ResourceManager",
            return_value=resource_manager,
        ):
            driver = OscilloscopeDriver(config)
            self.assertTrue(await driver.connect())
            device = driver.device
            await driver.disconnect()

        instrument.close.assert_called_once_with()
        resource_manager.close.assert_called_once_with()
        self.assertEqual(device.state, DeviceState.OFFLINE)


if __name__ == "__main__":
    unittest.main()
