import pytest

from openmhs.adapters.lab import (
    LaserDevice,
    LaserDriver,
    LiquidHandlerDevice,
    LiquidHandlerDriver,
    MicroscopeDevice,
    MicroscopeDriver,
)
from openmhs.adapters.raspberry_pi import (
    PiCameraDevice,
    PiCameraDriver,
)
from openmhs.adapters.robots import (
    Printer3DDevice,
    Printer3DDriver,
    RobotArmDevice,
    RobotArmDriver,
)
from openmhs.adapters.sensors import (
    AnalogSensorDevice,
    AnalogSensorDriver,
    BME280Device,
    BME280Driver,
    HCSR04Device,
    HCSR04Driver,
)
from openmhs.adapters.smart_home import (
    MQTTDevice,
    MQTTDriver,
    SmartPlugDevice,
    SmartPlugDriver,
)
from openmhs.core.device import CapabilityError, DeviceState
from openmhs.core.driver import DriverConfig


@pytest.mark.parametrize(
    "device_cls,driver_cls,driver_name",
    [
        (BME280Device, BME280Driver, "bme280"),
        (HCSR04Device, HCSR04Driver, "hcsr04"),
        (AnalogSensorDevice, AnalogSensorDriver, "analog_sensor"),
        (MQTTDevice, MQTTDriver, "mqtt_device"),
        (SmartPlugDevice, SmartPlugDriver, "smart_plug"),
        (PiCameraDevice, PiCameraDriver, "raspberry_pi_camera"),
        (MicroscopeDevice, MicroscopeDriver, "microscope"),
        (LiquidHandlerDevice, LiquidHandlerDriver, "liquid_handler"),
        (LaserDevice, LaserDriver, "laser"),
        (RobotArmDevice, RobotArmDriver, "robot_arm"),
        (Printer3DDevice, Printer3DDriver, "3d_printer"),
    ],
)
@pytest.mark.asyncio
async def test_simulation_only_adapters_fail_closed_on_real_and_succeed_on_simulation(
    device_cls, driver_cls, driver_name
):
    # 1. Real mode (default) -> fails closed
    dev_real = device_cls("dev1", simulation=False)
    assert not await dev_real.connect()
    assert dev_real.state is DeviceState.ERROR
    health_real = await dev_real.health_check()
    assert health_real["healthy"] is False
    assert health_real["simulated"] is False

    # Driver in real mode -> fails closed
    cfg_real = DriverConfig(driver_name=driver_name, simulation=False)
    drv_real = driver_cls(cfg_real)
    assert not await drv_real.connect()
    assert drv_real.is_connected is False
    assert drv_real.device is None

    # 2. Simulation mode -> connects as SIMULATED
    dev_sim = device_cls("dev1", simulation=True)
    assert await dev_sim.connect()
    assert dev_sim.state is DeviceState.SIMULATED
    health_sim = await dev_sim.health_check()
    assert health_sim["healthy"] is True
    assert health_sim["simulated"] is True
    assert "simulated" in dev_sim.metadata.tags

    # Driver in simulation mode -> connects
    cfg_sim = DriverConfig(driver_name=driver_name, simulation=True)
    drv_sim = driver_cls(cfg_sim)
    assert await drv_sim.connect()
    assert drv_sim.is_connected is True
    assert drv_sim.device.state is DeviceState.SIMULATED


@pytest.mark.asyncio
async def test_mqtt_device_values_and_deepcopy():
    dev = MQTTDevice("mqtt1", simulation=True)
    await dev.connect()

    # Initial status
    status = await dev.read("status")
    assert status["power"] == "off"
    assert status["brightness"] == 100
    assert status["color"] == {"r": 255, "g": 255, "b": 255}
    assert status["simulated"] is True

    # Mutating read result must not mutate internal state
    status["color"]["r"] = 0
    status_again = await dev.read("status")
    assert status_again["color"]["r"] == 255

    # Write power
    res = await dev.write("power", state="on")
    assert res["power"] == "on"
    assert res["simulated"] is True

    # Write brightness
    res_b = await dev.write("brightness", level=75)
    assert res_b["brightness"] == 75

    # Write color
    res_c = await dev.write("color", r=100, g=150, b=200)
    assert res_c["color"] == {"r": 100, "g": 150, "b": 200}

    # Validation errors
    with pytest.raises(CapabilityError):
        await dev.write("power", state="invalid")
    with pytest.raises(CapabilityError):
        await dev.write("brightness", level=150)
    with pytest.raises(CapabilityError):
        await dev.write("color", r=-1, g=0, b=0)


