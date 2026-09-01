import pytest
from fastapi.testclient import TestClient

from openmhs.api.server import create_app
from openmhs.core.backend import BackendDevice, BackendPolicy
from openmhs.core.device import DeviceCapability, DeviceMetadata
from openmhs.core.registry import DeviceRegistry
from openmhs.transport.security import (
    SecurityConfigurationError,
    validate_host_security,
)


class DummyDevice(BackendDevice):
    def __init__(self, device_id="dummy_001"):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="dummy",
            capabilities=[
                DeviceCapability(
                    name="measure",
                    description="Measure value",
                    parameters={"channel": {"type": "integer"}},
                    read_only=True,
                ),
                DeviceCapability(
                    name="set_level",
                    description="Set level",
                    parameters={"level": {"type": "number"}},
                    read_only=False,
                ),
            ],
            tags=["test", "dummy"],
        )
        super().__init__(metadata, BackendPolicy(simulation=True, backend_name="dummy"))

    async def _do_read(self, capability: str, **params):
        if capability == "measure":
            return {"channel": params.get("channel", 1), "value": 42.0}
        return {"error": "unknown"}

    async def _do_write(self, capability: str, **params):
        if capability == "set_level":
            return {"level": params.get("level", 0.0)}
        return {"error": "unknown"}


@pytest.fixture
def app_and_registry():
    registry = DeviceRegistry()
    app = create_app(registry, manage_discovery=False, is_local=True)
    return app, registry


def test_api_root(app_and_registry):
    app, _ = app_and_registry
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["message"] == "Open MHS API"


@pytest.mark.asyncio
async def test_api_devices_crud(app_and_registry):
    app, registry = app_and_registry
    dev = DummyDevice("scope_1")
    await dev.connect()
    await registry.register(dev)

    client = TestClient(app)

    # 1. List devices
    resp = client.get("/devices")
    assert resp.status_code == 200
    devices = resp.json()
    assert len(devices) == 1
    assert devices[0]["device_id"] == "scope_1"

    # 2. Get device
    resp = client.get("/devices/scope_1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["device_id"] == "scope_1"
    assert len(data["capabilities"]) == 2

    # 3. Read capability
    resp = client.post("/devices/scope_1/read/measure", json={"params": {"channel": 2}})
    assert resp.status_code == 200
    assert resp.json()["value"] == 42.0

    # 4. Write capability
    resp = client.post("/devices/scope_1/write/set_level", json={"params": {"level": 10.5}})
    assert resp.status_code == 200
    assert resp.json()["level"] == 10.5

    # 5. Health checks
    resp = client.get("/devices/scope_1/health")
    assert resp.status_code == 200
    assert resp.json()["healthy"] is True

    resp = client.get("/health")
    assert resp.status_code == 200
    assert "scope_1" in resp.json()

    # 6. Reset
    resp = client.post("/devices/scope_1/reset")
    assert resp.status_code == 200


def test_api_404_on_missing_device(app_and_registry):
    app, _ = app_and_registry
    client = TestClient(app)
    resp = client.get("/devices/nonexistent")
    assert resp.status_code == 404

    resp = client.post("/devices/nonexistent/read/measure", json={"params": {}})
    assert resp.status_code == 404


def test_api_security_bearer_token():
    registry = DeviceRegistry()
    app = create_app(registry, manage_discovery=False, is_local=False, token="secret123")
    client = TestClient(app)

    # Missing token -> 401
    resp = client.get("/devices")
    assert resp.status_code == 401
    assert "Bearer" in resp.headers.get("WWW-Authenticate", "")

    # Invalid token -> 401
    resp = client.get("/devices", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401

    # Valid token -> 200
    resp = client.get("/devices", headers={"Authorization": "Bearer secret123"})
    assert resp.status_code == 200


def test_host_security_validation():
    # Loopback is always allowed
    validate_host_security("127.0.0.1")
    validate_host_security("localhost")

    # Non-loopback without token -> error
    with pytest.raises(SecurityConfigurationError, match="requires a Bearer token"):
        validate_host_security("0.0.0.0", token=None)

    # Non-loopback with token but no TLS / insecure flag -> error
    with pytest.raises(SecurityConfigurationError, match="TLS reverse proxy"):
        validate_host_security("0.0.0.0", token="secret", behind_tls=False, allow_insecure=False)

    # Non-loopback with token and allow_insecure -> allowed
    validate_host_security("0.0.0.0", token="secret", allow_insecure=True)
    validate_host_security("0.0.0.0", token="secret", behind_tls=True)
