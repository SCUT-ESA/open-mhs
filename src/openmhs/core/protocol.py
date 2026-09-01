"""MHS Protocol implementation."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from openmhs.core.registry import DeviceRegistry

from openmhs.core.device import (
    AccessLevel,
    CapabilityError,
    DeviceError,
    SafetyError,
)


class CommandType(Enum):
    """Types of MHS commands."""

    READ = auto()
    WRITE = auto()
    DISCOVER = auto()
    HEALTH_CHECK = auto()
    RESET = auto()
    CONNECT = auto()
    DISCONNECT = auto()
    CONFIGURE = auto()
    SCRIPT = auto()


@dataclass
class Command:
    """An MHS command."""

    command_id: str
    command_type: CommandType
    device_id: str
    capability: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    auth_token: str | None = field(default=None, repr=False)
    timeout: float | None = None

    def __post_init__(self) -> None:
        # Keep malformed structural fields available for execute() to report as
        # a typed response, while rejecting unsafe parameter values early.
        if isinstance(self.parameters, dict):
            _validate_finite_parameters(self.parameters)

    def to_json(self) -> str:
        return json.dumps(
            {
                "command_id": self.command_id,
                "command_type": self.command_type.name,
                "device_id": self.device_id,
                "capability": self.capability,
                "parameters": self.parameters,
                "timestamp": self.timestamp,
                "timeout": self.timeout,
            },
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, data: str) -> Command:
        obj = json.loads(data, parse_constant=_reject_constant)
        return cls(
            command_id=obj["command_id"],
            command_type=CommandType[obj["command_type"]],
            device_id=obj["device_id"],
            capability=obj.get("capability"),
            parameters=obj.get("parameters", {}),
            timestamp=obj.get("timestamp", time.time()),
            timeout=obj.get("timeout"),
        )


@dataclass
class Response:
    """An MHS response."""

    command_id: str
    device_id: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    timestamp: float = field(default_factory=time.time)
    execution_time_ms: float | None = None
    error_code: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "command_id": self.command_id,
                "device_id": self.device_id,
                "success": self.success,
                "data": self.data,
                "error_message": self.error_message,
                "timestamp": self.timestamp,
                "execution_time_ms": self.execution_time_ms,
                "error_code": self.error_code,
            },
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, data: str) -> Response:
        obj = json.loads(data, parse_constant=_reject_constant)
        return cls(
            command_id=obj["command_id"],
            device_id=obj["device_id"],
            success=obj["success"],
            data=obj.get("data", {}),
            error_message=obj.get("error_message"),
            timestamp=obj.get("timestamp", time.time()),
            execution_time_ms=obj.get("execution_time_ms"),
            error_code=obj.get("error_code"),
        )


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant is not allowed: {value}")


def _validate_finite_parameters(value: Any, path: str = "parameters") -> None:
    """Reject non-finite floats recursively while preserving JSON booleans."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, dict):
        for name, item in value.items():
            _validate_finite_parameters(item, f"{path}.{name}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_finite_parameters(item, f"{path}[{index}]")


def _is_valid_timeout(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value)) and value > 0
    except (OverflowError, ValueError):
        return False


def _minimum_access(command_type: CommandType) -> AccessLevel:
    if command_type is CommandType.WRITE:
        return AccessLevel.WRITE
    if command_type in (CommandType.RESET, CommandType.DISCONNECT):
        return AccessLevel.ADMIN
    return AccessLevel.READ


class UnsupportedCommandError(DeviceError):
    """Raised for command types intentionally unsupported by this protocol."""


