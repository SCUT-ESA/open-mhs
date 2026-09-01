"""Device abstraction for MHS."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable


class DeviceState(Enum):
    """Device operational states."""

    UNKNOWN = auto()
    OFFLINE = auto()
    ONLINE = auto()
    BUSY = auto()
    ERROR = auto()
    MAINTENANCE = auto()
    SIMULATED = auto()


class AccessLevel(Enum):
    """Access control levels for device operations."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


def _is_finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


@dataclass
class SafetyLimit:
    """Safety constraint for a device parameter."""

    parameter: str
    min_value: float | None = None
    max_value: float | None = None
    unit: str = ""
    description: str = ""
    hard_limit: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.parameter, str) or not self.parameter:
            raise ValueError("Safety limit parameter must be a non-empty string")
        for name, bound in (("min_value", self.min_value), ("max_value", self.max_value)):
            if bound is not None and not _is_finite_number(bound):
                raise TypeError(f"Safety limit {name} must be a finite number")
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError("Safety limit minimum cannot exceed maximum")
        if not isinstance(self.hard_limit, bool):
            raise TypeError("Safety limit hard_limit must be a bool")

    def validate(self, value: float) -> bool:
        """Check if a finite, numeric value is within safety limits."""
        if not _is_finite_number(value):
            return False
        if self.min_value is not None and value < self.min_value:
            return False
        return self.max_value is None or value <= self.max_value


_UNSET = object()


