"""Aggregate VISA discovery tests with fake VISA objects."""

import asyncio
import threading

import pytest

from openmhs.adapters.visa.discovery import VisaDeviceSpec, VisaDiscoveryManager, VisaIdentity
from openmhs.adapters.visa.session import probe_resource
from openmhs.core.device import DeviceMetadata, DeviceState
from openmhs.core.driver import Driver
from openmhs.core.registry import DeviceRegistry


class Instrument:
    def __init__(self, identity):
        self.identity = identity
        self.close_count = 0
        self.timeout = None
        self.query_calls = []

    def query(self, command):
        self.query_calls.append(command)
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
    expected_scope = VisaIdentity.parse(
        "UNI-T,UPO6102N,SN,1.0", kind="scope", resource="USB::SCOPE::INSTR"
    ).canonical_id("scope")
    expected_gen = VisaIdentity.parse(
        "UNI-T,UTG2062X,SN,1.0", kind="generator", resource="USB::GEN::INSTR"
    ).canonical_id("wavegen")
    assert {item.device_id for item in result.added} == {expected_scope, expected_gen}
    assert manager.registry.list_devices() == [expected_scope, expected_gen]
    assert (
        manager.registry.get_device(manager._make_device_id("USB::SCOPE::INSTR", "scope"))
        is not None
    )
    assert all(item.close_count == 1 for item in instruments.values())
    await manager.close()


def test_unstable_canonical_id_requires_resource():
    identity = VisaIdentity.parse("UNI-T,UPO6102N,,1.0", kind="scope")
    with pytest.raises(ValueError, match="resource"):
        identity.canonical_id("scope")
    with pytest.raises(ValueError, match="resource"):
        VisaIdentity.parse("UNI-T,UPO6102N,,1.0", kind="scope", resource=" ").canonical_id("scope")


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

    def resource_manager_factory():
        return Manager(instruments)

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
    manager = VisaDiscoveryManager(DeviceRegistry(), lambda: Manager(instruments), specs=specs())
    notifications = 0

    async def listener():
        nonlocal notifications
        notifications += 1

    manager.add_change_listener(listener)
    await manager.scan_once()
    await manager.close()
    await manager.close()

    assert notifications == 2


@pytest.mark.asyncio
async def test_active_resource_is_probed_once_with_idn_only_each_round():
    resource = "USB::SCOPE::INSTR"
    instrument = Instrument("UNI-T,UPO6102N,SN,1.0")
    manager = VisaDiscoveryManager(
        DeviceRegistry(), lambda: Manager({resource: instrument}), specs=specs()
    )

    await manager.scan_once()
    await manager.scan_once()

    assert instrument.query_calls == ["*IDN?", "*IDN?"]
    await manager.close()


@pytest.mark.asyncio
async def test_scan_cancellation_drains_probe_before_next_scan():
    started = threading.Event()
    release = threading.Event()

    class BlockingManager(Manager):
        def list_resources(self, pattern):
            started.set()
            release.wait(timeout=2)
            return ()

    manager = VisaDiscoveryManager(DeviceRegistry(), lambda: BlockingManager({}), specs=specs())
    scan = asyncio.create_task(manager.scan_once())
    await asyncio.to_thread(started.wait)
    scan.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await scan
    # The lock is available only after the worker has returned.
    result = await manager.scan_once()
    assert result.added == ()


@pytest.mark.asyncio
async def test_registry_commit_cancellation_reconciles_discovery_indexes():
    resource = "USB::SCOPE::INSTR"
    instrument = Instrument("UNI-T,UPO6102N,SN,1.0")
    registry = DeviceRegistry()
    manager = VisaDiscoveryManager(registry, lambda: Manager({resource: instrument}), specs=specs())
    listener_started = asyncio.Event()
    release_listener = asyncio.Event()

    async def listener():
        listener_started.set()
        await release_listener.wait()

    registry.add_change_listener(listener)
    scan = asyncio.create_task(manager.scan_once())
    await listener_started.wait()
    scan.cancel()
    release_listener.set()
    with pytest.raises(asyncio.CancelledError):
        await scan
    entry = manager._managed[resource]
    assert registry.get_device(entry.device_id) is entry.device
    assert resource not in manager._owned
    await manager.close()


