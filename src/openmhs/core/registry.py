"""Device registry for MHS."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from openmhs.core.device import Device

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Entry:
    device: Device
    owned: bool


Listener = Callable[[], Awaitable[None] | None]


class DeviceRegistry:
    """An asynchronous device index with explicit ownership semantics."""

    def __init__(self) -> None:
        self._devices: dict[str, _Entry] = {}
        self._aliases: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._listeners: list[Listener] = []
        # Events are keyed by object identity so a reattached object cannot
        # become current while an earlier replacement is still closing it.
        self._cleanup_events: dict[tuple[str, int], asyncio.Event] = {}

    def add_change_listener(self, listener: Listener) -> Callable[[], None]:
        """Subscribe to changes; callbacks run after the registry lock is released."""
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

    def _resolve_alias(self, device_id: str) -> str:
        """Resolve an alias chain and reject corrupted cycles."""
        seen: set[str] = set()
        current = device_id
        while current in self._aliases:
            if current in seen:
                raise ValueError(f"Alias cycle detected at {current!r}")
            seen.add(current)
            current = self._aliases[current]
        return current

    @staticmethod
    def _remove_aliases_for(canonical_id: str, aliases: dict[str, str]) -> None:
        """Remove every alias that eventually resolves to a deleted canonical ID."""
        doomed = {canonical_id}
        changed = True
        while changed:
            changed = False
            for alias, target in aliases.items():
                if (alias in doomed or target in doomed) and alias not in doomed:
                    doomed.add(alias)
                    changed = True
        for alias in doomed:
            aliases.pop(alias, None)

    @staticmethod
    def _reject_canonical_alias_conflict(device_id: str, aliases: dict[str, str]) -> None:
        if device_id in aliases:
            raise ValueError(f"Canonical device ID conflicts with alias key: {device_id}")

    async def add_alias(self, alias: str, canonical_id: str) -> None:
        """Point a legacy/process ID at a canonical ID."""
        if not alias or not canonical_id:
            raise ValueError("alias and canonical_id must be non-empty")
        async with self._lock:
            target = self._resolve_alias(canonical_id)
            if target not in self._devices:
                raise KeyError(canonical_id)
            if alias == target:
                return
            if alias in self._devices:
                raise ValueError(f"Alias key conflicts with canonical device ID: {alias}")
            existing = self._aliases.get(alias)
            if existing is not None:
                existing_target = self._resolve_alias(existing)
                if existing_target != target:
                    raise ValueError(f"Alias {alias!r} already points to {existing_target!r}")
                return
            self._aliases[alias] = target

    async def _notify_change(self) -> None:
        for listener in tuple(self._listeners):
            try:
                result = listener()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                logger.debug("Registry listener cancelled itself")
            except Exception:
                logger.exception("Registry change listener failed")

    async def _notify_change_safely(self) -> None:
        """Deliver one committed change even if the caller is cancelled."""
        notification = asyncio.create_task(self._notify_change())
        try:
            await asyncio.shield(notification)
        except asyncio.CancelledError:
            while not notification.done():
                try:
                    await asyncio.shield(notification)
                except asyncio.CancelledError:
                    continue
            raise

    async def _close_entry(
        self, device_id: str, entry: _Entry, reserved_event: asyncio.Event | None = None
    ) -> None:
        """Close one owned entry once, without holding the registry lock."""
        key = (device_id, id(entry.device))
        if reserved_event is None:
            async with self._lock:
                event = self._cleanup_events.get(key)
                if event is None:
                    event = asyncio.Event()
                    self._cleanup_events[key] = event
                    owner = True
                else:
                    owner = False
        else:
            event = reserved_event
            owner = True
        if not owner:
            await event.wait()
            return
        try:
            close_task = asyncio.create_task(entry.device.close())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                while not close_task.done():
                    try:
                        await asyncio.shield(close_task)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        logger.exception("Registry cleanup failed while cancellation was draining")
                        break
                if not close_task.cancelled():
                    close_task.exception()
                raise
        finally:
            async with self._lock:
                self._cleanup_events.pop(key, None)
                event.set()

    async def register(self, device: Device, owned: bool = False) -> None:
        """Register or replace a device, closing only an owned old entry."""
        await self.replace(device, owned=owned)

    async def replace(self, device: Device, owned: bool = False) -> Device | None:
        """Atomically swap an entry, then release its owned predecessor."""
        device_id = device.metadata.device_id
        device_key = (device_id, id(device))
        while True:
            async with self._lock:
                self._reject_canonical_alias_conflict(device_id, self._aliases)
                pending_new = self._cleanup_events.get(device_key)
                previous = self._devices.get(device_id)
                previous_key = (
                    (device_id, id(previous.device))
                    if previous is not None and previous.owned and previous.device is not device
                    else None
                )
                pending_previous = (
                    self._cleanup_events.get(previous_key) if previous_key is not None else None
                )
                if pending_new is None and pending_previous is None:
                    reserved = None
                    if previous_key is not None:
                        reserved = asyncio.Event()
                        self._cleanup_events[previous_key] = reserved
                    if previous is not None and previous.device is not device:
                        self._remove_aliases_for(device_id, self._aliases)
                    self._devices[device_id] = _Entry(device, owned)
                    break
            pending_event = pending_new or pending_previous
            assert pending_event is not None
            await pending_event.wait()
        try:
            if previous_key is not None and reserved is not None:
                assert previous is not None
                await self._close_entry(device_id, previous, reserved)
        finally:
            await self._notify_change_safely()
        return previous.device if previous is not None else None

    async def register_if_absent(self, device: Device, owned: bool = False) -> bool:
        """Insert *device* atomically, without touching a rejected device."""
        device_id = device.metadata.device_id
        key = (device_id, id(device))
        while True:
            async with self._lock:
                self._reject_canonical_alias_conflict(device_id, self._aliases)
                pending = self._cleanup_events.get(key)
                if pending is None:
                    if device_id in self._devices:
                        return False
                    self._devices[device_id] = _Entry(device, owned)
                    break
            await pending.wait()
        await self._notify_change_safely()
        return True

    async def swap_if_same(
        self,
        device_id: str,
        expected: Device,
        replacement: Device | str,
        new_device: Device | None = None,
        *,
        owned: bool = False,
    ) -> bool:
        """Atomically replace ``expected`` across IDs if it is still current.

        The predecessor is detached while holding the lock; owned cleanup runs
        afterwards, so readers can never observe a half-swapped pair.
        """
        old_id = device_id
        if isinstance(replacement, str):
            if new_device is None:
                raise TypeError("new_device is required when replacement ID is supplied")
            new_id = replacement
            replacement_device = new_device
        else:
            if new_device is not None:
                raise TypeError("new_device is only valid with an explicit replacement ID")
            new_id = replacement.metadata.device_id
            replacement_device = replacement
        reserved: asyncio.Event | None = None
        async with self._lock:
            self._reject_canonical_alias_conflict(
                replacement_device.metadata.device_id, self._aliases
            )
            old_id = self._resolve_alias(old_id)
            old_entry = self._devices.get(old_id)
            if old_entry is None or old_entry.device is not expected:
                return False
            new_id = self._resolve_alias(new_id)
            collision = self._devices.get(new_id)
            if collision is not None and collision.device is not expected:
                return False
            if replacement_device.metadata.device_id != new_id:
                raise ValueError("replacement metadata ID does not match replacement ID")
            if old_entry.owned:
                reserved = asyncio.Event()
                self._cleanup_events[(old_id, id(expected))] = reserved
            del self._devices[old_id]
            self._remove_aliases_for(old_id, self._aliases)
            self._devices[new_id] = _Entry(replacement_device, owned)
        try:
            if reserved is not None:
                await self._close_entry(old_id, old_entry, reserved)
        finally:
            await self._notify_change_safely()
        return True

    async def unregister(self, device_id: str, close: bool | None = None) -> Device | None:
        """Remove a device and close it only when owned by default.

        ``close=True`` is an explicit, dangerous compatibility override;
        ``close=False`` always leaves the device open.
        """
        async with self._lock:
            canonical_id = self._resolve_alias(device_id)
            entry = self._devices.pop(canonical_id, None)
            reserved = None
            if entry is not None and (close is True or (close is None and entry.owned)):
                reserved = asyncio.Event()
                self._cleanup_events[(canonical_id, id(entry.device))] = reserved
            self._remove_aliases_for(canonical_id, self._aliases)
        if entry is None:
            return None
        try:
            if reserved is not None:
                await self._close_entry(canonical_id, entry, reserved)
        finally:
            await self._notify_change_safely()
        return entry.device

    async def detach(self, device_id: str) -> Device | None:
        """Remove an entry without ever closing its device."""
        async with self._lock:
            canonical_id = self._resolve_alias(device_id)
            entry = self._devices.pop(canonical_id, None)
            self._remove_aliases_for(canonical_id, self._aliases)
        if entry is not None:
            await self._notify_change_safely()
            return entry.device
        return None

    async def detach_if_same(self, device_id: str, expected: Device) -> bool:
        """Detach only when the indexed value is the expected object."""
        async with self._lock:
            canonical_id = self._resolve_alias(device_id)
            entry = self._devices.get(canonical_id)
            if entry is None or entry.device is not expected:
                return False
            del self._devices[canonical_id]
            self._remove_aliases_for(canonical_id, self._aliases)
        await self._notify_change_safely()
        return True

    async def snapshot(self) -> tuple[Device, ...]:
        """Return one stable, immutable view of current device references."""
        async with self._lock:
            return tuple(entry.device for entry in self._devices.values())

    def get_device(self, device_id: str) -> Device | None:
        """Get a device by canonical or legacy alias ID."""
        canonical_id = self._resolve_alias(device_id)
        entry = self._devices.get(canonical_id)
        return entry.device if entry is not None else None

    def list_devices(self) -> list[str]:
        """List all registered device IDs."""
        return list(self._devices.keys())

    def list_devices_by_type(self, device_type: str) -> list[Device]:
        """List devices of a specific type."""
        return [
            entry.device
            for entry in self._devices.values()
            if entry.device.metadata.device_type == device_type
        ]

    def list_devices_by_tag(self, tag: str) -> list[Device]:
        """List devices with a specific tag."""
        return [
            entry.device for entry in self._devices.values() if tag in entry.device.metadata.tags
        ]

    async def health_check_all(self) -> dict[str, dict[str, Any]]:
        """Perform health checks against a stable registry snapshot."""
        async with self._lock:
            devices = tuple((device_id, entry.device) for device_id, entry in self._devices.items())
        results: dict[str, dict[str, Any]] = {}
        for device_id, device in devices:
            try:
                results[device_id] = await device.health_check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                results[device_id] = {"error": str(exc), "healthy": False}
        return results

    async def reset_all(self) -> dict[str, bool]:
        """Reset devices from a stable registry snapshot."""
        async with self._lock:
            devices = tuple((device_id, entry.device) for device_id, entry in self._devices.items())
        results: dict[str, bool] = {}
        for device_id, device in devices:
            try:
                results[device_id] = await device.reset()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                results[device_id] = False
        return results

    async def _close_owned(
        self, entries: tuple[tuple[str, _Entry, asyncio.Event | None], ...]
    ) -> None:
        results = await asyncio.gather(
            *(
                self._close_entry(device_id, entry, reserved)
                for device_id, entry, reserved in entries
                if entry.owned
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.error("Owned device cleanup failed: %s", result)

    async def _drain_cleanup_task(self, cleanup: asyncio.Task[None]) -> None:
        """Drain cleanup despite repeated cancellation, preserving cancellation priority."""
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
            except BaseException:
                logger.exception("Registry cleanup failed while cancellation was draining")
                break
        if not cleanup.cancelled():
            cleanup.exception()

    async def close_all(self) -> None:
        """Atomically remove all entries and close only devices we own."""
        async with self._lock:
            entries = tuple(self._devices.items())
            self._devices.clear()
            self._aliases.clear()
            cleanup_entries = []
            for device_id, entry in entries:
                reserved = None
                if entry.owned:
                    key = (device_id, id(entry.device))
                    reserved = self._cleanup_events.get(key)
                    if reserved is None:
                        reserved = asyncio.Event()
                        self._cleanup_events[key] = reserved
                cleanup_entries.append((device_id, entry, reserved))
        if not entries:
            return
        cleanup = asyncio.create_task(self._close_owned(tuple(cleanup_entries)))
        cancellation: asyncio.CancelledError | None = None
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancellation = exc
            await self._drain_cleanup_task(cleanup)
        try:
            await self._notify_change_safely()
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        if cancellation is not None:
            raise cancellation

    def get_metadata_summary(self) -> list[dict[str, Any]]:
        """Get metadata summary for all devices."""
        summary = []
        for entry in self._devices.values():
            device = entry.device
            meta = device.metadata
            summary.append(
                {
                    "device_id": meta.device_id,
                    "device_type": meta.device_type,
                    "manufacturer": meta.manufacturer,
                    "model": meta.model,
                    "state": device.state.name,
                    "capabilities": [c.name for c in meta.capabilities],
                    "tags": meta.tags,
                    "location": meta.location,
                }
            )
        return summary