@dataclass
class DeviceCapability:
    """Describe a capability with one canonical parameter contract."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    read_only: bool = False
    async_capable: bool = False
    readable: bool = True
    writable: bool | object = field(default=_UNSET)
    schema: dict[str, Any] = field(default_factory=dict)
    required: list[str] | Any = field(default_factory=list)
    access: AccessLevel = AccessLevel.READ

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Capability name must be a non-empty string")
        if not isinstance(self.readable, bool):
            raise TypeError("Capability readable must be a bool")
        if not isinstance(self.read_only, bool):
            raise TypeError("Capability read_only must be a bool")
        writable_supplied = self.writable is not _UNSET
        if writable_supplied and not isinstance(self.writable, bool):
            raise TypeError("Capability writable must be a bool")
        if self.read_only and writable_supplied and self.writable:
            raise ValueError("Capability read_only=True conflicts with writable=True")
        self.writable = (
            False if self.read_only else (True if not writable_supplied else self.writable)
        )

        if not isinstance(self.parameters, dict) or not isinstance(self.schema, dict):
            raise TypeError("Capability parameters and schema must be dictionaries")
        parameter_required = self._schema_required(self.parameters)
        declared_required = self._schema_required(self.schema)
        parameter_schema = self._normalise_schema(self.parameters)
        declared_schema = self._normalise_schema(self.schema)
        if (
            parameter_required
            and declared_required
            and set(parameter_required) != set(declared_required)
        ):
            raise ValueError("Capability parameters and schema required must agree")
        if parameter_schema and declared_schema and parameter_schema != declared_schema:
            raise ValueError("Capability parameters and schema must describe the same contract")
        canonical = declared_schema or parameter_schema
        schema_required = declared_required or parameter_required
        self.schema = dict(canonical)
        self.parameters = dict(canonical)

        if isinstance(self.required, (str, bytes)):
            raise TypeError("Capability required must be a non-string iterable")
        try:
            required = list(self.required)
        except TypeError as exc:
            raise TypeError("Capability required must be a non-string iterable") from exc
        if any(not isinstance(name, str) or not name for name in required):
            raise TypeError("Capability required names must be non-empty strings")
        if len(required) != len(set(required)):
            raise ValueError("Capability required names must be unique")
        if schema_required:
            if required and set(required) != set(schema_required):
                raise ValueError("Capability required and schema required must agree")
            required = list(schema_required)
        unknown = [name for name in required if name not in canonical]
        if unknown:
            raise ValueError(f"Capability required names are not in schema: {', '.join(unknown)}")
        self.required = required
        if not isinstance(self.access, AccessLevel):
            try:
                self.access = AccessLevel(self.access)
            except (TypeError, ValueError) as exc:
                raise TypeError("Capability access must be an AccessLevel") from exc

    @staticmethod
    def _schema_required(value: dict[str, Any]) -> list[str]:
        properties = value.get("properties")
        required = value.get("required", []) if isinstance(properties, dict) else []
        if isinstance(required, (str, bytes)):
            raise TypeError("Capability schema required must be a non-string iterable")
        try:
            result = list(required)
        except TypeError as exc:
            raise TypeError("Capability schema required must be an iterable") from exc
        if any(not isinstance(name, str) or not name for name in result):
            raise TypeError("Capability schema required names must be non-empty strings")
        if len(result) != len(set(result)):
            raise ValueError("Capability schema required names must be unique")
        return result

    @staticmethod
    def _normalise_schema(value: dict[str, Any]) -> dict[str, Any]:
        """Accept legacy flat fields and object-shaped JSON schema."""
        if not value:
            return {}
        if set(value) - {"type", "properties", "required", "additionalProperties"}:
            return dict(value)
        properties = value.get("properties")
        if not isinstance(properties, dict):
            return dict(value)
        return dict(properties)


@dataclass
class DeviceMetadata:
    """Static metadata describing a device."""

    device_id: str
    device_type: str
    manufacturer: str = ""
    model: str = ""
    serial_number: str | None = None
    version: str = "1.0.0"
    capabilities: list[DeviceCapability] = field(default_factory=list)
    safety_limits: list[SafetyLimit] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    location: str = ""
    natural_language_description: str = ""
    documentation_url: str = ""
    driver_class: str = ""
    connection_info: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Device(Protocol):
    """Protocol defining the MHS Device interface."""

    @property
    def metadata(self) -> DeviceMetadata: ...

    @property
    def state(self) -> DeviceState: ...

    async def read(self, capability: str, **params: Any) -> dict[str, Any]: ...

    async def write(self, capability: str, **params: Any) -> dict[str, Any]: ...

    async def discover(self) -> list[str]: ...

    async def health_check(self) -> dict[str, Any]: ...

    async def reset(self) -> bool: ...

    async def close(self) -> None: ...


class BaseDevice:
    """Base implementation with fail-closed operation gates."""

    _OPERABLE_STATES = frozenset((DeviceState.ONLINE, DeviceState.SIMULATED))

    def __init__(self, metadata: DeviceMetadata):
        self._metadata = metadata
        self._state = DeviceState.UNKNOWN
        self._last_read: dict[str, Any] = {}
        self._last_write: dict[str, Any] = {}
        self._state_history: list[tuple[float, DeviceState]] = []
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

    def _ensure_operable(self, operation: str) -> None:
        if self._state not in self._OPERABLE_STATES:
            raise DeviceError(
                f"Cannot {operation} device {self._metadata.device_id} while it is "
                f"{self._state.name.lower()}"
            )

    async def read(self, capability: str, **params: Any) -> dict[str, Any]:
        """Read a capability only while the device is safely operable."""
        async with self._lock:
            self._ensure_operable("read")
            cap = self._get_capability(capability)
            if cap is None:
                raise CapabilityError(
                    f"Capability '{capability}' not found on {self._metadata.device_id}"
                )
            if not cap.readable:
                raise CapabilityError(f"Capability '{capability}' is not readable")
            self._validate_parameters(cap, params)
            result = await self._do_read(capability, **params)
            self._last_read = {
                "capability": capability,
                "timestamp": time.time(),
                "result": result,
            }
            return result

    async def write(self, capability: str, **params: Any) -> dict[str, Any]:
        """Write a capability only while the device is safely operable."""
        async with self._lock:
            self._ensure_operable("write")
            cap = self._get_capability(capability)
            if cap is None:
                raise CapabilityError(
                    f"Capability '{capability}' not found on {self._metadata.device_id}"
                )
            if not cap.writable or cap.read_only:
                raise CapabilityError(f"Capability '{capability}' is read-only")
            safety_parameters = {limit.parameter for limit in self._metadata.safety_limits}
            self._validate_parameters(
                cap,
                params,
                finite_error=(
                    SafetyError if safety_parameters.intersection(params) else CapabilityError
                ),
            )
            self._validate_safety_limits(capability, params)
            result = await self._do_write(capability, **params)
            self._last_write = {
                "capability": capability,
                "timestamp": time.time(),
                "params": params,
                "result": result,
            }
            return result

    async def discover(self) -> list[str]:
        """Return the names of advertised capabilities."""
        return [cap.name for cap in self._metadata.capabilities]

    async def health_check(self) -> dict[str, Any]:
        """Return health without requiring an operational state."""
        return {
            "device_id": self._metadata.device_id,
            "state": self._state.name,
            "healthy": self._state in self._OPERABLE_STATES,
            "timestamp": time.time(),
        }

    async def reset(self) -> bool:
        """Reset an operable device to its current operational mode."""
        async with self._lock:
            self._ensure_operable("reset")
            state = self._state
            self._set_state(state)
            return True

    async def close(self) -> None:
        """Close safely and idempotently, including during cleanup."""
        async with self._lock:
            if self._state is DeviceState.OFFLINE:
                return
            self._set_state(DeviceState.OFFLINE)

    def _get_capability(self, name: str) -> DeviceCapability | None:
        for cap in self._metadata.capabilities:
            if cap.name == name:
                return cap
        return None

    @staticmethod
    def _validate_parameters(
        capability: DeviceCapability,
        params: dict[str, Any],
        finite_error: type[Exception] | None = None,
    ) -> None:
        BaseDevice._validate_finite_parameters(params, error_type=finite_error)
        missing = [name for name in capability.required if name not in params]
        if missing:
            raise CapabilityError(
                f"Capability '{capability.name}' requires parameters: {', '.join(missing)}"
            )
        if not capability.schema:
            return
        extra = [name for name in params if name not in capability.schema]
        if extra:
            raise CapabilityError(
                f"Capability '{capability.name}' does not accept parameters: {', '.join(extra)}"
            )
        for name, value in params.items():
            definition = capability.schema[name]
            expected = definition.get("type") if isinstance(definition, dict) else None
            if expected is not None and not BaseDevice._matches_schema_type(value, expected):
                raise CapabilityError(
                    f"Capability '{capability.name}' parameter '{name}' has an invalid type"
                )

    @staticmethod
    def _validate_finite_parameters(
        value: Any,
        path: str = "parameters",
        error_type: type[Exception] | None = None,
    ) -> None:
        if error_type is None:
            error_type = CapabilityError
        if isinstance(value, float):
            if not math.isfinite(value):
                raise error_type(f"{path} contains a non-finite number")
            return
        if isinstance(value, dict):
            for name, item in value.items():
                BaseDevice._validate_finite_parameters(item, f"{path}.{name}", error_type)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                BaseDevice._validate_finite_parameters(item, f"{path}[{index}]", error_type)

    @staticmethod
    def _matches_schema_type(value: Any, expected: Any) -> bool:
        if isinstance(expected, list):
            return any(BaseDevice._matches_schema_type(value, item) for item in expected)
        if expected == "null":
            return value is None
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return _is_finite_number(value)
        if expected == "string":
            return isinstance(value, str)
        if expected == "array":
            return isinstance(value, list)
        if expected == "object":
            return isinstance(value, dict)
        return True

    def _validate_safety_limits(self, capability: str, params: dict[str, Any]) -> None:
        for limit in self._metadata.safety_limits:
            if limit.parameter in params and not limit.validate(params[limit.parameter]):
                value = params[limit.parameter]
                raise SafetyError(
                    f"Value {value} for '{limit.parameter}' violates safety limit "
                    f"[{limit.min_value}, {limit.max_value}] {limit.unit}"
                )

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        raise NotImplementedError

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        raise NotImplementedError


class DeviceError(Exception):
    """Base exception for device operations."""


class CapabilityError(DeviceError):
    """Raised when a capability is not available or invalid."""


class SafetyError(DeviceError):
    """Raised when a safety limit is violated."""


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