@pytest.mark.asyncio
async def test_smart_plug_simulation():
    dev = SmartPlugDevice("plug1", simulation=True)
    await dev.connect()

    usage_off = await dev.read("power_usage")
    assert usage_off["power_watts"] == 0.5

    await dev.write("power", state="on")
    usage_on = await dev.read("power_usage")
    assert usage_on["power_watts"] > 40.0

    with pytest.raises(CapabilityError):
        await dev.write("power", state="toggle")


@pytest.mark.asyncio
async def test_robot_arm_validation_and_cancellation():
    dev = RobotArmDevice("arm1", dof=6, simulation=True)
    await dev.connect()

    # Joint position with correct length
    res = await dev.write("joint_position", joints=[10.0, 20.0, 30.0, 40.0, 50.0, 60.0], speed=80)
    assert res["joints"] == [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    assert res["speed"] == 80

    # Mutating returned joints must not mutate device state
    res["joints"][0] = 999.0
    status = await dev.read("status")
    assert status["joints"][0] == 10.0

    # Wrong DOF count
    with pytest.raises(CapabilityError):
        await dev.write("joint_position", joints=[10.0, 20.0])

    # Non-finite joint
    with pytest.raises(CapabilityError):
        await dev.write("joint_position", joints=[10.0, float("nan"), 30.0, 40.0, 50.0, 60.0])

    # Speed out of range
    with pytest.raises(CapabilityError):
        await dev.write("joint_position", joints=[0.0] * 6, speed=150)

    # Cartesian position validation
    with pytest.raises(CapabilityError):
        await dev.write("cartesian_position", x=1000.0)

    # Gripper validation
    with pytest.raises(CapabilityError):
        await dev.write("gripper", position=150.0)


@pytest.mark.asyncio
async def test_printer_3d_validation_and_safety():
    dev = Printer3DDevice("printer1", simulation=True)
    await dev.connect()

    res = await dev.write("temperature", nozzle=210.0, bed=60.0)
    assert res["nozzle"] == 210.0
    assert res["bed"] == 60.0

    # Temperature out of bounds
    with pytest.raises(CapabilityError):
        await dev.write("temperature", nozzle=400.0)

    with pytest.raises(CapabilityError):
        await dev.write("temperature", bed=150.0)

    # Print action validation
    with pytest.raises(CapabilityError):
        await dev.write("print", action="destroy")

    # Home axis validation
    with pytest.raises(CapabilityError):
        await dev.write("home_axis", axis="w")

    # Close resets heating and printing
    await dev.write("print", action="start")
    assert dev._printing is True
    await dev.close()
    assert dev._printing is False
    assert dev._nozzle_temp == 25.0
    assert dev._bed_temp == 25.0


@pytest.mark.asyncio
async def test_laser_device_validation_and_close():
    dev = LaserDevice("laser1", simulation=True)
    await dev.connect()

    res = await dev.write("power", power=50.0)
    assert res["power"] == 50.0
    assert res["enabled"] is True

    # Power out of bounds
    with pytest.raises(CapabilityError):
        await dev.write("power", power=150.0)
    with pytest.raises(CapabilityError):
        await dev.write("power", power=float("nan"))

    # Alignment validation
    with pytest.raises(CapabilityError):
        await dev.write("align", method="magic")

    # Close turns off power
    await dev.close()
    assert dev._power == 0.0
    assert dev._enabled is False


@pytest.mark.asyncio
async def test_liquid_handler_validation():
    dev = LiquidHandlerDevice("lh1", simulation=True)
    await dev.connect()

    # Status capability works
    st = await dev.read("status")
    assert st["ready"] is True
    assert st["position"] == {"x": 0, "y": 0, "z": 0}

    # Volume out of range
    with pytest.raises(CapabilityError):
        await dev.write("aspirate", volume=2000.0)

    # Cycles must be non-negative
    with pytest.raises(CapabilityError):
        await dev.write("wash", cycles=-1)


@pytest.mark.asyncio
async def test_microscope_validation():
    dev = MicroscopeDevice("micro1", simulation=True)
    await dev.connect()

    cap = await dev.read("capture", exposure=50, gain=20)
    assert cap["captured"] is True

    with pytest.raises(CapabilityError):
        await dev.read("capture", gain=150)
