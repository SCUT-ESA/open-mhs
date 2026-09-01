"""Driver abstraction for MHS."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import math
import pkgutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from openmhs.core.device import BaseDevice

from openmhs.core.device import DeviceMetadata

logger = logging.getLogger(__name__)


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


@dataclass
class DriverConfig:
    """Configuration for a device driver."""

    driver_name: str
    connection_params: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    retry_count: int = 3
    auto_connect: bool = True
    log_operations: bool = True
    safety_override: bool = False
    simulation: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.driver_name, str) or not self.driver_name.strip():
            raise ValueError("driver_name must be a non-empty string")
        if not _is_finite_number(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        if isinstance(self.retry_count, bool) or not isinstance(self.retry_count, int):
            raise TypeError("retry_count must be a non-negative integer")
        if self.retry_count < 0:
            raise ValueError("retry_count must be a non-negative integer")
        for name in ("auto_connect", "log_operations", "safety_override", "simulation"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")
        if self.safety_override:
            raise ValueError("safety_override is not supported")
        if not isinstance(self.connection_params, dict):
            raise TypeError("connection_params must be a dictionary")
        legacy_simulation = self.connection_params.get("simulation", self.simulation)
        if not isinstance(legacy_simulation, bool):
            raise TypeError("connection_params['simulation'] must be a bool")
        if "simulation" in self.connection_params and legacy_simulation != self.simulation:
            raise ValueError("simulation and connection_params['simulation'] conflict")


class Driver:
    """Base class for translating MHS commands to device protocols."""

    DRIVER_NAME: ClassVar[str] = ""
    SUPPORTED_DEVICES: ClassVar[list[str]] = []

    def __init__(
        self,
        config: DriverConfig,
        *,
        simulation: bool | None = None,
        backend_factory: Any = None,
    ):
        if simulation is not None and not isinstance(simulation, bool):
            raise TypeError("simulation must be a bool")
        self.config = config
        self.simulation = config.simulation if simulation is None else simulation
        self.backend_factory = backend_factory
        self._connected = False
        self._device: BaseDevice | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def device(self) -> BaseDevice | None:
        return self._device

    async def connect(self) -> bool:
        """Connect to the physical device."""
        raise NotImplementedError

    async def _connect_candidate(self, candidate: Any) -> bool:
        """Adopt one fully-connected candidate without leaking on retries/cancel."""
        async with self._lifecycle_lock:
            if self._connected and self._device is not None:
                await self._cleanup_candidate(candidate)
                return True
            try:
                connected = await candidate.connect()
            except asyncio.CancelledError:
                await self._cleanup_candidate(candidate)
                raise
            except Exception:  # noqa: BLE001
                await self._cleanup_candidate(candidate)
                self._connected = False
                return False
            if not connected:
                await self._cleanup_candidate(candidate)
                self._connected = False
                return False
            previous = self._device
            self._device = candidate
            self._connected = True
            if previous is not None and previous is not candidate:
                await self._cleanup_candidate(previous)
            return True

    @staticmethod
    async def _cleanup_candidate(candidate: Any) -> None:
        close = getattr(candidate, "close", None)
        if close is None:
            return
        task = asyncio.create_task(close())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except BaseException:  # noqa: BLE001
                    break
            raise
        except BaseException:
            logger.exception("Candidate cleanup failed")

    async def _disconnect_base(self) -> None:
        """Disconnect the base-managed device with cleanup state guarantees.

        Adapter-specific ``disconnect`` overrides are intentionally not wrapped;
        adapters may retain their established lifecycle behavior and call this
        helper when appropriate.
        """
        async with self._lifecycle_lock:
            device = self._device
            self._connected = False
            self._device = None
            if device is None:
                return
            close_task = asyncio.create_task(device.close())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                while not close_task.done():
                    try:
                        await asyncio.shield(close_task)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        logger.exception("Base driver cleanup failed")
                        break
                if not close_task.cancelled():
                    close_task.exception()
                raise

    async def disconnect(self) -> None:
        """Disconnect the base-managed device."""
        await self._disconnect_base()

    async def discover_capabilities(self) -> list[str]:
        """Discover what capabilities are available."""
        if self._device:
            return await self._device.discover()
        return []

    def get_metadata(self) -> DeviceMetadata | None:
        """Get device metadata."""
        if self._device:
            return self._device.metadata
        return None


class DriverConflictError(ValueError):
    """Raised when two driver classes claim the same driver name."""


class DriverRegistry:
    """Registry for discovering and loading drivers."""

    def __init__(self) -> None:
        self._drivers: dict[str, type[Driver]] = {}
        self.discovery_diagnostics: list[str] = []

    def register(self, driver_class: type[Driver], *, replace: bool = False) -> None:
        """Register a driver class, rejecting duplicate names by default."""
        if not inspect.isclass(driver_class) or not issubclass(driver_class, Driver):
            raise ValueError(f"{driver_class} must be a subclass of Driver")
        if not driver_class.DRIVER_NAME:
            raise ValueError(f"{driver_class} must define DRIVER_NAME")
        existing = self._drivers.get(driver_class.DRIVER_NAME)
        if existing is not None and existing is not driver_class and not replace:
            raise DriverConflictError(
                f"Driver name '{driver_class.DRIVER_NAME}' is already registered by "
                f"{existing.__module__}.{existing.__qualname__}"
            )
        self._drivers[driver_class.DRIVER_NAME] = driver_class

    def unregister(self, driver_name: str) -> None:
        """Unregister a driver."""
        self._drivers.pop(driver_name, None)

    def get_driver(self, name: str) -> type[Driver] | None:
        """Get a driver class by name."""
        return self._drivers.get(name)

    def list_drivers(self) -> list[str]:
        """List all registered driver names."""
        return list(self._drivers.keys())

    def find_driver_for_device(self, device_type: str) -> type[Driver] | None:
        """Find a driver that supports a given device type."""
        for driver_class in self._drivers.values():
            if device_type in driver_class.SUPPORTED_DEVICES:
                return driver_class
        return None

    def auto_discover(self, package_name: str = "openmhs.adapters") -> list[str]:
        """Recursively discover drivers and return actionable diagnostics."""
        diagnostics: list[str] = []
        self.discovery_diagnostics = diagnostics
        try:
            package = importlib.import_module(package_name)
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(f"Unable to import {package_name}: {exc}")
            return diagnostics

        module_names = [package_name]
        package_path = getattr(package, "__path__", None)
        if package_path is not None:
            try:
                module_names.extend(
                    name for _, name, _ in pkgutil.walk_packages(package_path, package_name + ".")
                )
            except Exception:  # noqa: BLE001,S110
                pass
        for module_name in module_names:
            try:
                module = (
                    package if module_name == package_name else importlib.import_module(module_name)
                )
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        obj.__module__ == module.__name__
                        and issubclass(obj, Driver)
                        and obj is not Driver
                        and obj.DRIVER_NAME
                    ):
                        self.register(obj)
            except DriverConflictError as exc:
                diagnostics.append(f"Driver conflict in {module_name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                diagnostics.append(f"Unable to inspect {module_name}: {exc}")
        return diagnostics


# Global driver registry instance
global_driver_registry = DriverRegistry()


def register_driver(driver_class: type[Driver]) -> type[Driver]:
    """Decorator to register a driver class."""
    global_driver_registry.register(driver_class)
    return driver_class
