"""Device registry for MHS."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import asyncio

from openmhs.core.device import Device, DeviceMetadata, DeviceState


class DeviceRegistry:
    """Registry for managing connected devices."""

    def __init__(self):
        self._devices: Dict[str, Device] = {}
        self._lock = asyncio.Lock()

    async def register(self, device: Device) -> None:
        """Register a device."""
        async with self._lock:
            self._devices[device.metadata.device_id] = device

    async def unregister(self, device_id: str) -> Optional[Device]:
        """Unregister a device."""
        async with self._lock:
            device = self._devices.pop(device_id, None)
            if device:
                await device.close()
            return device

    def get_device(self, device_id: str) -> Optional[Device]:
        """Get a device by ID."""
        return self._devices.get(device_id)

    def list_devices(self) -> List[str]:
        """List all registered device IDs."""
        return list(self._devices.keys())

    def list_devices_by_type(self, device_type: str) -> List[Device]:
        """List devices of a specific type."""
        return [
            d for d in self._devices.values()
            if d.metadata.device_type == device_type
        ]

    def list_devices_by_tag(self, tag: str) -> List[Device]:
        """List devices with a specific tag."""
        return [
            d for d in self._devices.values()
            if tag in d.metadata.tags
        ]

    async def health_check_all(self) -> Dict[str, Dict[str, Any]]:
        """Perform health check on all devices."""
        results = {}
        for device_id, device in self._devices.items():
            try:
                results[device_id] = await device.health_check()
            except Exception as e:
                results[device_id] = {"error": str(e), "healthy": False}
        return results

    async def reset_all(self) -> Dict[str, bool]:
        """Reset all devices."""
        results = {}
        for device_id, device in self._devices.items():
            try:
                results[device_id] = await device.reset()
            except Exception:
                results[device_id] = False
        return results

    async def close_all(self) -> None:
        """Close all devices."""
        for device in list(self._devices.values()):
            try:
                await device.close()
            except Exception:
                pass
        self._devices.clear()

    def get_metadata_summary(self) -> List[Dict[str, Any]]:
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