class MHSProtocol:
    """Translate MHS commands to device operations."""

    _UNSUPPORTED = frozenset((CommandType.CONNECT, CommandType.CONFIGURE, CommandType.SCRIPT))

    def __init__(self, registry: DeviceRegistry | None = None):
        from openmhs.core.registry import DeviceRegistry

        self.registry = registry or DeviceRegistry()

    async def execute(
        self, command: Command, access_level: AccessLevel = AccessLevel.READ
    ) -> Response:
        """Execute a command, preserving cancellation and returning typed errors."""
        start_time = time.time()
        if not isinstance(access_level, AccessLevel):
            return self._error_response(
                command, "permission_denied", "Invalid access level", start_time
            )
        try:
            self._validate_command(command)
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(command, "invalid_command", str(exc), start_time)
        if command.command_type in self._UNSUPPORTED:
            return self._error_response(
                command,
                "unsupported_command",
                f"Command {command.command_type.name} is unsupported",
                start_time,
            )
        try:
            required_access = _minimum_access(command.command_type)
            if _access_rank(access_level) < _access_rank(required_access):
                raise PermissionError(
                    f"{command.command_type.name} requires {required_access.value} access"
                )
            device = self.registry.get_device(command.device_id)
            if device is None:
                return self._error_response(
                    command,
                    "device_not_found",
                    f"Device '{command.device_id}' not found",
                    start_time,
                )
            operation = self._execute_on_device(device, command, access_level)
            if command.timeout is not None:
                result = await asyncio.wait_for(operation, timeout=command.timeout)
            else:
                result = await operation
            return Response(
                command_id=command.command_id,
                device_id=command.device_id,
                success=True,
                data=result,
                execution_time_ms=(time.time() - start_time) * 1000,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return self._error_response(command, "timeout", "Command timed out", start_time)
        except PermissionError as exc:
            return self._error_response(command, "permission_denied", str(exc), start_time)
        except UnsupportedCommandError as exc:
            return self._error_response(command, "unsupported_command", str(exc), start_time)
        except SafetyError as exc:
            return self._error_response(command, "safety_error", str(exc), start_time)
        except CapabilityError as exc:
            return self._error_response(command, "capability_error", str(exc), start_time)
        except DeviceError as exc:
            return self._error_response(command, "device_error", str(exc), start_time)
        except Exception:  # noqa: BLE001
            return self._error_response(
                command, "internal_error", "Internal server error", start_time
            )

    @staticmethod
    def _validate_command(command: Command) -> None:
        if not isinstance(command.command_type, CommandType):
            raise TypeError("command_type must be a CommandType")
        if not isinstance(command.command_id, str) or not command.command_id:
            raise ValueError("command_id must be a non-empty string")
        if not isinstance(command.device_id, str) or not command.device_id:
            raise ValueError("device_id must be a non-empty string")
        if not isinstance(command.parameters, dict):
            raise TypeError("parameters must be a dictionary")
        _validate_finite_parameters(command.parameters)
        if command.command_type in (CommandType.READ, CommandType.WRITE) and (
            not isinstance(command.capability, str) or not command.capability
        ):
            raise ValueError(f"{command.command_type.name} command requires 'capability'")
        if command.timeout is not None and not _is_valid_timeout(command.timeout):
            raise ValueError("timeout must be a finite positive number")

    @staticmethod
    def _error_response(
        command: Command, error_code: str, message: str, start_time: float
    ) -> Response:
        return Response(
            command_id=command.command_id,
            device_id=command.device_id,
            success=False,
            error_message=message,
            error_code=error_code,
            execution_time_ms=(time.time() - start_time) * 1000,
        )

    async def _execute_on_device(
        self, device: Any, command: Command, access_level: AccessLevel = AccessLevel.READ
    ) -> dict[str, Any]:
        """Execute a supported command on a specific device."""
        if command.command_type is CommandType.READ:
            if not command.capability:
                raise ValueError("READ command requires 'capability'")
            self._check_capability_access(device, command.capability, access_level)
            return cast(dict[str, Any], await device.read(command.capability, **command.parameters))
        if command.command_type is CommandType.WRITE:
            if not command.capability:
                raise ValueError("WRITE command requires 'capability'")
            self._check_capability_access(device, command.capability, access_level)
            return cast(
                dict[str, Any], await device.write(command.capability, **command.parameters)
            )
        if command.command_type is CommandType.DISCOVER:
            return {"capabilities": await device.discover()}
        if command.command_type is CommandType.HEALTH_CHECK:
            return cast(dict[str, Any], await device.health_check())
        if command.command_type is CommandType.RESET:
            return {"reset": await device.reset()}
        if command.command_type is CommandType.DISCONNECT:
            await device.close()
            return {"disconnected": True}
        if command.command_type in self._UNSUPPORTED:
            raise UnsupportedCommandError(f"Command {command.command_type.name} is unsupported")
        raise ValueError(f"Unknown command type: {command.command_type}")

    @staticmethod
    def _check_capability_access(
        device: Any, capability_name: str, access_level: AccessLevel
    ) -> None:
        metadata = getattr(device, "metadata", None)
        capability = next(
            (
                item
                for item in getattr(metadata, "capabilities", ())
                if item.name == capability_name
            ),
            None,
        )
        if capability is None:
            return
        required = getattr(capability, "access", AccessLevel.READ)
        if not isinstance(required, AccessLevel):
            required = AccessLevel(required)
        if _access_rank(access_level) < _access_rank(required):
            raise PermissionError(
                f"Capability '{capability_name}' requires {required.value} access"
            )


def _access_rank(level: AccessLevel) -> int:
    return {AccessLevel.READ: 0, AccessLevel.WRITE: 1, AccessLevel.ADMIN: 2}[level]


__all__ = [
    "Command",
    "CommandType",
    "MHSProtocol",
    "Response",
    "UnsupportedCommandError",
]
