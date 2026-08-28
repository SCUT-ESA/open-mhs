"""MHS Protocol implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional
import json
import time


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
    SCRIPT = auto()  # Execute a pre-defined script


@dataclass
class Command:
    """An MHS command."""
    command_id: str
    command_type: CommandType
    device_id: str
    capability: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    auth_token: Optional[str] = None
    timeout: Optional[float] = None

    def to_json(self) -> str:
        return json.dumps({
            "command_id": self.command_id,
            "command_type": self.command_type.name,
            "device_id": self.device_id,
            "capability": self.capability,
            "parameters": self.parameters,
            "timestamp": self.timestamp,
            "timeout": self.timeout,
        })

    @classmethod
    def from_json(cls, data: str) -> Command:
        obj = json.loads(data)
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
    data: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    execution_time_ms: Optional[float] = None

    def to_json(self) -> str:
        return json.dumps({
            "command_id": self.command_id,
            "device_id": self.device_id,
            "success": self.success,
            "data": self.data,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
            "execution_time_ms": self.execution_time_ms,
        })

    @classmethod
    def from_json(cls, data: str) -> Response:
        obj = json.loads(data)
        return cls(
            command_id=obj["command_id"],
            device_id=obj["device_id"],
            success=obj["success"],
            data=obj.get("data", {}),
            error_message=obj.get("error_message"),
            timestamp=obj.get("timestamp", time.time()),
            execution_time_ms=obj.get("execution_time_ms"),
        )


class MHSProtocol:
    """MHS Protocol handler.
    
    Translates between MHS commands and device operations.
    """

    def __init__(self, registry: Optional["DeviceRegistry"] = None):
        from openmhs.core.registry import DeviceRegistry
        self.registry = registry or DeviceRegistry()

    async def execute(self, command: Command) -> Response:
        """Execute an MHS command."""
        start_time = time.time()
        
        device = self.registry.get_device(command.device_id)
        if device is None:
            return Response(
                command_id=command.command_id,
                device_id=command.device_id,
                success=False,
                error_message=f"Device '{command.device_id}' not found",
            )

        try:
            result = await self._execute_on_device(device, command)
            execution_time = (time.time() - start_time) * 1000
            return Response(
                command_id=command.command_id,
                device_id=command.device_id,
                success=True,
                data=result,
                execution_time_ms=execution_time,
            )
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            return Response(
                command_id=command.command_id,
                device_id=command.device_id,
                success=False,
                error_message=str(e),
                execution_time_ms=execution_time,
            )

    async def _execute_on_device(self, device: Any, command: Command) -> Dict[str, Any]:
        """Execute command on a specific device."""
        if command.command_type == CommandType.READ:
            if not command.capability:
                raise ValueError("READ command requires 'capability'")
            return await device.read(command.capability, **command.parameters)
        
        elif command.command_type == CommandType.WRITE:
            if not command.capability:
                raise ValueError("WRITE command requires 'capability'")
            return await device.write(command.capability, **command.parameters)
        
        elif command.command_type == CommandType.DISCOVER:
            capabilities = await device.discover()
            return {"capabilities": capabilities}
        
        elif command.command_type == CommandType.HEALTH_CHECK:
            return await device.health_check()
        
        elif command.command_type == CommandType.RESET:
            success = await device.reset()
            return {"reset": success}
        
        elif command.command_type == CommandType.CONNECT:
            # Connection is handled by driver
            return {"connected": True}
        
        elif command.command_type == CommandType.DISCONNECT:
            await device.close()
            return {"disconnected": True}
        
        elif command.command_type == CommandType.CONFIGURE:
            # Configuration is driver-specific
            return {"configured": True, "params": command.parameters}
        
        elif command.command_type == CommandType.SCRIPT:
            script = command.parameters.get("script", "")
            return {"script_executed": True, "script": script}
        
        else:
            raise ValueError(f"Unknown command type: {command.command_type}")
