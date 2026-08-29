"""Device abstraction for MHS."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
import asyncio
import time


class DeviceState(Enum):
    """Device operational states."""
    UNKNOWN = auto()
    OFFLINE = auto()
    ONLINE = auto()
    BUSY = auto()
    ERROR = auto()
    MAINTENANCE = auto()


class AccessLevel(Enum):
    """Access control levels for device operations."""
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


@dataclass
class SafetyLimit:
    """Safety constraint for a device parameter."""
    parameter: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    unit: str = ""
    description: str = ""
    hard_limit: bool = True  # If True, cannot be overridden

    def validate(self, value: float) -> bool:
        """Check if value is within safety limits."""
        if self.min_value is not None and value < self.min_value:
            return False
        if self.max_value is not None and value > self.max_value:
            return False
        return True


@dataclass
class DeviceCapability:
    """Describes a capability of a device."""
    name: str
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    read_only: bool = False
    async_capable: bool = False


@dataclass
class DeviceMetadata:
    """Static metadata describing a device."""
    device_id: str
    device_type: str
    manufacturer: str = ""
    model: str = ""
    serial_number: Optional[str] = None
    version: str = "1.0.0"
    capabilities: List[DeviceCapability] = field(default_factory=list)
    safety_limits: List[SafetyLimit] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    location: str = ""
    natural_language_description: str = ""
    documentation_url: str = ""
    driver_class: str = ""
    connection_info: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Device(Protocol):
    """Protocol defining the MHS Device interface.

    Any physical device that can be controlled by an AI agent
    must implement this interface.
    """

    @property
    def metadata(self) -> DeviceMetadata:
        """Return device metadata."""
        ...

    @property
    def state(self) -> DeviceState:
        """Return current device state."""
        ...

    async def read(self, capability: str, **params: Any) -> Dict[str, Any]:
        """Read data from the device.

        Args:
            capability: Name of the capability to read.
            **params: Additional parameters for the read operation.

        Returns:
            Dictionary containing the read data.
        """
        ...

    async def write(self, capability: str, **params: Any) -> Dict[str, Any]:
        """Write data/command to the device.

        Args:
            capability: Name of the capability to write to.
            **params: Parameters for the write operation.

        Returns:
            Dictionary containing the result.
        """
        ...

    async def discover(self) -> List[str]:
        """Discover available capabilities.

        Returns:
            List of capability names.
        """
        ...

    async def health_check(self) -> Dict[str, Any]:
        """Perform a health check on the device.

        Returns:
            Health status dictionary.
        """
        ...

    async def reset(self) -> bool:
        """Reset the device to a known good state.

        Returns:
            True if reset successful.
        """
        ...

    async def close(self) -> None:
        """Clean up resources and disconnect."""
        ...


class BaseDevice:
    """Base implementation of the Device protocol.

    Provides common functionality that most device implementations need.
    """

    def __init__(self, metadata: DeviceMetadata):
        self._metadata = metadata
        self._state = DeviceState.UNKNOWN
        self._last_read: Dict[str, Any] = {}
        self._last_write: Dict[str, Any] = {}
        self._state_history: List[tuple[float, DeviceState]] = []
        self._lock = asyncio.Lock()

    @property
    def metadata(self) -> DeviceMetadata:
        return self._metadata

    @property
    def state(self) -> DeviceState:
        return self._state

    def _set_state(self, state: DeviceState) -> None:
        self._state = state
        self._state_history.append((time.time(), state))

    async def read(self, capability: str, **params: Any) -> Dict[str, Any]:
        """Read implementation with safety checks."""
        async with self._lock:
            if self._state == DeviceState.OFFLINE:
                raise DeviceError(f"Device {self._metadata.device_id} is offline")
            if self._state == DeviceState.ERROR:
                raise DeviceError(f"Device {self._metadata.device_id} is in error state")

            cap = self._get_capability(capability)
            if cap is None:
                raise CapabilityError(f"Capability '{capability}' not found on {self._metadata.device_id}")

            result = await self._do_read(capability, **params)
            self._last_read = {
                "capability": capability,
                "timestamp": time.time(),
                "result": result,
            }
            return result

    async def write(self, capability: str, **params: Any) -> Dict[str, Any]:
        """Write implementation with safety checks."""
        async with self._lock:
            if self._state == DeviceState.OFFLINE:
                raise DeviceError(f"Device {self._metadata.device_id} is offline")
            if self._state == DeviceState.ERROR:
                raise DeviceError(f"Device {self._metadata.device_id} is in error state")
            if self._state == DeviceState.MAINTENANCE:
                raise DeviceError(f"Device {self._metadata.device_id} is under maintenance")

            cap = self._get_capability(capability)
            if cap is None:
                raise CapabilityError(f"Capability '{capability}' not found on {self._metadata.device_id}")

            if cap.read_only:
                raise CapabilityError(f"Capability '{capability}' is read-only")

            # Validate safety limits
            self._validate_safety_limits(capability, params)

            result = await self._do_write(capability, **params)
            self._last_write = {
                "capability": capability,
                "timestamp": time.time(),
                "params": params,
                "result": result,
            }
            return result

    async def discover(self) -> List[str]:
        """Return list of capability names."""
        return [cap.name for cap in self._metadata.capabilities]

    async def health_check(self) -> Dict[str, Any]:
        """Default health check - override in subclasses."""
        return {
            "device_id": self._metadata.device_id,
            "state": self._state.name,
            "healthy": self._state in (DeviceState.ONLINE, DeviceState.BUSY),
            "timestamp": time.time(),
        }

    async def reset(self) -> bool:
        """Default reset - override in subclasses."""
        self._set_state(DeviceState.ONLINE)
        return True

    async def close(self) -> None:
        """Default cleanup - override in subclasses."""
        self._set_state(DeviceState.OFFLINE)

    def _get_capability(self, name: str) -> Optional[DeviceCapability]:
        for cap in self._metadata.capabilities:
            if cap.name == name:
                return cap
        return None

    def _validate_safety_limits(self, capability: str, params: Dict[str, Any]) -> None:
        for limit in self._metadata.safety_limits:
            if limit.parameter in params:
                value = params[limit.parameter]
                if isinstance(value, (int, float)) and not limit.validate(value):
                    raise SafetyError(
                        f"Value {value} for '{limit.parameter}' violates safety limit "
                        f"[{limit.min_value}, {limit.max_value}] {limit.unit}"
                    )

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        """Override this in subclasses."""
        raise NotImplementedError

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        """Override this in subclasses."""
        raise NotImplementedError


class DeviceError(Exception):
    """Base exception for device operations."""
    pass


class CapabilityError(DeviceError):
    """Raised when a capability is not available or invalid."""
    pass


class SafetyError(DeviceError):
    """Raised when a safety limit is violated."""
    pass


__all__ = [
    "AccessLevel",
    "BaseDevice",
    "CapabilityError",
    "Device",
    "DeviceCapability",
    "DeviceError",
    "DeviceMetadata",
    "DeviceState",
    "SafetyError",
    "SafetyLimit",
]
