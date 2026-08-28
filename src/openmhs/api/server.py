"""REST API server for MHS.

Provides HTTP endpoints for device control and monitoring.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from openmhs.core.device import DeviceState
from openmhs.core.protocol import Command, CommandType, MHSProtocol
from openmhs.core.registry import DeviceRegistry


def create_app(registry: Optional[DeviceRegistry] = None) -> Any:
    """Create FastAPI application for MHS."""
    if not HAS_FASTAPI:
        raise ImportError("fastapi not installed. Install with: pip install openmhs[api]")

    registry = registry or DeviceRegistry()
    protocol = MHSProtocol(registry)
    app = FastAPI(
        title="Open MHS API",
        description="Open Model Hardware Standard REST API",
        version="0.1.0",
    )

    @app.get("/")
    async def root():
        return {"message": "Open MHS API", "version": "0.1.0"}

    @app.get("/devices")
    async def list_devices():
        """List all registered devices."""
        return registry.get_metadata_summary()

    @app.get("/devices/{device_id}")
    async def get_device(device_id: str):
        """Get device details."""
        device = registry.get_device(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
        
        meta = device.metadata
        return {
            "device_id": meta.device_id,
            "device_type": meta.device_type,
            "manufacturer": meta.manufacturer,
            "model": meta.model,
            "state": device.state.name,
            "capabilities": [
                {"name": c.name, "description": c.description, "read_only": c.read_only}
                for c in meta.capabilities
            ],
            "safety_limits": [
                {"parameter": s.parameter, "min": s.min_value, "max": s.max_value, "unit": s.unit}
                for s in meta.safety_limits
            ],
            "tags": meta.tags,
            "location": meta.location,
            "description": meta.natural_language_description,
        }

    @app.post("/devices/{device_id}/read/{capability}")
    async def read_device(device_id: str, capability: str, params: Optional[Dict[str, Any]] = None):
        """Read from a device capability."""
        command = Command(
            command_id=f"api_{device_id}_{capability}",
            command_type=CommandType.READ,
            device_id=device_id,
            capability=capability,
            parameters=params or {},
        )
        response = await protocol.execute(command)
        
        if not response.success:
            raise HTTPException(status_code=400, detail=response.error_message)
        return response.data

    @app.post("/devices/{device_id}/write/{capability}")
    async def write_device(device_id: str, capability: str, params: Dict[str, Any]):
        """Write to a device capability."""
        command = Command(
            command_id=f"api_{device_id}_{capability}",
            command_type=CommandType.WRITE,
            device_id=device_id,
            capability=capability,
            parameters=params,
        )
        response = await protocol.execute(command)
        
        if not response.success:
            raise HTTPException(status_code=400, detail=response.error_message)
        return response.data

    @app.get("/devices/{device_id}/discover")
    async def discover_capabilities(device_id: str):
        """Discover device capabilities."""
        device = registry.get_device(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
        
        capabilities = await device.discover()
        return {"device_id": device_id, "capabilities": capabilities}

    @app.post("/devices/{device_id}/reset")
    async def reset_device(device_id: str):
        """Reset a device."""
        command = Command(
            command_id=f"api_reset_{device_id}",
            command_type=CommandType.RESET,
            device_id=device_id,
        )
        response = await protocol.execute(command)
        
        if not response.success:
            raise HTTPException(status_code=400, detail=response.error_message)
        return response.data

    @app.get("/devices/{device_id}/health")
    async def health_check(device_id: str):
        """Check device health."""
        device = registry.get_device(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
        
        health = await device.health_check()
        return health

    @app.get("/health")
    async def global_health():
        """Check health of all devices."""
        return await registry.health_check_all()

    return app
