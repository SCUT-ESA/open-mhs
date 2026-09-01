import pytest

from openmhs.core.backend import BackendDevice, BackendPolicy
from openmhs.core.device import CapabilityError, DeviceCapability, DeviceMetadata, DeviceState
from openmhs.core.driver import Driver, DriverConfig


class FakeDevice(BackendDevice):
    def __init__(self, policy):
        super().__init__(
            DeviceMetadata(
                "fake",
                "fake",
                capabilities=[
                    DeviceCapability(
                        "set", schema={"value": {"type": "number", "minimum": 0, "maximum": 1}}
                    )
                ],
            ),
            policy,
        )

    async def _do_read(self, capability, **params):
        return {"value": params.get("value", 0), "nested": {"ok": True}}

    async def _do_write(self, capability, **params):
        return {"value": params["value"]}


@pytest.mark.asyncio
async def test_simulation_is_explicit_and_results_are_deepcopied():
    device = FakeDevice(BackendPolicy(simulation=True, backend_name="fake-sim"))
    assert await device.connect()
    assert device.state is DeviceState.SIMULATED
    result = await device.read("set", value=0.5)
    result["nested"]["ok"] = False
    again = await device.read("set", value=0.5)
    assert again["nested"]["ok"] is True
    assert again["backend"] == "fake-sim"
    assert again["simulated"] is True


@pytest.mark.asyncio
async def test_real_without_factory_fails_closed():
    device = FakeDevice(BackendPolicy(backend_name="missing"))
    assert not await device.connect()
    assert device.state is DeviceState.ERROR
    health = await device.health_check()
    assert health["healthy"] is False
    assert health["simulated"] is False


@pytest.mark.asyncio
async def test_schema_enforces_finite_and_range():
    device = FakeDevice(BackendPolicy(simulation=True))
    await device.connect()
    with pytest.raises(CapabilityError):
        await device.write("set", value=2)
    with pytest.raises(CapabilityError):
        await device.write("set", value=float("nan"))


@pytest.mark.asyncio
async def test_driver_connect_candidate_is_idempotent_and_cleans_repeat():
    class Candidate:
        def __init__(self):
            self.closed = 0

        async def connect(self):
            return True

        async def close(self):
            self.closed += 1

    candidates = []

    class TestDriver(Driver):
        async def connect(self):
            candidate = Candidate()
            candidates.append(candidate)
            return await self._connect_candidate(candidate)

    driver = TestDriver(DriverConfig("test", simulation=True))
    await driver.connect()
    first = driver.device
    await driver.connect()
    assert driver.device is first
    assert candidates[1].closed == 1
