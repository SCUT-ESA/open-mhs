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
import math
import time
from typing import Any

try:
    import cv2  # noqa: F401

    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from openmhs.core.backend import BackendDevice, BackendPolicy
from openmhs.core.device import (
    CapabilityError,
    DeviceCapability,
    DeviceMetadata,
    DeviceState,
    SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class CameraDevice(BackendDevice):
    """Generic camera device."""

    def __init__(
        self,
        device_id: str,
        source: str = "0",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        *,
        simulation: bool = False,
        backend_factory: Any = None,
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
                    parameters={
                        "action": "start/stop",
                        "duration": "seconds",
                        "fps": "frames per second",
                    },
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
                        "fps": "frames per second",
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
        super().__init__(
            metadata,
            BackendPolicy(
                simulation=simulation,
                backend_factory=backend_factory,
                backend_name="camera",
                supported=True,
            ),
        )
        self._source = source
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: Any = None
        self._streaming = False
        self._brightness = 50
        self._contrast = 50
        self._exposure = "auto"
        self._simulation_mode = self.simulated
        self._backend_factory = backend_factory

    def _strict_validate(self, capability_name: str, params: dict[str, Any]) -> None:
        super()._strict_validate(capability_name, params)
        if capability_name == "frame":
            fmt_value = params.get("format", "jpg")
            fmt = fmt_value.lower().lstrip(".") if isinstance(fmt_value, str) else ""
            if fmt not in {"jpg", "jpeg", "png"}:
                raise CapabilityError("camera format must be jpg or png")
            quality = params.get("quality", 85)
            if not isinstance(quality, int) or isinstance(quality, bool) or not 0 <= quality <= 100:
                raise CapabilityError("camera quality must be an integer from 0 to 100")
        if capability_name == "stream":
            if params.get("action", "start") not in {"start", "stop"}:
                raise CapabilityError("stream action must be start or stop")
            for name, minimum in (("duration", 0), ("fps", 1)):
                if name in params and (
                    not isinstance(params[name], (int, float))
                    or isinstance(params[name], bool)
                    or not math.isfinite(float(params[name]))
                    or params[name] < minimum
                ):
                    raise CapabilityError(f"{name} is outside its safe range")
        if capability_name == "settings":
            if "resolution" in params:
                try:
                    width, height = (int(item) for item in params["resolution"].lower().split("x"))
                except (TypeError, ValueError):
                    raise CapabilityError("resolution must be WxH") from None
                if width <= 0 or height <= 0:
                    raise CapabilityError("resolution dimensions must be positive")
            if "fps" in params and (
                not isinstance(params["fps"], (int, float))
                or isinstance(params["fps"], bool)
                or not math.isfinite(float(params["fps"]))
                or params["fps"] <= 0
            ):
                raise CapabilityError("fps must be a positive finite number")
            for name in ("brightness", "contrast"):
                if name in params and (
                    not isinstance(params[name], (int, float))
                    or isinstance(params[name], bool)
                    or not 0 <= params[name] <= 100
                ):
                    raise CapabilityError(f"{name} must be between 0 and 100")

    async def connect(self) -> bool:
        if self._simulation_mode:
            return await super().connect()
        if self._backend_factory is None:
            self._set_state(DeviceState.ERROR)
            return False
        try:
            self._cap = await asyncio.to_thread(self._backend_factory)
            opened = await asyncio.to_thread(self._cap.isOpened)
            if not opened:
                await asyncio.to_thread(self._cap.release)
                self._cap = None
                self._set_state(DeviceState.ERROR)
                return False
            cv_module = globals().get("cv2")
            if cv_module is not None:
                await asyncio.to_thread(self._cap.set, cv_module.CAP_PROP_FRAME_WIDTH, self._width)
                await asyncio.to_thread(
                    self._cap.set, cv_module.CAP_PROP_FRAME_HEIGHT, self._height
                )
                await asyncio.to_thread(self._cap.set, cv_module.CAP_PROP_FPS, self._fps)
            self._backend = self._cap
            self._set_state(DeviceState.ONLINE)
            return True
        except asyncio.CancelledError:
            if self._cap is not None:
                await asyncio.shield(asyncio.to_thread(self._cap.release))
                self._cap = None
            self._set_state(DeviceState.ERROR)
            raise
        except Exception:
            if self._cap is not None:
                await asyncio.to_thread(self._cap.release)
                self._cap = None
            self._set_state(DeviceState.ERROR)
            return False

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "frame":
            return await self._capture_frame(**params)
        elif capability == "settings":
            return await asyncio.to_thread(self._get_settings)
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "settings":
            return await asyncio.to_thread(self._set_settings, **params)
        elif capability == "stream":
            return await self._control_stream(**params)
        return {"error": f"Unknown capability: {capability}"}

    async def _capture_frame(self, format: str = "jpg", quality: int = 85) -> dict[str, Any]:
        if self._simulation_mode:
            # Generate a simulated image
            output_format = format.lower().lstrip(".")
            if output_format not in {"jpg", "jpeg", "png"}:
                return {"error": "Unsupported image format", "simulated": True}
            if HAS_PIL:
                img = Image.new("RGB", (self._width, self._height), color=(73, 109, 137))
                buffer = io.BytesIO()
                img.save(
                    buffer, format="PNG" if output_format == "png" else "JPEG", quality=quality
                )
                img_b64 = base64.b64encode(buffer.getvalue()).decode()
            else:
                img_b64 = base64.b64encode(
                    f"SIMULATED_FRAME_{output_format}_{self._width}x{self._height}".encode()
                ).decode()
            return {
                "image_base64": img_b64,
                "format": output_format,
                "width": self._width,
                "height": self._height,
                "timestamp": time.time(),
                "simulated": True,
            }

        if self._cap is None or not await asyncio.to_thread(self._cap.isOpened):
            return {"error": "Camera not connected"}

        ret, frame = await asyncio.to_thread(self._cap.read)
        if not ret:
            return {"error": "Failed to capture frame"}
        output_format = format.lower().lstrip(".")
        if output_format not in {"jpg", "jpeg", "png"}:
            return {"error": "Unsupported image format"}
        extension = ".png" if output_format == "png" else ".jpg"
        cv_module = globals().get("cv2")
        if cv_module is not None:
            encode_param = (
                [] if extension == ".png" else [int(cv_module.IMWRITE_JPEG_QUALITY), quality]
            )
            ok, buffer = await asyncio.to_thread(cv_module.imencode, extension, frame, encode_param)
            if not ok:
                return {"error": "Failed to encode frame"}
            encoded = bytes(buffer)
        elif isinstance(frame, (bytes, bytearray)):
            encoded = bytes(frame)
        elif hasattr(self._cap, "encode"):
            encoded = await asyncio.to_thread(self._cap.encode, frame, output_format, quality)
        else:
            return {"error": "No image encoder available"}
        img_b64 = base64.b64encode(encoded).decode()
        shape = getattr(frame, "shape", None)
        width = shape[1] if shape is not None else self._width
        height = shape[0] if shape is not None else self._height

        return {
            "image_base64": img_b64,
            "format": output_format,
            "width": width,
            "height": height,
            "timestamp": time.time(),
        }

    def _get_settings(self) -> dict[str, Any]:
        if self._simulation_mode:
            return {
                "resolution": f"{self._width}x{self._height}",
                "fps": self._fps,
                "brightness": self._brightness,
                "contrast": self._contrast,
                "exposure": self._exposure,
            }
        if self._cap is None:
            return {"error": "Camera not connected"}
        cv_module = globals().get("cv2")
        if cv_module is None or not hasattr(self._cap, "get"):
            return {
                "resolution": f"{self._width}x{self._height}",
                "fps": self._fps,
                "brightness": self._brightness,
                "contrast": self._contrast,
                "exposure": self._exposure,
            }
        return {
            "resolution": f"{int(self._cap.get(cv_module.CAP_PROP_FRAME_WIDTH))}x{int(self._cap.get(cv_module.CAP_PROP_FRAME_HEIGHT))}",
            "fps": self._cap.get(cv_module.CAP_PROP_FPS),
            "brightness": self._cap.get(cv_module.CAP_PROP_BRIGHTNESS),
            "contrast": self._cap.get(cv_module.CAP_PROP_CONTRAST),
            "exposure": self._exposure,
        }

    def _set_settings(self, **params: Any) -> dict[str, Any]:
        if self._simulation_mode:
            if "resolution" in params:
                w, h = params["resolution"].split("x")
                self._width = int(w)
                self._height = int(h)
            if "fps" in params:
                self._fps = int(params["fps"])
            if "brightness" in params:
                self._brightness = params["brightness"]
            if "contrast" in params:
                self._contrast = params["contrast"]
            if "exposure" in params:
                self._exposure = params["exposure"]
            return {"configured": True, "settings": self._get_settings()}

        if self._cap is None:
            return {"error": "Camera not connected"}
        cv_module = globals().get("cv2")
        if cv_module is None or not hasattr(self._cap, "set"):
            if "resolution" in params:
                self._width, self._height = (int(item) for item in params["resolution"].split("x"))
            if "fps" in params:
                self._fps = int(params["fps"])
            self._brightness = params.get("brightness", self._brightness)
            self._contrast = params.get("contrast", self._contrast)
            self._exposure = params.get("exposure", self._exposure)
            return {"configured": True, "settings": self._get_settings()}

        if "resolution" in params:
            width, height = (int(item) for item in params["resolution"].split("x"))
            self._cap.set(cv_module.CAP_PROP_FRAME_WIDTH, width)
            self._cap.set(cv_module.CAP_PROP_FRAME_HEIGHT, height)
            self._width, self._height = width, height
        if "fps" in params:
            self._cap.set(cv_module.CAP_PROP_FPS, int(params["fps"]))
            self._fps = int(params["fps"])
        if "brightness" in params:
            self._cap.set(cv_module.CAP_PROP_BRIGHTNESS, params["brightness"])
        if "contrast" in params:
            self._cap.set(cv_module.CAP_PROP_CONTRAST, params["contrast"])
        if "exposure" in params:
            self._exposure = params["exposure"]

        return {"configured": True, "settings": self._get_settings()}

    async def _control_stream(
        self, action: str = "start", duration: int | None = None, **params: Any
    ) -> dict[str, Any]:
        if action == "start":
            self._streaming = True
            try:
                if duration is not None:
                    await asyncio.sleep(duration)
                return {"streaming": self._streaming, "duration": duration}
            except asyncio.CancelledError:
                self._streaming = False
                raise
        elif action == "stop":
            self._streaming = False
            return {"streaming": False}
        return {"error": f"Unknown stream action: {action}"}

    async def close(self) -> None:
        self._streaming = False
        cap, self._cap = self._cap, None
        self._backend = None
        if cap is not None:
            await asyncio.to_thread(cap.release)
        await super().close()


@register_driver
class CameraDriver(Driver):
    """Driver for USB/IP cameras."""

    DRIVER_NAME = "camera"
    SUPPORTED_DEVICES = ["camera", "webcam", "ip_camera"]

    def __init__(
        self, config: DriverConfig, *, simulation: bool | None = None, backend_factory: Any = None
    ):
        super().__init__(config, simulation=simulation, backend_factory=backend_factory)
        self._source = config.connection_params.get("source", "0")
        self._width = config.connection_params.get("width", 640)
        self._height = config.connection_params.get("height", 480)
        self._fps = config.connection_params.get("fps", 30)

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "camera_001")
        candidate = CameraDevice(
            device_id,
            self._source,
            self._width,
            self._height,
            self._fps,
            simulation=self.simulation,
            backend_factory=self.backend_factory,
        )
        return await self._connect_candidate(candidate)
