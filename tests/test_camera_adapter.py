import pytest

from openmhs.adapters.camera import CameraDevice, CameraDriver
from openmhs.core.device import CapabilityError, DeviceState
from openmhs.core.driver import DriverConfig


class FakeVideoCapture:
    def __init__(self, opened: bool = True):
        self._opened = opened
        self.released = False
        self.properties = {}

    def isOpened(self) -> bool:
        return self._opened and not self.released

    def set(self, prop: int, value: float) -> bool:
        self.properties[prop] = value
        return True

    def get(self, prop: int) -> float:
        return self.properties.get(prop, 0.0)

    def read(self):
        if not self.isOpened():
            return False, None
        # Return fake frame as dummy bytes
        return True, b"fake_frame_bytes"

    def encode(self, frame, format, quality):
        return b"fake_encoded_" + format.encode()

    def release(self):
        self.released = True


@pytest.mark.asyncio
async def test_camera_simulation():
    dev = CameraDevice("cam_sim", simulation=True)
    assert await dev.connect()
    assert dev.state is DeviceState.SIMULATED

    # Capture frame in simulation
    frame = await dev.read("frame", format="jpg", quality=80)
    assert frame["format"] == "jpg"
    assert frame["simulated"] is True
    assert "image_base64" in frame or "error" in frame

    # Settings get & set
    settings = await dev.read("settings")
    assert "resolution" in settings
    assert settings["simulated"] is True

    new_settings = await dev.write("settings", brightness=80, contrast=70)
    assert new_settings["configured"] is True
    assert new_settings["settings"]["brightness"] == 80

    # Stream start & stop
    stream_res = await dev.write("stream", action="start", duration=0)
    assert stream_res["streaming"] is True

    stop_res = await dev.write("stream", action="stop")
    assert stop_res["streaming"] is False

    # Validation
    with pytest.raises(CapabilityError):
        await dev.read("frame", format="bmp")
    with pytest.raises(CapabilityError):
        await dev.read("frame", quality=150)
    with pytest.raises(CapabilityError):
        await dev.write("settings", brightness=150)
    with pytest.raises(CapabilityError):
        await dev.write("stream", action="pause")


@pytest.mark.asyncio
async def test_camera_real_without_factory_fails_closed():
    dev = CameraDevice("cam_real", simulation=False)
    assert not await dev.connect()
    assert dev.state is DeviceState.ERROR


@pytest.mark.asyncio
async def test_camera_real_with_fake_backend():
    cap = FakeVideoCapture(opened=True)
    dev = CameraDevice("cam_real", simulation=False, backend_factory=lambda: cap)
    assert await dev.connect()
    assert dev.state is DeviceState.ONLINE

    frame = await dev.read("frame", format="png", quality=90)
    assert frame["format"] == "png"
    assert frame["simulated"] is False
    assert "image_base64" in frame

    await dev.close()
    assert cap.released is True
    assert dev.state is DeviceState.OFFLINE


@pytest.mark.asyncio
async def test_camera_real_failed_open_releases():
    cap = FakeVideoCapture(opened=False)
    dev = CameraDevice("cam_failed", simulation=False, backend_factory=lambda: cap)
    assert not await dev.connect()
    assert dev.state is DeviceState.ERROR
    assert cap.released is True


@pytest.mark.asyncio
async def test_camera_driver_connect_and_disconnect():
    cap = FakeVideoCapture(opened=True)
    cfg = DriverConfig(driver_name="camera", simulation=False)
    drv = CameraDriver(cfg, backend_factory=lambda: cap)
    assert await drv.connect()
    assert drv.is_connected is True
    assert drv.device.state is DeviceState.ONLINE

    await drv.disconnect()
    assert drv.is_connected is False
    assert drv.device is None
    assert cap.released is True
