"""Aggregate VISA discovery tests with fake VISA objects."""

import hashlib

import pytest

from openmhs.adapters.visa.discovery import VisaDeviceSpec, VisaDiscoveryManager
from openmhs.core.device import DeviceMetadata, DeviceState
from openmhs.core.driver import Driver
from openmhs.core.registry import DeviceRegistry


class Instrument:
    def __init__(self, identity):
        self.identity = identity
        self.close_count = 0
        self.timeout = None

    def query(self, command):
        assert command == "*IDN?"
        return self.identity

    def close(self):
        self.close_count += 1


class Manager:
    def __init__(self, instruments):
        self.instruments = instruments
        self.closed = 0

    def list_resources(self, pattern):
        return tuple(self.instruments)

    def open_resource(self, resource):
        return self.instruments[resource]

    def close(self):
        self.closed += 1


class Device:
    def __init__(self, device_id, device_type):
        self.metadata = DeviceMetadata(device_id, device_type)
        self.state = DeviceState.ONLINE
        self.close_count = 0

    async def close(self):
        self.close_count += 1
        self.state = DeviceState.OFFLINE


class Driver(Driver):
    def __init__(self, config):
        super().__init__(config)
        self._device = Device(config.connection_params["device_id"], config.driver_name)
        self.disconnect_count = 0

    async def connect(self):
        self._connected = True
        return True

    async def disconnect(self):
        self.disconnect_count += 1
        await self._device.close()
        self._device = None


def specs():
    return (
        VisaDeviceSpec(
            "scope", "oscilloscope", "oscilloscope", lambda x: "UPO6102N" in x, Driver, "scope"
        ),
        VisaDeviceSpec(
            "generator",
            "waveform_generator",
            "waveform_generator",
            lambda x: "UTG2062X" in x,
            Driver,
            "wavegen",
        ),
    )


@pytest.mark.asyncio
async def test_one_scan_finds_both_types_and_ids():
    instruments = {
        "USB::SCOPE::INSTR": Instrument("UNI-T,UPO6102N,SN,1.0"),
        "USB::GEN::INSTR": Instrument("UNI-T,UTG2062X,SN,1.0"),
        "USB::OTHER::INSTR": Instrument("ACME,OTHER,SN,1.0"),
    }
    manager = VisaDiscoveryManager(DeviceRegistry(), lambda: Manager(instruments), specs=specs())
    result = await manager.scan_once()
    expected_scope = hashlib.sha256(b"USB::SCOPE::INSTR").hexdigest()[:12]
    expected_gen = hashlib.sha256(b"USB::GEN::INSTR").hexdigest()[:12]
    assert {item.device_id for item in result.added} == {
        f"scope-{expected_scope}",
        f"wavegen-{expected_gen}",
    }
    assert manager.registry.list_devices() == [f"scope-{expected_scope}", f"wavegen-{expected_gen}"]
    assert all(item.close_count == 1 for item in instruments.values())
    await manager.close()


@pytest.mark.asyncio
async def test_probe_failure_does_not_remove_existing_device():
    resource = "USB::SCOPE::INSTR"
    instrument = Instrument("UNI-T,UPO6102N,SN,1.0")
    managers = [Manager({resource: instrument}), Manager({resource: instrument})]
    registry = DeviceRegistry()
    manager = VisaDiscoveryManager(registry, lambda: managers.pop(0), specs=specs())
    await manager.scan_once()
    # A failed enumeration is not complete and therefore cannot imply removal.
    manager._resource_manager_factory = lambda: (_ for _ in ()).throw(RuntimeError("busy"))
    result = await manager.scan_once()
    assert result.removed == ()
    assert registry.list_devices()
    await manager.close()


@pytest.mark.asyncio
async def test_driver_config_receives_injected_resource_manager_factory():
    resource = "USB::GEN::INSTR"
    instruments = {resource: Instrument("UNI-T,UTG2062X,SN,1.0")}
    resource_manager_factory = lambda: Manager(instruments)
    seen_factories = []

    def driver_factory(config):
        seen_factories.append(config.connection_params["resource_manager_factory"])
        return Driver(config)

    generator_spec = VisaDeviceSpec(
        "generator",
        "waveform_generator",
        "waveform_generator",
        lambda identity: "UTG2062X" in identity,
        driver_factory,
        "wavegen",
    )
    manager = VisaDiscoveryManager(
        DeviceRegistry(), resource_manager_factory, specs=(generator_spec,)
    )

    result = await manager.scan_once()

    assert len(result.added) == 1
    assert seen_factories == [resource_manager_factory]
    await manager.close()


@pytest.mark.asyncio
async def test_close_notifies_listeners_when_managed_devices_are_detached():
    resource = "USB::SCOPE::INSTR"
    instruments = {resource: Instrument("UNI-T,UPO6102N,SN,1.0")}
    manager = VisaDiscoveryManager(
        DeviceRegistry(), lambda: Manager(instruments), specs=specs()
    )
    notifications = 0

    async def listener():
        nonlocal notifications
        notifications += 1

    manager.add_change_listener(listener)
    await manager.scan_once()
    await manager.close()
    await manager.close()

    assert notifications == 2
