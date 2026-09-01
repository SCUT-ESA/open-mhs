"""Aggregate VISA discovery for supported UNI-T instruments."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import weakref
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

MAX_DISCOVERY_INTERVAL_SECONDS = 3_600_000
_MISSING_SERIALS = frozenset(
    {"", "0", "na", "n/a", "none", "null", "unknown", "unspecified", "-", "--"}
)


def _validate_interval(value: float) -> float:
    valid = False
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        try:
            valid = (
                math.isfinite(float(value))
                and value > 0
                and value <= MAX_DISCOVERY_INTERVAL_SECONDS
            )
        except (OverflowError, TypeError, ValueError):
            valid = False
    if not valid:
        raise ValueError("Discovery interval must be a finite positive number")
    return float(value)


def normalize_identity_part(value: str | None) -> str:
    """Normalize one IDN field without retaining presentation differences."""
    return " ".join((value or "").strip().split()).casefold()


@dataclass(frozen=True)
class VisaIdentity:
    """Parsed VISA ``*IDN?`` identity used for stable, private IDs."""

    kind: str
    manufacturer: str
    model: str
    serial: str | None = None
    version: str | None = None
    resource: str | None = None
    unstable: bool = False

    def __post_init__(self) -> None:
        if self.serial is not None and normalize_identity_part(self.serial) in _MISSING_SERIALS:
            object.__setattr__(self, "serial", None)
            object.__setattr__(self, "unstable", True)

    @classmethod
    def parse(cls, identity: str, *, kind: str = "", resource: str | None = None) -> VisaIdentity:
        fields = [field.strip() for field in identity.split(",")]
        manufacturer = fields[0] if fields else ""
        model = fields[1] if len(fields) > 1 else ""
        serial_value = fields[2] if len(fields) > 2 else ""
        serial = (
            serial_value if normalize_identity_part(serial_value) not in _MISSING_SERIALS else None
        )
        version = fields[3] if len(fields) > 3 and fields[3].strip() else None
        return cls(kind, manufacturer, model, serial, version, resource, serial is None)

    from_idn = parse

    def canonical_id(self, prefix: str) -> str:
        parts = [self.kind, self.manufacturer, self.model]
        if self.serial is None:
            if not isinstance(self.resource, str) or not self.resource.strip():
                raise ValueError("resource is required when VISA identity has no serial")
            parts.extend(("", self.resource))
        else:
            parts.append(self.serial)
        payload = chr(0).join(normalize_identity_part(part) for part in parts)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}-{digest}"

    @property
    def serial_number_hash(self) -> str | None:
        if self.serial is None:
            return None
        return hashlib.sha256(normalize_identity_part(self.serial).encode("utf-8")).hexdigest()[:12]


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


def canonical_device_id(
    prefix: str,
    *,
    kind: str,
    manufacturer: str,
    model: str,
    serial: str | None = None,
    resource: str | None = None,
) -> str:
    """Return the canonical opaque ID for a VISA identity."""
    return VisaIdentity(
        kind, manufacturer, model, serial, resource=resource, unstable=serial is None
    ).canonical_id(prefix)


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
    visa_identity: VisaIdentity


def _identity_fields(identity: str) -> list[str]:
    return [field.strip() for field in identity.split(",")]


def _is_uni_t(manufacturer: str) -> bool:
    normalized = normalize_identity_part(manufacturer)
    # Older firmware identifies itself as "UNI-T Technologies".
    return normalized in {"uni-t", "uni-t technologies"}


def matches_upo6102n(identity: str) -> bool:
    fields = _identity_fields(identity)
    return (
        len(fields) >= 2
        and _is_uni_t(fields[0])
        and normalize_identity_part(fields[1]) == "upo6102n"
    )


def matches_utg2062x(identity: str) -> bool:
    fields = _identity_fields(identity)
    return (
        len(fields) >= 2
        and _is_uni_t(fields[0])
        and normalize_identity_part(fields[1]) == "utg2062x"
    )


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
        interval = _validate_interval(interval)
        self.registry = registry
        self._resource_manager_factory = resource_manager_factory or (
            pyvisa.ResourceManager if pyvisa is not None else None
        )
        self._specs = tuple(specs or default_visa_specs())
        self.interval = interval
        self._managed: dict[str, _ManagedEntry] = {}
        self._owned: dict[str, Driver] = {}
        self._disconnect_tasks: dict[
            int, tuple[weakref.ReferenceType[Driver], asyncio.Task[None]]
        ] = {}
        self._scan_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._state = DiscoveryState.NEW
        self._listeners: list[Callable[[], Awaitable[None] | None]] = []
        self._close_task: asyncio.Task[None] | None = None

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
            snapshot = await self._run_probe()
            errors = list(snapshot.errors)
            added: list[DiscoveredDevice] = []
            removed: list[DiscoveredDevice] = []
            unchanged: list[DiscoveredDevice] = []
            observations = {item.resource: item for item in snapshot.observations}
            if snapshot.enumeration_complete:
                listed = set(snapshot.listed_resources)
                consumed: set[str] = set()
                for resource, entry in list(self._managed.items()):
                    observation = observations.get(resource)
                    if resource in listed:
                        consumed.add(resource)
                        if (
                            observation is None
                            or observation.error is not None
                            or observation.identity is None
                            or self._same_identity(entry, observation.identity)
                        ):
                            unchanged.append(self._description(entry))
                        else:
                            replaced = await self._replace_entry(
                                entry, resource, observation.identity, errors, added, removed
                            )
                            if not replaced:
                                unchanged.append(self._description(entry))
                        continue

                    # A serial-bearing instrument may have moved to a new VISA
                    # resource. Rebind it before treating the old resource as gone.
                    rebound = next(
                        (
                            item
                            for item in snapshot.observations
                            if item.resource not in consumed
                            and item.supported
                            and item.identity is not None
                            and self._same_identity(entry, item.identity, serial_only=True)
                        ),
                        None,
                    )
                    added_before = len(added)
                    removed_before = len(removed)
                    if rebound is not None:
                        consumed.add(rebound.resource)
                        if await self._replace_entry(
                            entry, rebound.resource, rebound.identity or "", errors, added, removed
                        ):
                            # Rebinding the same physical identity is not a logical
                            # add/remove; discard only this replacement's events.
                            del added[added_before:]
                            del removed[removed_before:]
                            unchanged.append(self._description(self._managed[rebound.resource]))
                        else:
                            # A failed candidate must not turn a transient
                            # reconnect problem into destructive removal.
                            unchanged.append(self._description(entry))
                    else:
                        removed.append(self._description(entry))
                        try:
                            await self._disconnect_and_detach(entry, errors)
                        finally:
                            # _disconnect_and_detach drains physical cleanup and
                            # performs registry detach before propagating cancel.
                            self._managed.pop(resource, None)
                            self._owned.pop(resource, None)

                for resource in snapshot.listed_resources:
                    observation = observations[resource]
                    if (
                        resource in consumed
                        or resource in self._managed
                        or not observation.supported
                        or observation.identity is None
                    ):
                        continue
                    await self._connect_new(resource, observation.identity, errors, added)
            else:
                unchanged.extend(self._description(entry) for entry in self._managed.values())
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

    async def _close_impl(self) -> None:
        """Complete all close phases in a shielded task."""
        if self._state is DiscoveryState.CLOSED:
            return
        removed = False
        try:
            await self.stop()
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
                for _, task in tuple(self._disconnect_tasks.values()):
                    try:
                        await task
                    except BaseException:
                        logger.exception("Owned driver cleanup task failed")
                self._disconnect_tasks.clear()
        finally:
            self._state = DiscoveryState.CLOSED
        if removed:
            await self._notify_listeners()

    async def close(self) -> None:
        if self._state is DiscoveryState.CLOSED:
            return
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_impl())
        cleanup = self._close_task
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    logger.exception("Discovery close failed while cancellation was draining")
                    break
            if not cleanup.cancelled():
                cleanup.exception()
            raise

    async def _connect_driver(
        self, resource: str, identity: str, spec: VisaDeviceSpec, errors: list[DiscoveryIssue]
    ) -> _ManagedEntry | None:
        visa_identity = VisaIdentity.parse(identity, kind=spec.kind, resource=resource)
        device_id = visa_identity.canonical_id(spec.device_id_prefix)
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
            return None
        self._owned[resource] = driver
        device = None
        try:
            connected = await driver.connect()
            device = driver.device
            if not connected or device is None:
                raise RuntimeError("Driver failed to connect")
            device.metadata.connection_info.update(
                {
                    "identity_kind": visa_identity.kind,
                    "identity_manufacturer": visa_identity.manufacturer,
                    "identity_model": visa_identity.model,
                    "identity_serial_hash": visa_identity.serial_number_hash,
                    "identity_unstable": visa_identity.unstable,
                }
            )
            return _ManagedEntry(
                resource,
                identity,
                device_id,
                driver,
                device,
                spec.description_factory or DiscoveredDevice,
                visa_identity,
            )
        except asyncio.CancelledError:
            try:
                await self._disconnect_driver(driver)
            except BaseException:
                logger.exception("Failed to clean up cancelled driver %s", resource)
            finally:
                self._owned.pop(resource, None)
            raise
        except Exception as exc:  # noqa: BLE001
            try:
                await self._disconnect_driver(driver)
            except BaseException as cleanup:
                logger.exception("Failed to clean up failed driver %s", resource)
                errors.append(DiscoveryIssue(resource, f"Disconnect failed: {cleanup}"))
            finally:
                self._owned.pop(resource, None)
            errors.append(DiscoveryIssue(resource, str(exc)))
            return None

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
        entry = await self._connect_driver(resource, identity, spec, errors)
        if entry is None:
            return
        registered = False
        try:
            registered = await self.registry.register_if_absent(entry.device)
            if not registered:
                errors.append(
                    DiscoveryIssue(resource, f"Device ID already registered: {entry.device_id}")
                )
                await self._disconnect_driver(entry.driver)
                self._owned.pop(resource, None)
                return
            legacy_id = self._make_device_id(resource, spec.device_id_prefix)
            if legacy_id != entry.device_id:
                await self.registry.add_alias(legacy_id, entry.device_id)
            self._managed[resource] = entry
            self._owned.pop(resource, None)
            added.append(self._description(entry))
        except asyncio.CancelledError:
            # register_if_absent commits before listener delivery.  If
            # cancellation interrupted its await, inspect the registry rather
            # than closing a device that is already indexed.
            registered = registered or self.registry.get_device(entry.device_id) is entry.device
            if registered:
                self._managed[resource] = entry
                self._owned.pop(resource, None)
            else:
                try:
                    await self._disconnect_driver(entry.driver)
                except BaseException:
                    logger.exception("Failed to clean up cancelled new driver %s", resource)
                self._owned.pop(resource, None)
            raise
        except Exception as exc:  # noqa: BLE001
            registered = registered or self.registry.get_device(entry.device_id) is entry.device
            if registered:
                self._managed[resource] = entry
                self._owned.pop(resource, None)
                errors.append(DiscoveryIssue(resource, str(exc)))
                return
            try:
                await self.registry.detach_if_same(entry.device_id, entry.device)
            except Exception:
                logger.exception("Failed to detach failed new driver %s", resource)
            try:
                await self._disconnect_driver(entry.driver)
            except BaseException as cleanup:
                logger.exception("Failed to clean up new driver %s", resource)
                errors.append(DiscoveryIssue(resource, f"Disconnect failed: {cleanup}"))
            finally:
                self._owned.pop(resource, None)
            errors.append(DiscoveryIssue(resource, str(exc)))

    async def _replace_entry(
        self,
        old: _ManagedEntry,
        resource: str,
        identity: str,
        errors: list[DiscoveryIssue],
        added: list[DiscoveredDevice],
        removed: list[DiscoveredDevice],
    ) -> bool:
        spec = next((item for item in self._specs if item.identity_matcher(identity)), None)
        if spec is None:
            return False
        candidate = await self._connect_driver(resource, identity, spec, errors)
        if candidate is None:
            return False
        committed = False
        try:
            swapped = await self.registry.swap_if_same(old.device_id, old.device, candidate.device)
            if not swapped:
                raise RuntimeError("Existing device changed while replacement connected")
            committed = True
            # The registry swap is the commit point. Update discovery's own
            # index before awaiting old-driver cleanup so cancellation or a
            # cleanup failure cannot leave the two indexes disagreeing.
            self._managed.pop(old.resource, None)
            self._managed[resource] = candidate
            self._owned.pop(resource, None)
            legacy_id = self._make_device_id(resource, spec.device_id_prefix)
            if legacy_id != candidate.device_id:
                await self.registry.add_alias(legacy_id, candidate.device_id)
            removed.append(self._description(old))
            added.append(self._description(candidate))
            try:
                await self._disconnect_driver(old.driver)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(DiscoveryIssue(old.resource, f"Disconnect failed: {exc}"))
            return True
        except asyncio.CancelledError:
            # swap_if_same commits before listener delivery.  Reconcile the
            # private discovery index from the registry before propagating cancel.
            committed = (
                committed or self.registry.get_device(candidate.device_id) is candidate.device
            )
            if committed:
                self._managed.pop(old.resource, None)
                self._managed[resource] = candidate
                self._owned.pop(resource, None)
                try:
                    await self._disconnect_driver(old.driver)
                except BaseException:
                    logger.exception("Failed to clean up replaced driver %s", old.resource)
            else:
                try:
                    await self._disconnect_driver(candidate.driver)
                except BaseException:
                    logger.exception("Failed to clean up cancelled replacement %s", resource)
                self._owned.pop(resource, None)
            raise
        except Exception as exc:  # noqa: BLE001
            if committed:
                errors.append(DiscoveryIssue(resource, str(exc)))
                return True
            try:
                await self._disconnect_driver(candidate.driver)
            except BaseException as cleanup:
                logger.exception("Failed to clean up rejected replacement %s", resource)
                errors.append(DiscoveryIssue(resource, f"Disconnect failed: {cleanup}"))
            self._owned.pop(resource, None)
            errors.append(DiscoveryIssue(resource, str(exc)))
            return False

    async def _disconnect_and_detach(
        self, entry: _ManagedEntry, errors: list[DiscoveryIssue]
    ) -> bool:
        cancellation: asyncio.CancelledError | None = None
        try:
            await self._disconnect_driver(entry.driver)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception as exc:
            logger.exception("Failed to disconnect discovered driver %s", entry.resource)
            errors.append(DiscoveryIssue(entry.resource, f"Disconnect failed: {exc}"))
        detached = False
        while not detached:
            try:
                detached = await self.registry.detach_if_same(entry.device_id, entry.device)
                break
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
                # detach_if_same commits before listener delivery; do not retry a
                # committed mutation, but do retry if cancellation arrived first.
                detached = self.registry.get_device(entry.device_id) is not entry.device
                if not detached:
                    continue
            except Exception as exc:
                logger.exception("Failed to detach discovered device %s", entry.resource)
                errors.append(DiscoveryIssue(entry.resource, f"Detach failed: {exc}"))
                break
        if cancellation is not None:
            raise cancellation
        return detached

    async def _disconnect_driver(self, driver: Driver) -> None:
        """Disconnect once, draining cancellation before cleanup bookkeeping."""
        key = id(driver)
        record = self._disconnect_tasks.get(key)
        task = record[1] if record is not None and record[0]() is driver else None
        if record is not None and task is None:
            self._disconnect_tasks.pop(key, None)
        if task is None:
            driver_ref: weakref.ReferenceType[Driver]

            def forget(reference: weakref.ReferenceType[Driver]) -> None:
                current = self._disconnect_tasks.get(key)
                if current is not None and current[0] is reference:
                    self._disconnect_tasks.pop(key, None)

            driver_ref = weakref.ref(driver, forget)
            task = asyncio.create_task(driver.disconnect())
            self._disconnect_tasks[key] = (driver_ref, task)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    logger.exception("Driver cleanup failed while cancellation was draining")
                    break
            if not task.cancelled():
                task.exception()
            raise
        finally:
            if task.done() and (task.cancelled() or task.exception() is not None):
                current = self._disconnect_tasks.get(key)
                if current is not None and current[1] is task:
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
    def _same_identity(entry: _ManagedEntry, identity: str, *, serial_only: bool = False) -> bool:
        parsed = VisaIdentity.parse(identity, kind=entry.visa_identity.kind)
        old = entry.visa_identity
        if serial_only:
            return (
                old.serial is not None
                and parsed.serial is not None
                and normalize_identity_part(old.serial) == normalize_identity_part(parsed.serial)
                and normalize_identity_part(old.manufacturer)
                == normalize_identity_part(parsed.manufacturer)
                and normalize_identity_part(old.model) == normalize_identity_part(parsed.model)
            )
        return (
            normalize_identity_part(old.manufacturer)
            == normalize_identity_part(parsed.manufacturer)
            and normalize_identity_part(old.model) == normalize_identity_part(parsed.model)
            and normalize_identity_part(old.serial) == normalize_identity_part(parsed.serial)
        )

    @staticmethod
    def _description(entry: _ManagedEntry) -> DiscoveredDevice:
        return entry.description_factory(entry.resource, entry.identity, entry.device_id)

    async def _run_probe(self) -> ProbeSnapshot:
        """Run a scan in a thread and drain it before releasing ``_scan_lock``."""
        worker = asyncio.create_task(asyncio.to_thread(self._probe_resources))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    logger.exception("Probe worker failed while cancellation was draining")
                    break
            if not worker.cancelled():
                worker.exception()
            raise

    def _probe_resources(self) -> ProbeSnapshot:
        if self._resource_manager_factory is None:
            return ProbeSnapshot(False, (), (), (DiscoveryIssue(None, "PyVISA is not installed"),))
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
                identity = None
                error = None
                supported = False
                try:
                    # A scan is deliberately read-only: every listed resource,
                    # including an active one, gets exactly one IDN probe.
                    identity = probe_resource(manager, resource)
                    supported = any(spec.identity_matcher(identity) for spec in self._specs)
                except Exception as exc:
                    logger.exception("VISA probe failed for %s", resource)
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
    "VisaIdentity",
    "canonical_device_id",
    "default_visa_specs",
    "matches_upo6102n",
    "matches_utg2062x",
    "normalize_identity_part",
]
