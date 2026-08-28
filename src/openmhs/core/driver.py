"""Driver abstraction for MHS."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type
import importlib
import inspect
import pkgutil

from openmhs.core.device import BaseDevice, Device, DeviceMetadata


@dataclass
class DriverConfig:
    """Configuration for a device driver."""
    driver_name: str
    connection_params: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    retry_count: int = 3
    auto_connect: bool = True
    log_operations: bool = True
    safety_override: bool = False  # Allow overriding hard limits (dangerous)


class Driver:
    """Base class for MHS device drivers.
    
    A driver is responsible for translating MHS commands to
    device-specific protocols and vice versa.
    """

    DRIVER_NAME: str = ""
    SUPPORTED_DEVICES: List[str] = []
    
    def __init__(self, config: DriverConfig):
        self.config = config
        self._connected = False
        self._device: Optional[BaseDevice] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def device(self) -> Optional[BaseDevice]:
        return self._device

    async def connect(self) -> bool:
        """Connect to the physical device.
        
        Returns:
            True if connection successful.
        """
        raise NotImplementedError

    async def disconnect(self) -> None:
        """Disconnect from the physical device."""
        self._connected = False
        if self._device:
            await self._device.close()
            self._device = None

    async def discover_capabilities(self) -> List[str]:
        """Discover what capabilities are available."""
        if self._device:
            return await self._device.discover()
        return []

    def get_metadata(self) -> Optional[DeviceMetadata]:
        """Get device metadata."""
        if self._device:
            return self._device.metadata
        return None


class DriverRegistry:
    """Registry for discovering and loading drivers."""

    def __init__(self):
        self._drivers: Dict[str, Type[Driver]] = {}

    def register(self, driver_class: Type[Driver]) -> None:
        """Register a driver class."""
        if not issubclass(driver_class, Driver):
            raise ValueError(f"{driver_class} must be a subclass of Driver")
        self._drivers[driver_class.DRIVER_NAME] = driver_class

    def unregister(self, driver_name: str) -> None:
        """Unregister a driver."""
        self._drivers.pop(driver_name, None)

    def get_driver(self, name: str) -> Optional[Type[Driver]]:
        """Get a driver class by name."""
        return self._drivers.get(name)

    def list_drivers(self) -> List[str]:
        """List all registered driver names."""
        return list(self._drivers.keys())

    def find_driver_for_device(self, device_type: str) -> Optional[Type[Driver]]:
        """Find a driver that supports a given device type."""
        for driver_class in self._drivers.values():
            if device_type in driver_class.SUPPORTED_DEVICES:
                return driver_class
        return None

    def auto_discover(self, package_name: str = "openmhs.adapters") -> None:
        """Auto-discover and register drivers from a package."""
        try:
            package = importlib.import_module(package_name)
            for _, modname, ispkg in pkgutil.iter_modules(package.__path__, package_name + "."):
                if not ispkg:
                    try:
                        module = importlib.import_module(modname)
                        for name, obj in inspect.getmembers(module, inspect.isclass):
                            if (issubclass(obj, Driver) and obj is not Driver 
                                and hasattr(obj, 'DRIVER_NAME') and obj.DRIVER_NAME):
                                self.register(obj)
                    except Exception:
                        pass
        except ImportError:
            pass


# Global driver registry instance
global_driver_registry = DriverRegistry()


def register_driver(driver_class: Type[Driver]) -> Type[Driver]:
    """Decorator to register a driver class."""
    global_driver_registry.register(driver_class)
    return driver_class
