"""Aggregate VISA discovery for supported UNI-T instruments."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

try:
    import pyvisa
except ImportError:  # pragma: no cover
    pyvisa = None  # type: ignore[assignment]

from openmhs.adapters.oscilloscope import OscilloscopeDriver
from openmhs.adapters.visa.session import probe_resource
from openmhs.adapters.waveform_generator import WaveformGeneratorDriver
from openmhs.core.driver import Driver, DriverConfig
from openmhs.core.registry import DeviceRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveryIssue:
    resource: str | None
    message: str


@dataclass(frozen=True)
class ResourceObservation:
    resource: str
    identity: str | None
    supported: bool
    error: str | None


@dataclass(frozen=True)
class ProbeSnapshot:
    enumeration_complete: bool
    listed_resources: tuple[str, ...]
    observations: tuple[ResourceObservation, ...]
    errors: tuple[DiscoveryIssue, ...]


@dataclass(frozen=True)
class DiscoveredDevice:
    resource: str
    identity: str
    device_id: str


@dataclass(frozen=True)
class DiscoveredOscilloscope(DiscoveredDevice):
    pass


@dataclass(frozen=True)
class DiscoveredWaveformGenerator(DiscoveredDevice):
    pass


@dataclass(frozen=True)
class ScanResult:
    added: tuple[DiscoveredDevice, ...]
    removed: tuple[DiscoveredDevice, ...]
    unchanged: tuple[DiscoveredDevice, ...]
    errors: tuple[DiscoveryIssue, ...]


@dataclass(frozen=True)
class VisaDeviceSpec:
    kind: str
    device_type: str
    driver_name: str
    identity_matcher: Callable[[str], bool]
    driver_factory: Callable[[DriverConfig], Driver]
    device_id_prefix: str
    description_factory: Callable[[str, str, str], DiscoveredDevice] | None = None


class DiscoveryState(Enum):
    NEW = auto()
    RUNNING = auto()
    STOPPED = auto()
    CLOSED = auto()


@dataclass
class _ManagedEntry:
    resource: str
    identity: str
    device_id: str
    driver: Driver
    device: Any
    description_factory: Callable[[str, str, str], DiscoveredDevice]


def _identity_fields(identity: str) -> list[str]:
    return [field.strip() for field in identity.split(",")]


def matches_upo6102n(identity: str) -> bool:
    fields = _identity_fields(identity)
    return len(fields) >= 2 and "UNI-T" in fields[0].upper() and fields[1].upper() == "UPO6102N"


def matches_utg2062x(identity: str) -> bool:
    fields = _identity_fields(identity)
    return len(fields) >= 2 and "UNI-T" in fields[0].upper() and fields[1].upper() == "UTG2062X"


def default_visa_specs() -> tuple[VisaDeviceSpec, ...]:
    """Return built-in device specifications (drivers are imported eagerly)."""
    return (
        VisaDeviceSpec(
            "oscilloscope",
            "oscilloscope",
            "oscilloscope",
            matches_upo6102n,
            OscilloscopeDriver,
            "scope",
            DiscoveredOscilloscope,
        ),
        VisaDeviceSpec(
            "waveform_generator",
            "waveform_generator",
            "waveform_generator",
            matches_utg2062x,
            WaveformGeneratorDriver,
            "wavegen",
            DiscoveredWaveformGenerator,
        ),
    )


class VisaDiscoveryManager:
    """Poll VISA once per interval and own connected discovery drivers."""

    def __init__(
        self,
        registry: DeviceRegistry,
        resource_manager_factory: Callable[[], Any] | None = None,
        specs: Sequence[VisaDeviceSpec] | None = None,
        interval: float = 5.0,
        discovery_interval: float | None = None,
        poll_interval: float | None = None,
    ) -> None:
        if discovery_interval is not None:
            interval = discovery_interval
        if poll_interval is not None:
            interval = poll_interval
        if interval <= 0:
            raise ValueError("Discovery interval must be positive")
        self.registry = registry
        self._resource_manager_factory = resource_manager_factory or (
            pyvisa.ResourceManager if pyvisa is not None else None
        )
        self._specs = tuple(specs or default_visa_specs())
        self.interval = interval
        self._managed: dict[str, _ManagedEntry] = {}
        self._owned: dict[str, Driver] = {}
        self._disconnect_tasks: dict[int, asyncio.Task[None]] = {}
        self._scan_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._state = DiscoveryState.NEW
        self._listeners: list[Callable[[], Awaitable[None] | None]] = []

    @property
    def state(self) -> DiscoveryState:
        return self._state

    def add_change_listener(
        self, listener: Callable[[], Awaitable[None] | None]
    ) -> Callable[[], None]:
        self._listeners.append(listener)
        removed = False

        def remove() -> None:
            nonlocal removed
            if removed:
                return
            removed = True
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return remove

    async def scan_once(self) -> ScanResult:
        if self._state is DiscoveryState.CLOSED:
            raise RuntimeError("Discovery manager is closed")
        async with self._scan_lock:
            snapshot = await asyncio.to_thread(self._probe_resources, set(self._managed))
            errors = list(snapshot.errors)
            added: list[DiscoveredDevice] = []
            removed: list[DiscoveredDevice] = []
            unchanged: list[DiscoveredDevice] = []
            observations = {item.resource: item for item in snapshot.observations}
            if snapshot.enumeration_complete:
                listed = set(snapshot.listed_resources)
                for resource, entry in list(self._managed.items()):
                    if resource not in listed:
                        removed.append(self._description(entry))
                        await self._disconnect_and_detach(entry, errors)
                        self._managed.pop(resource, None)
                        self._owned.pop(resource, None)
                    else:
                        unchanged.append(self._description(entry))
            else:
                unchanged.extend(self._description(entry) for entry in self._managed.values())
            for resource in snapshot.listed_resources:
                observation = observations[resource]
                if (
                    resource in self._managed
                    or not observation.supported
                    or observation.identity is None
                ):
                    continue
                await self._connect_new(resource, observation.identity, errors, added)
            result = ScanResult(tuple(added), tuple(removed), tuple(unchanged), tuple(errors))
        if result.added or result.removed:
            await self._notify_listeners()
        return result

    def start(self) -> None:
        if self._state is DiscoveryState.CLOSED:
            raise RuntimeError("Discovery manager is closed")
        if self._state is DiscoveryState.RUNNING:
            return
        self._state = DiscoveryState.RUNNING
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self.run())

    async def run(self) -> None:
        current = asyncio.current_task()
        if self._poll_task is not None and self._poll_task is not current:
            raise RuntimeError("Discovery polling is already running")
        if self._state is DiscoveryState.CLOSED:
            raise RuntimeError("Discovery manager is closed")
        if self._poll_task is None:
            self._poll_task = current
        if self._state is not DiscoveryState.RUNNING:
            self._state = DiscoveryState.RUNNING
            self._stop_event.clear()
        try:
            while not self._stop_event.is_set():
                try:
                    await self.scan_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Discovery polling scan failed")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self._state is DiscoveryState.RUNNING:
                self._state = DiscoveryState.STOPPED
            if self._poll_task is current:
                self._poll_task = None

    async def stop(self) -> None:
        task = self._poll_task
        self._stop_event.set()
        if task is not None and task is not asyncio.current_task():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._poll_task = None
        if self._state in (DiscoveryState.NEW, DiscoveryState.RUNNING):
            self._state = DiscoveryState.STOPPED

    async def close(self) -> None:
        if self._state is DiscoveryState.CLOSED:
            return
        await self.stop()
        removed = False
        async with self._scan_lock:
            for entry in list(self._managed.values()):
                removed = (await self._disconnect_and_detach(entry, [])) or removed
                self._managed.pop(entry.resource, None)
                self._owned.pop(entry.resource, None)
            for resource, driver in list(self._owned.items()):
                try:
                    await self._disconnect_driver(driver)
                except Exception:
                    logger.exception("Failed to disconnect provisional driver %s", resource)
                finally:
                    self._owned.pop(resource, None)
            for task in tuple(self._disconnect_tasks.values()):
                try:
                    await task
                except BaseException:
                    logger.exception("Owned driver cleanup task failed")
            self._disconnect_tasks.clear()
            self._state = DiscoveryState.CLOSED
        if removed:
            await self._notify_listeners()

    async def _connect_new(
        self,
        resource: str,
        identity: str,
        errors: list[DiscoveryIssue],
        added: list[DiscoveredDevice],
    ) -> None:
        spec = next((item for item in self._specs if item.identity_matcher(identity)), None)
        if spec is None:
            return
        device_id = self._make_device_id(resource, spec.device_id_prefix)
        config = DriverConfig(
            driver_name=spec.driver_name,
            connection_params={
                "device_id": device_id,
                "visa_resource": resource,
                "resource_manager_factory": self._resource_manager_factory,
            },
        )
        try:
            driver = spec.driver_factory(config)
        except Exception as exc:  # noqa: BLE001
            errors.append(DiscoveryIssue(resource, str(exc)))
            return
        self._owned[resource] = driver
        registered = False
        device = None
        try:
            connected = await driver.connect()
            device = driver.device
            if not connected or device is None:
                raise RuntimeError("Driver failed to connect")
            if not await self.registry.register_if_absent(device):
                raise RuntimeError(f"Device ID already registered: {device_id}")
            registered = True
            entry = _ManagedEntry(
                resource,
                identity,
                device_id,
                driver,
                device,
                spec.description_factory or DiscoveredDevice,
            )
            self._managed[resource] = entry
            added.append(self._description(entry))
        except asyncio.CancelledError:
            try:
                await self._disconnect_driver(driver)
                if registered and device is not None:
                    await self.registry.detach_if_same(device_id, device)
            finally:
                self._owned.pop(resource, None)
            raise
        except Exception as exc:  # noqa: BLE001
            try:
                await self._disconnect_driver(driver)
                if registered and device is not None:
                    await self.registry.detach_if_same(device_id, device)
            finally:
                self._owned.pop(resource, None)
            errors.append(DiscoveryIssue(resource, str(exc)))

    async def _disconnect_and_detach(
        self, entry: _ManagedEntry, errors: list[DiscoveryIssue]
    ) -> bool:
        try:
            await self._disconnect_driver(entry.driver)
        except Exception as exc:  # noqa: BLE001
            errors.append(DiscoveryIssue(entry.resource, f"Disconnect failed: {exc}"))
        try:
            return await self.registry.detach_if_same(entry.device_id, entry.device)
        except Exception as exc:  # noqa: BLE001
            errors.append(DiscoveryIssue(entry.resource, f"Detach failed: {exc}"))
            return False

    async def _disconnect_driver(self, driver: Driver) -> None:
        key = id(driver)
        task = self._disconnect_tasks.get(key)
        if task is None:
            task = asyncio.create_task(driver.disconnect())
            self._disconnect_tasks[key] = task
        try:
            await asyncio.shield(task)
        finally:
            if task.done():
                self._disconnect_tasks.pop(key, None)

    async def _notify_listeners(self) -> None:
        for listener in tuple(self._listeners):
            try:
                result = listener()
                if result is not None:
                    await result
            except Exception:
                logger.exception("Discovery change listener failed")

    @staticmethod
    def _description(entry: _ManagedEntry) -> DiscoveredDevice:
        return entry.description_factory(entry.resource, entry.identity, entry.device_id)

    def _probe_resources(self, managed_resources: set[str] | None = None) -> ProbeSnapshot:
        if self._resource_manager_factory is None:
            return ProbeSnapshot(False, (), (), (DiscoveryIssue(None, "PyVISA is not installed"),))
        active = managed_resources or set()
        manager = None
        errors: list[DiscoveryIssue] = []
        observations: list[ResourceObservation] = []
        try:
            manager = self._resource_manager_factory()
            resources = tuple(manager.list_resources("?*::INSTR"))
        except Exception as exc:  # noqa: BLE001
            errors.append(DiscoveryIssue(None, str(exc)))
            if manager is not None:
                try:
                    manager.close()
                except Exception as close_exc:  # noqa: BLE001
                    errors.append(DiscoveryIssue(None, str(close_exc)))
            return ProbeSnapshot(False, (), (), tuple(errors))
        try:
            for resource in resources:
                if resource in active:
                    observations.append(ResourceObservation(resource, None, True, None))
                    continue
                identity = None
                error = None
                supported = False
                try:
                    identity = probe_resource(manager, resource)
                    supported = any(spec.identity_matcher(identity) for spec in self._specs)
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                observations.append(ResourceObservation(resource, identity, supported, error))
                if error is not None:
                    errors.append(DiscoveryIssue(resource, error))
        finally:
            try:
                manager.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(DiscoveryIssue(None, str(exc)))
        return ProbeSnapshot(True, resources, tuple(observations), tuple(errors))

    @staticmethod
    def _is_supported(identity: str) -> bool:
        """Retain the legacy scope matcher for external callers."""
        return matches_upo6102n(identity)

    @staticmethod
    def _make_device_id(resource: str, prefix: str = "scope") -> str:
        digest = hashlib.sha256(resource.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}-{digest}"


__all__ = [
    "DiscoveredDevice",
    "DiscoveredOscilloscope",
    "DiscoveredWaveformGenerator",
    "DiscoveryIssue",
    "DiscoveryState",
    "ProbeSnapshot",
    "ResourceObservation",
    "ScanResult",
    "VisaDeviceSpec",
    "VisaDiscoveryManager",
    "default_visa_specs",
    "matches_upo6102n",
    "matches_utg2062x",
]
