"""REST API server for MHS.

Provides HTTP endpoints for device control and monitoring.
"""

from __future__ import annotations

import contextlib
import math
import uuid
from typing import Any

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, status
    from pydantic import BaseModel, Field

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from openmhs.adapters.visa.discovery import VisaDiscoveryManager
from openmhs.core.device import AccessLevel
from openmhs.core.protocol import Command, CommandType, MHSProtocol
from openmhs.core.registry import DeviceRegistry
from openmhs.transport.security import (
    get_bearer_token,
    get_request_access_level,
)

if HAS_FASTAPI:

    class ReadParamsModel(BaseModel):
        params: dict[str, Any] = Field(default_factory=dict)

        model_config = {"extra": "forbid"}

    class WriteParamsModel(BaseModel):
        params: dict[str, Any] = Field(default_factory=dict)

        model_config = {"extra": "forbid"}


def _validate_finite_dict(d: dict[str, Any]) -> None:
    for k, v in d.items():
        if isinstance(v, float) and not math.isfinite(v):
            raise HTTPException(
                status_code=422,
                detail=f"Parameter '{k}' must be a finite number",
            )
        elif isinstance(v, dict):
            _validate_finite_dict(v)


def _map_protocol_error_to_http(error_code: str | None, error_message: str | None) -> HTTPException:
    msg = error_message or "Operation failed"
    code_map = {
        "device_not_found": status.HTTP_404_NOT_FOUND,
        "capability_not_found": status.HTTP_404_NOT_FOUND,
        "capability_denied": status.HTTP_400_BAD_REQUEST,
        "access_denied": status.HTTP_403_FORBIDDEN,
        "safety_violation": status.HTTP_400_BAD_REQUEST,
        "invalid_command": 422,
        "unsupported_command": status.HTTP_501_NOT_IMPLEMENTED,
        "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
        "device_error": status.HTTP_503_SERVICE_UNAVAILABLE,
        "internal_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    status_code = code_map.get(error_code or "", status.HTTP_400_BAD_REQUEST)
    return HTTPException(status_code=status_code, detail=msg)


def create_app(
    registry: DeviceRegistry | None = None,
    discovery_manager: VisaDiscoveryManager | None = None,
    *,
    token: str | None = None,
    is_local: bool = True,
    manage_discovery: bool = True,
) -> Any:
    """Create FastAPI application for MHS."""
    if not HAS_FASTAPI:
        raise ImportError("fastapi not installed. Install with: pip install openmhs[api]")

    reg = registry or DeviceRegistry()
    protocol = MHSProtocol(reg)
    expected_token = token if token is not None else get_bearer_token()

    manager = discovery_manager
    if manager is None and manage_discovery:
        manager = VisaDiscoveryManager(reg)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        if manager is not None and manage_discovery:
            manager.start()
        try:
            yield
        finally:
            if manager is not None and manage_discovery:
                await manager.close()

    app = FastAPI(
        title="Open MHS API",
        description="Open Model Hardware Standard REST API",
        version="0.1.0",
        lifespan=lifespan,
    )

    async def get_access_level(
        authorization: str | None = Header(default=None),
    ) -> AccessLevel:
        level = get_request_access_level(
            authorization, is_local=is_local, expected_token=expected_token
        )
        if level is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: valid Bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return level

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {"message": "Open MHS API", "version": "0.1.0"}

    @app.get("/devices")
    async def list_devices(
        _access: AccessLevel = Depends(get_access_level),  # noqa: B008
    ) -> list[dict[str, Any]]:
        """List all registered devices."""
        return reg.get_metadata_summary()

    @app.get("/devices/{device_id}")
    async def get_device(
        device_id: str, _access: AccessLevel = Depends(get_access_level)  # noqa: B008
    ) -> dict[str, Any]:
        """Get device details."""
        device = reg.get_device(device_id)
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
                {
                    "name": c.name,
                    "description": c.description,
                    "readable": getattr(c, "readable", not c.read_only),
                    "writable": getattr(c, "writable", not c.read_only),
                    "read_only": c.read_only,
                }
                for c in meta.capabilities
            ],
            "safety_limits": [
                {
                    "parameter": s.parameter,
                    "min": s.min_value,
                    "max": s.max_value,
                    "unit": s.unit,
                }
                for s in meta.safety_limits
            ],
            "tags": meta.tags,
            "location": meta.location,
            "description": meta.natural_language_description,
        }

    @app.post("/devices/{device_id}/read/{capability}")
    async def read_device(
        device_id: str,
        capability: str,
        body: ReadParamsModel | None = None,
        access: AccessLevel = Depends(get_access_level),  # noqa: B008
    ) -> dict[str, Any]:
        """Read from a device capability."""
        params = body.params if body is not None else {}
        _validate_finite_dict(params)
        command = Command(
            command_id=f"api_{uuid.uuid4().hex[:12]}",
            command_type=CommandType.READ,
            device_id=device_id,
            capability=capability,
            parameters=params,
        )
        response = await protocol.execute(command, access_level=access)
        if not response.success:
            raise _map_protocol_error_to_http(response.error_code, response.error_message)
        return response.data

    @app.post("/devices/{device_id}/write/{capability}")
    async def write_device(
        device_id: str,
        capability: str,
        body: WriteParamsModel,
        access: AccessLevel = Depends(get_access_level),  # noqa: B008
    ) -> dict[str, Any]:
        """Write to a device capability."""
        params = body.params
        _validate_finite_dict(params)
        command = Command(
            command_id=f"api_{uuid.uuid4().hex[:12]}",
            command_type=CommandType.WRITE,
            device_id=device_id,
            capability=capability,
            parameters=params,
        )
        response = await protocol.execute(command, access_level=access)
        if not response.success:
            raise _map_protocol_error_to_http(response.error_code, response.error_message)
        return response.data

    @app.get("/devices/{device_id}/discover")
    async def discover_capabilities(
        device_id: str, _access: AccessLevel = Depends(get_access_level)  # noqa: B008
    ) -> dict[str, Any]:
        """Discover device capabilities."""
        device = reg.get_device(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

        capabilities = await device.discover()
        return {"device_id": device_id, "capabilities": capabilities}

    @app.post("/devices/{device_id}/reset")
    async def reset_device(
        device_id: str, access: AccessLevel = Depends(get_access_level)  # noqa: B008
    ) -> dict[str, Any]:
        """Reset a device."""
        command = Command(
            command_id=f"api_{uuid.uuid4().hex[:12]}",
            command_type=CommandType.RESET,
            device_id=device_id,
        )
        response = await protocol.execute(command, access_level=access)
        if not response.success:
            raise _map_protocol_error_to_http(response.error_code, response.error_message)
        return response.data

    @app.get("/devices/{device_id}/health")
    async def health_check(
        device_id: str, _access: AccessLevel = Depends(get_access_level)  # noqa: B008
    ) -> dict[str, Any]:
        """Check device health."""
        device = reg.get_device(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

        health = await device.health_check()
        return health

    @app.get("/health")
    async def global_health(
        _access: AccessLevel = Depends(get_access_level),  # noqa: B008
    ) -> dict[str, Any]:
        """Check health of all devices."""
        return await reg.health_check_all()

    return app
