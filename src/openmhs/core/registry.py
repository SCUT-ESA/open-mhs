"""Device registry for MHS."""

from __future__ import annotations

import asyncio
from typing import Any

from openmhs.core.device import Device


class DeviceRegistry:
    """An index of devices; it does not own discovery-created hardware."""

    def __init__(self) -> None:
        self._devices: dict[str, Device] = {}
        self._lock = asyncio.Lock()

    async def register(self, device: Device) -> None:
        """Register a device, replacing an existing entry if necessary."""
        async with self._lock:
            self._devices[device.metadata.device_id] = device

    async def register_if_absent(self, device: Device) -> bool:
        """Insert *device* atomically, without touching a rejected device."""
        async with self._lock:
            device_id = device.metadata.device_id
            if device_id in self._devices:
                return False
            self._devices[device_id] = device
            return True

    async def unregister(self, device_id: str) -> Device | None:
        """Unregister a device and close it (legacy behavior)."""
        async with self._lock:
            device = self._devices.pop(device_id, None)
            if device:
                await device.close()
            return device

    async def detach_if_same(self, device_id: str, expected: Device) -> bool:
        """Detach only when the indexed value is the expected object.

        Detaching deliberately does not close the device.  Callers that own the
        device must release it before detaching it from this index.
        """
        async with self._lock:
            if self._devices.get(device_id) is not expected:
                return False
            del self._devices[device_id]
            return True

    async def snapshot(self) -> tuple[Device, ...]:
        """Return one stable, immutable view of the current device references."""
        async with self._lock:
            return tuple(self._devices.values())

    def get_device(self, device_id: str) -> Device | None:
        """Get a device by ID."""
        return self._devices.get(device_id)

    def list_devices(self) -> list[str]:
        """List all registered device IDs."""
        return list(self._devices.keys())

    def list_devices_by_type(self, device_type: str) -> list[Device]:
        """List devices of a specific type."""
        return [
            d for d in self._devices.values()
            if d.metadata.device_type == device_type
        ]

    def list_devices_by_tag(self, tag: str) -> list[Device]:
        """List devices with a specific tag."""
        return [
            d for d in self._devices.values()
            if tag in d.metadata.tags
        ]

    async def health_check_all(self) -> dict[str, dict[str, Any]]:
        """Perform health check on all devices."""
        results = {}
        for device_id, device in self._devices.items():
            try:
                results[device_id] = await device.health_check()
            except Exception as e:  # noqa: BLE001
                results[device_id] = {"error": str(e), "healthy": False}
        return results

    async def reset_all(self) -> dict[str, bool]:
        """Reset all devices."""
        results = {}
        for device_id, device in self._devices.items():
            try:
                results[device_id] = await device.reset()
            except Exception:  # noqa: BLE001
                results[device_id] = False
        return results

    async def close_all(self) -> None:
        """Close all devices."""
        for device in list(self._devices.values()):
            try:
                await device.close()
            except Exception:  # noqa: BLE001,S110
                pass
        self._devices.clear()

    def get_metadata_summary(self) -> list[dict[str, Any]]:
        """Get metadata summary for all devices."""
        summary = []
        for device in self._devices.values():
            meta = device.metadata
            summary.append({
                "device_id": meta.device_id,
                "device_type": meta.device_type,
                "manufacturer": meta.manufacturer,
                "model": meta.model,
                "state": device.state.name,
                "capabilities": [c.name for c in meta.capabilities],
                "tags": meta.tags,
                "location": meta.location,
            })
        return summary
