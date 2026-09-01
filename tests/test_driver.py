import asyncio
import math
import sys
import types

import pytest

from openmhs.core.device import DeviceMetadata
from openmhs.core.driver import Driver, DriverConfig


def test_driver_config_rejects_unsafe_values():
    with pytest.raises(ValueError):
        DriverConfig("")
    for timeout in (0, -1, math.nan, math.inf, 10**1000):
        with pytest.raises(ValueError):
            DriverConfig("driver", timeout=timeout)
    for retry in (-1, True, 1.5):
        with pytest.raises((TypeError, ValueError)):
            DriverConfig("driver", retry_count=retry)
    with pytest.raises(ValueError):
        DriverConfig("driver", safety_override=True)
    with pytest.raises(TypeError):
        DriverConfig("driver", auto_connect=1)


@pytest.mark.asyncio
async def test_base_disconnect_is_idempotent_and_cancellation_safe():
    class Device:
        metadata = DeviceMetadata("d", "test")
        closed = 0

        async def close(self):
            self.closed += 1
            await asyncio.sleep(0)

    class TestDriver(Driver):
        async def connect(self):
            return True

    driver = TestDriver(DriverConfig("driver"))
    device = Device()
    driver._device = device
    driver._connected = True
    await driver.disconnect()
    await driver.disconnect()
    assert not driver.is_connected
    assert driver.device is None
    assert device.closed == 1


def test_auto_discover_reports_conflict_without_overwriting_existing_driver():
    class ExistingDriver(Driver):
        DRIVER_NAME = "duplicate"

    module_name = "test_discovery_conflict"
    module = types.ModuleType(module_name)

    class DiscoveredDriver(Driver):
        DRIVER_NAME = "duplicate"

    DiscoveredDriver.__module__ = module_name
    module.DiscoveredDriver = DiscoveredDriver
    sys.modules[module_name] = module
    try:
        registry = __import__("openmhs.core.driver", fromlist=["DriverRegistry"]).DriverRegistry()
        registry.register(ExistingDriver)
        diagnostics = registry.auto_discover(module_name)
        assert registry.get_driver("duplicate") is ExistingDriver
        assert diagnostics
        assert "duplicate" in diagnostics[0]
    finally:
        sys.modules.pop(module_name, None)
