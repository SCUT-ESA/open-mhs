"""Camera adapters for MHS.

Supports:
- USB webcams (via OpenCV)
- Raspberry Pi Camera Module
- IP cameras (RTSP/HTTP)
- Simulated camera (for testing)
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from typing import Any, Dict, Optional

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from openmhs.core.device import (
    BaseDevice, DeviceCapability, DeviceMetadata, DeviceState, SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class CameraDevice(BaseDevice):
    """Generic camera device."""

    def __init__(
        self,
        device_id: str,
        source: str = "0",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
    ):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="camera",
            manufacturer="Generic",
            model="USB Camera",
            capabilities=[
                DeviceCapability(
                    name="frame",
                    description="Capture a single image frame",
                    parameters={"format": "jpg/png", "quality": "0-100"},
                    read_only=True,
                ),
                DeviceCapability(
                    name="stream",
                    description="Start/stop video streaming",
                    parameters={"duration": "seconds", "fps": "frames per second"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="settings",
                    description="Get or set camera settings",
                    parameters={
                        "resolution": "WxH",
                        "brightness": "0-100",
                        "contrast": "0-100",
                        "exposure": "auto/manual",
                    },
                    read_only=False,
                ),
            ],
            safety_limits=[
                SafetyLimit("brightness", 0, 100, "%", "Brightness range"),
                SafetyLimit("contrast", 0, 100, "%", "Contrast range"),
            ],
            tags=["camera", "vision", "imaging", "sensor"],
            natural_language_description=(
                "A camera device capable of capturing still images and video streams. "
                "Can be used for visual inspection, monitoring, or computer vision tasks."
            ),
            driver_class="camera",
            connection_info={"source": source, "resolution": f"{width}x{height}", "fps": fps},
        )
        super().__init__(metadata)
        self._source = source
        self._width = width
        self._height = height
        self._fps = fps
        self._cap = None
        self._streaming = False
        self._simulation_mode = not HAS_OPENCV

    async def connect(self) -> bool:
        if self._simulation_mode:
            self._set_state(DeviceState.ONLINE)
            return True
        try:
            self._cap = cv2.VideoCapture(int(self._source) if self._source.isdigit() else self._source)
            if self._cap.isOpened():
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
                self._cap.set(cv2.CAP_PROP_FPS, self._fps)
                self._set_state(DeviceState.ONLINE)
                return True
            else:
                self._set_state(DeviceState.ERROR)
                return False
        except Exception:
            self._set_state(DeviceState.ERROR)
            return False

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "frame":
            return await self._capture_frame(**params)
        elif capability == "settings":
            return self._get_settings()
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "settings":
            return self._set_settings(**params)
        elif capability == "stream":
            return await self._control_stream(**params)
        return {"error": f"Unknown capability: {capability}"}

    async def _capture_frame(self, format: str = "jpg", quality: int = 85) -> Dict[str, Any]:
        if self._simulation_mode:
            # Generate a simulated image
            if HAS_PIL:
                img = Image.new('RGB', (self._width, self._height), color=(73, 109, 137))
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=quality)
                img_b64 = base64.b64encode(buffer.getvalue()).decode()
                return {
                    "image_base64": img_b64,
                    "format": "jpg",
                    "width": self._width,
                    "height": self._height,
                    "timestamp": time.time(),
                    "simulated": True,
                }
            else:
                return {"error": "PIL not available for simulation", "simulated": True}
        
        if self._cap is None or not self._cap.isOpened():
            return {"error": "Camera not connected"}
        
        ret, frame = self._cap.read()
        if not ret:
            return {"error": "Failed to capture frame"}
        
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        _, buffer = cv2.imencode(".jpg", frame, encode_param)
        img_b64 = base64.b64encode(buffer).decode()
        
        return {
            "image_base64": img_b64,
            "format": "jpg",
            "width": frame.shape[1],
            "height": frame.shape[0],
            "timestamp": time.time(),
        }

    def _get_settings(self) -> Dict[str, Any]:
        if self._simulation_mode:
            return {
                "resolution": f"{self._width}x{self._height}",
                "fps": self._fps,
                "brightness": 50,
                "contrast": 50,
                "exposure": "auto",
            }
        if self._cap is None:
            return {"error": "Camera not connected"}
        return {
            "resolution": f"{int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}",
            "fps": self._cap.get(cv2.CAP_PROP_FPS),
            "brightness": self._cap.get(cv2.CAP_PROP_BRIGHTNESS),
            "contrast": self._cap.get(cv2.CAP_PROP_CONTRAST),
        }

    def _set_settings(self, **params: Any) -> Dict[str, Any]:
        if self._simulation_mode:
            if "resolution" in params:
                w, h = params["resolution"].split("x")
                self._width = int(w)
                self._height = int(h)
            if "fps" in params:
                self._fps = int(params["fps"])
            return {"configured": True, "settings": params}
        
        if self._cap is None:
            return {"error": "Camera not connected"}
        
        if "brightness" in params:
            self._cap.set(cv2.CAP_PROP_BRIGHTNESS, params["brightness"])
        if "contrast" in params:
            self._cap.set(cv2.CAP_PROP_CONTRAST, params["contrast"])
        
        return {"configured": True, "settings": params}

    async def _control_stream(self, action: str = "start", duration: int = 10, **params: Any) -> Dict[str, Any]:
        if action == "start":
            self._streaming = True
            return {"streaming": True, "duration": duration}
        elif action == "stop":
            self._streaming = False
            return {"streaming": False}
        return {"error": f"Unknown stream action: {action}"}

    async def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        await super().close()


@register_driver
class CameraDriver(Driver):
    """Driver for USB/IP cameras."""

    DRIVER_NAME = "camera"
    SUPPORTED_DEVICES = ["camera", "webcam", "ip_camera"]

    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._source = config.connection_params.get("source", "0")
        self._width = config.connection_params.get("width", 640)
        self._height = config.connection_params.get("height", 480)
        self._fps = config.connection_params.get("fps", 30)

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "camera_001")
        self._device = CameraDevice(device_id, self._source, self._width, self._height, self._fps)
        result = await self._device.connect()
        self._connected = result
        return result