@pytest.mark.asyncio
async def test_replacement_commit_cancellation_keeps_registry_and_index_aligned():
    resource = "USB::SCOPE::INSTR"
    instruments = [
        Instrument("UNI-T,UPO6102N,SN1,1.0"),
        Instrument("UNI-T,UPO6102N,SN2,1.0"),
    ]
    managers = [Manager({resource: instruments[0]}), Manager({resource: instruments[1]})]
    registry = DeviceRegistry()
    manager = VisaDiscoveryManager(registry, lambda: managers.pop(0), specs=specs())
    await manager.scan_once()
    listener_started = asyncio.Event()
    release_listener = asyncio.Event()
    notifications = 0

    async def listener():
        nonlocal notifications
        notifications += 1
        if notifications == 1:
            listener_started.set()
            await release_listener.wait()

    registry.add_change_listener(listener)
    scan = asyncio.create_task(manager.scan_once())
    await listener_started.wait()
    scan.cancel()
    release_listener.set()
    with pytest.raises(asyncio.CancelledError):
        await scan
    entry = manager._managed[resource]
    assert registry.get_device(entry.device_id) is entry.device
    assert len(manager._managed) == 1
    await manager.close()


@pytest.mark.asyncio
async def test_scan_removal_cancellation_detaches_after_driver_drain():
    resource = "USB::SCOPE::INSTR"
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingDriver(Driver):
        def __init__(self, config):
            super().__init__(config)
            self._device = Device(config.connection_params["device_id"], "oscilloscope")

        async def connect(self):
            return True

        async def disconnect(self):
            started.set()
            await release.wait()

    spec = VisaDeviceSpec(
        "scope",
        "oscilloscope",
        "oscilloscope",
        lambda value: "UPO6102N" in value,
        BlockingDriver,
        "scope",
    )
    managers = [
        Manager({resource: Instrument("UNI-T,UPO6102N,SN,1.0")}),
        Manager({}),
    ]
    registry = DeviceRegistry()
    manager = VisaDiscoveryManager(registry, lambda: managers.pop(0), specs=(spec,))
    await manager.scan_once()
    scanning = asyncio.create_task(manager.scan_once())
    await started.wait()
    scanning.cancel()
    scanning.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await scanning
    assert manager._managed == {}
    assert manager._owned == {}
    assert await registry.snapshot() == ()


@pytest.mark.asyncio
async def test_close_cancellation_completes_detach_and_closed_state():
    resource = "USB::SCOPE::INSTR"
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingDriver(Driver):
        def __init__(self, config):
            super().__init__(config)
            self._device = Device(config.connection_params["device_id"], "oscilloscope")

        async def connect(self):
            return True

        async def disconnect(self):
            started.set()
            await release.wait()

    spec = VisaDeviceSpec(
        "scope",
        "oscilloscope",
        "oscilloscope",
        lambda value: "UPO6102N" in value,
        BlockingDriver,
        "scope",
    )
    registry = DeviceRegistry()
    manager = VisaDiscoveryManager(
        registry, lambda: Manager({resource: Instrument("UNI-T,UPO6102N,SN,1.0")}), specs=(spec,)
    )
    await manager.scan_once()
    closing = asyncio.create_task(manager.close())
    await started.wait()
    closing.cancel()
    closing.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert manager.state.name == "CLOSED"
    assert manager._managed == {}
    assert manager._owned == {}
    assert await registry.snapshot() == ()


@pytest.mark.asyncio
async def test_disconnect_cancellation_drains_and_deduplicates_cleanup():
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingDriver:
        disconnect_calls = 0

        async def disconnect(self):
            type(self).disconnect_calls += 1
            started.set()
            await release.wait()

    driver = BlockingDriver()
    manager = VisaDiscoveryManager(DeviceRegistry(), specs=specs())
    disconnect = asyncio.create_task(manager._disconnect_driver(driver))
    await started.wait()
    disconnect.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await disconnect
    await manager._disconnect_driver(driver)
    assert driver.disconnect_calls == 1


@pytest.mark.parametrize("interval", [True, float("nan"), float("inf"), 0, -1, 10**100])
def test_discovery_interval_rejects_unsafe_values(interval):
    with pytest.raises(ValueError):
        VisaDiscoveryManager(DeviceRegistry(), specs=specs(), interval=interval)


@pytest.mark.parametrize("timeout", [True, float("nan"), float("inf"), 0, -1, 10**100, 3_600_001])
def test_probe_timeout_rejects_unsafe_values(timeout):
    with pytest.raises(ValueError):
        probe_resource(Manager({}), "resource", timeout_ms=timeout)


def test_placeholder_serial_is_unstable_and_uses_resource():
    identity = VisaIdentity.parse("UNI-T,UPO6102N,N/A,1.0", kind="scope", resource="USB::1")
    assert identity.serial is None
    assert identity.unstable
    assert identity.canonical_id("scope") != VisaIdentity.parse(
        "UNI-T,UPO6102N,N/A,1.0", kind="scope", resource="USB::2"
    ).canonical_id("scope")
    direct = VisaIdentity("scope", "UNI-T", "UPO6102N", "N/A")
    assert direct.serial is None
    with pytest.raises(ValueError, match="resource"):
        direct.canonical_id("scope")
