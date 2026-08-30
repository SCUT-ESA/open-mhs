"""VISA discovery for supported oscilloscopes."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

try:  # PyVISA is optional; importing core/MCP must not require it.
    import pyvisa
except ImportError:  # pragma: no cover - exercised in installations without pyvisa
    pyvisa = None  # type: ignore[assignment]

from openmhs.adapters.oscilloscope import OscilloscopeDriver
from openmhs.core.driver import Driver, DriverConfig
from openmhs.core.registry import DeviceRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveryIssue:
    """A non-fatal issue encountered while probing or reconciling."""

    resource: str | None
    message: str


@dataclass(frozen=True)
class ResourceObservation:
    """The result of probing one resource from a completed enumeration."""

    resource: str
    identity: str | None
    supported: bool
    error: str | None


@dataclass(frozen=True)
class ProbeSnapshot:
    """Immutable result of one resource-manager probe."""

    enumeration_complete: bool
    listed_resources: tuple[str, ...]
    observations: tuple[ResourceObservation, ...]
    errors: tuple[DiscoveryIssue, ...]


@dataclass(frozen=True)
class DiscoveredOscilloscope:
    """An oscilloscope known to the discovery manager."""

    resource: str
    identity: str
    device_id: str


@dataclass(frozen=True)
class ScanResult:
    """Ownership transitions and diagnostics from one discovery scan."""

    added: tuple[DiscoveredOscilloscope, ...]
    removed: tuple[DiscoveredOscilloscope, ...]
    unchanged: tuple[DiscoveredOscilloscope, ...]
    errors: tuple[DiscoveryIssue, ...]


class DiscoveryState(Enum):
    """Lifecycle state for a discovery manager."""

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


class OscilloscopeDiscoveryManager:
    """Poll VISA and own drivers for supported oscilloscopes."""

    def __init__(
        self,
        registry: DeviceRegistry,
        resource_manager_factory: Callable[[], Any] | None = None,
        driver_factory: Callable[[DriverConfig], Driver] | None = None,
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
        if resource_manager_factory is not None:
            self._resource_manager_factory = resource_manager_factory
        elif pyvisa is not None:
            self._resource_manager_factory = pyvisa.ResourceManager
        else:
            self._resource_manager_factory = None
        self._driver_factory = driver_factory or OscilloscopeDriver
        self.interval = interval
        self._managed: dict[str, _ManagedEntry] = {}
        # Includes provisional drivers while connect() is in flight.  This is
        # what lets close() reclaim ownership after a cancelled scan.
        self._owned: dict[str, Driver] = {}
        self._disconnect_tasks: dict[int, asyncio.Task[None]] = {}
        self._scan_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._state = DiscoveryState.NEW
        self._listeners: list[Callable[[], Awaitable[None] | None]] = []

    @property
    def state(self) -> DiscoveryState:
        """Return the manager lifecycle state."""
        return self._state

    def add_change_listener(
        self,
        listener: Callable[[], Awaitable[None] | None],
    ) -> Callable[[], None]:
        """Register a catalog-change listener and return an idempotent remover."""
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
        """Probe resources and reconcile manager ownership."""
        if self._state is DiscoveryState.CLOSED:
            raise RuntimeError("Discovery manager is closed")

        async with self._scan_lock:
            snapshot = await asyncio.to_thread(
                self._probe_resources, set(self._managed.keys())
            )
            errors = list(snapshot.errors)
            added: list[DiscoveredOscilloscope] = []
            removed: list[DiscoveredOscilloscope] = []
            unchanged: list[DiscoveredOscilloscope] = []
            observations = {item.resource: item for item in snapshot.observations}

            # A complete list is the only evidence that an absent resource was
            # physically removed. Query/open failures are deliberately ignored
            # for removal purposes.
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
                unchanged.extend(
                    self._description(entry) for entry in self._managed.values()
                )

            for resource in snapshot.listed_resources:
                observation = observations[resource]
                if resource in self._managed:
                    continue
                if not observation.supported or observation.identity is None:
                    continue
                await self._connect_new(
                    resource,
                    observation.identity,
                    errors,
                    added,
                )

            result = ScanResult(
                tuple(added),
                tuple(removed),
                tuple(unchanged),
                tuple(errors),
            )

        if result.added or result.removed:
            await self._notify_listeners()
        return result

    def start(self) -> None:
        """Schedule exactly one polling loop."""
        if self._state is DiscoveryState.CLOSED:
            raise RuntimeError("Discovery manager is closed")
        if self._state is DiscoveryState.RUNNING:
            return
        self._state = DiscoveryState.RUNNING
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """Run the immediate-and-periodic polling loop."""
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
                    # A later poll can recover from transient VISA failures.
                    logger.exception("Discovery polling scan failed")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.interval
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            if self._state is DiscoveryState.RUNNING:
                self._state = DiscoveryState.STOPPED
            if self._poll_task is current:
                self._poll_task = None

    async def stop(self) -> None:
        """Stop polling while retaining currently managed devices."""
        task = self._poll_task
        self._stop_event.set()
        if task is not None and task is not asyncio.current_task():
            # Do not cancel a scan: it must reach its reconciliation safe point.
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._poll_task = None
        if self._state in (DiscoveryState.NEW, DiscoveryState.RUNNING):
            self._state = DiscoveryState.STOPPED

    async def close(self) -> None:
        """Stop polling and release every driver owned by this manager."""
        if self._state is DiscoveryState.CLOSED:
            return
        await self.stop()
        async with self._scan_lock:
            entries = list(self._managed.values())
            for entry in entries:
                await self._disconnect_and_detach(entry, [])
                self._managed.pop(entry.resource, None)
                self._owned.pop(entry.resource, None)

            # A cancelled connect may not have produced a managed entry yet.
            for resource, driver in list(self._owned.items()):
                try:
                    await self._disconnect_driver(driver)
                except Exception:
                    logger.exception("Failed to disconnect provisional driver %s", resource)
                finally:
                    self._owned.pop(resource, None)

            # A cancellation can leave a shielded disconnect task running.
            # Drain it before declaring the manager closed.
            for task in tuple(self._disconnect_tasks.values()):
                try:
                    await task
                except BaseException:
                    logger.exception("Owned driver cleanup task failed")
            self._disconnect_tasks.clear()
            self._state = DiscoveryState.CLOSED

    async def _connect_new(
        self,
        resource: str,
        identity: str,
        errors: list[DiscoveryIssue],
        added: list[DiscoveredOscilloscope],
    ) -> None:
        device_id = self._make_device_id(resource)
        config = DriverConfig(
            driver_name="oscilloscope",
            connection_params={"device_id": device_id, "visa_resource": resource},
        )
        try:
            driver = self._driver_factory(config)
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
            entry = _ManagedEntry(resource, identity, device_id, driver, device)
            self._managed[resource] = entry
            added.append(DiscoveredOscilloscope(resource, identity, device_id))
        except asyncio.CancelledError:
            # Keep _owned populated until disconnect completes, including when
            # cancellation interrupts this await. close() can then reclaim it.
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
        self,
        entry: _ManagedEntry,
        errors: list[DiscoveryIssue],
    ) -> None:
        try:
            await self._disconnect_driver(entry.driver)
        except Exception as exc:  # noqa: BLE001
            errors.append(DiscoveryIssue(entry.resource, f"Disconnect failed: {exc}"))
        finally:
            try:
                await self.registry.detach_if_same(entry.device_id, entry.device)
            except Exception as exc:  # noqa: BLE001
                errors.append(DiscoveryIssue(entry.resource, f"Detach failed: {exc}"))

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
    def _description(entry: _ManagedEntry) -> DiscoveredOscilloscope:
        return DiscoveredOscilloscope(entry.resource, entry.identity, entry.device_id)

    def _probe_resources(
        self, managed_resources: set[str] | None = None
    ) -> ProbeSnapshot:
        """Perform all blocking VISA calls in the worker thread."""
        if self._resource_manager_factory is None:
            issue = DiscoveryIssue(None, "PyVISA is not installed")
            return ProbeSnapshot(False, (), (), (issue,))

        active_resources = managed_resources or set()
        resource_manager = None
        errors: list[DiscoveryIssue] = []
        observations: list[ResourceObservation] = []
        try:
            resource_manager = self._resource_manager_factory()
            resources = tuple(resource_manager.list_resources("?*::INSTR"))
        except Exception as exc:  # noqa: BLE001
            errors.append(DiscoveryIssue(None, str(exc)))
            if resource_manager is not None:
                try:
                    resource_manager.close()
                except Exception as close_exc:  # noqa: BLE001
                    errors.append(DiscoveryIssue(None, str(close_exc)))
            return ProbeSnapshot(False, (), (), tuple(errors))

        try:
            for resource in resources:
                if resource in active_resources:
                    observations.append(
                        ResourceObservation(resource, None, True, None)
                    )
                    continue
                instrument = None
                identity: str | None = None
                supported = False
                error: str | None = None
                try:
                    instrument = resource_manager.open_resource(resource)
                    instrument.timeout = 2000
                    identity = instrument.query("*IDN?").strip()
                    supported = self._is_supported(identity)
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                finally:
                    if instrument is not None:
                        try:
                            instrument.close()
                        except Exception as exc:  # noqa: BLE001
                            error = str(exc) if error is None else f"{error}; {exc}"
                observations.append(
                    ResourceObservation(resource, identity, supported, error)
                )
                if error is not None:
                    errors.append(DiscoveryIssue(resource, error))
        finally:
            try:
                resource_manager.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(DiscoveryIssue(None, str(exc)))

        return ProbeSnapshot(True, resources, tuple(observations), tuple(errors))

    @staticmethod
    def _is_supported(identity: str) -> bool:
        fields = [field.strip().upper() for field in identity.split(",")]
        return len(fields) >= 2 and "UNI-T" in fields[0] and fields[1] == "UPO6102N"

    @staticmethod
    def _make_device_id(resource: str) -> str:
        digest = hashlib.sha256(resource.encode("utf-8")).hexdigest()[:12]
        return f"scope-{digest}"


__all__ = [
    "DiscoveredOscilloscope",
    "DiscoveryIssue",
    "DiscoveryState",
    "OscilloscopeDiscoveryManager",
    "ProbeSnapshot",
    "ResourceObservation",
    "ScanResult",
]
