"""Laboratory equipment adapters for MHS.

Supports:
- Microscope (simulated)
- Liquid handler (simulated)
- Centrifuge (simulated)
- Laser (simulated)
- Spectrometer (simulated)
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

from openmhs.core.backend import BackendDevice, BackendPolicy
from openmhs.core.device import (
    CapabilityError,
    DeviceCapability,
    DeviceMetadata,
    SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class MicroscopeDevice(BackendDevice):
    """Digital microscope."""

    def _strict_validate(self, capability_name: str, params: dict[str, Any]) -> None:
        super()._strict_validate(capability_name, params)
        for name, value in params.items():
            if name in {
                "exposure",
                "gain",
                "resolution",
                "x",
                "y",
                "z",
                "intensity",
                "wavelength",
            } and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                raise CapabilityError(f"{name} must be a finite number")
        if "gain" in params and not 0 <= params["gain"] <= 100:
            raise CapabilityError("gain is outside its safe range")
        if "intensity" in params and not 0 <= params["intensity"] <= 100:
            raise CapabilityError("intensity is outside its safe range")
        if "speed" in params and params["speed"] not in {"slow", "medium", "fast"}:
            raise CapabilityError("speed is unsupported")

    def __init__(self, device_id: str, *, simulation: bool = False, backend_factory: Any = None):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="microscope",
            manufacturer="Generic",
            model="Digital Microscope",
            capabilities=[
                DeviceCapability(
                    name="capture",
                    description="Capture an image through the microscope",
                    parameters={"exposure": "ms", "gain": "0-100", "resolution": "MP"},
                    read_only=True,
                ),
                DeviceCapability(
                    name="focus",
                    description="Adjust focus position",
                    parameters={"z": "micrometers", "speed": "slow/medium/fast"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="stage_position",
                    description="Move the XY stage",
                    parameters={"x": "mm", "y": "mm"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="light",
                    description="Control illumination",
                    parameters={"intensity": "0-100%", "wavelength": "nm"},
                    read_only=False,
                ),
            ],
            safety_limits=[
                SafetyLimit("intensity", 0, 100, "%", "Light intensity"),
                SafetyLimit("gain", 0, 100, "%", "Camera gain"),
            ],
            tags=["lab", "microscope", "imaging", "biology"],
            natural_language_description=(
                "A digital microscope with motorized focus, XY stage, and controllable illumination. "
                "Used for biological and materials imaging."
            ),
            driver_class="microscope",
        )
        super().__init__(
            metadata,
            BackendPolicy(
                simulation=simulation,
                backend_factory=backend_factory,
                backend_name="simulation",
                supported=False,
            ),
        )
        self._focus_z = 0.0
        self._stage_x = 0.0
        self._stage_y = 0.0
        self._light_intensity = 50.0

    async def connect(self) -> bool:
        return await super().connect()

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "capture":
            return {
                "captured": True,
                "exposure": params.get("exposure", 100),
                "gain": params.get("gain", 50),
                "simulated": True,
            }
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "focus":
            z = params.get("z", 0)
            self._focus_z = z
            return {"focus_z": z}
        elif capability == "stage_position":
            x = params.get("x", 0)
            y = params.get("y", 0)
            self._stage_x = x
            self._stage_y = y
            return {"x": x, "y": y}
        elif capability == "light":
            intensity = params.get("intensity", 50)
            self._light_intensity = intensity
            return {"intensity": intensity}
        return {"error": f"Unknown capability: {capability}"}


@register_driver
class MicroscopeDriver(Driver):
    """Driver for digital microscopes."""

    def __init__(
        self, config: DriverConfig, *, simulation: bool | None = None, backend_factory: Any = None
    ):
        super().__init__(config, simulation=simulation, backend_factory=backend_factory)

    DRIVER_NAME = "microscope"
    SUPPORTED_DEVICES = ["microscope", "imaging"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "scope_001")
        return await self._connect_candidate(
            MicroscopeDevice(
                device_id, simulation=self.simulation, backend_factory=self.backend_factory
            )
        )


# === Liquid Handler ===


class LiquidHandlerDevice(BackendDevice):
    """Automated liquid handling robot."""

    def _strict_validate(self, capability_name: str, params: dict[str, Any]) -> None:
        super()._strict_validate(capability_name, params)
        for name, value in params.items():
            if name in {"volume", "x", "y", "z", "cycles"} and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                raise CapabilityError(f"{name} must be a finite number")
        bounds = (("volume", 0, 1000), ("x", 0, 300), ("y", 0, 200), ("z", 0, 150))
        for name, low, high in bounds:
            if name in params and not low <= params[name] <= high:
                raise CapabilityError(f"{name} is outside its safe range")
        if "cycles" in params and (
            params["cycles"] < 0 or int(params["cycles"]) != params["cycles"]
        ):
            raise CapabilityError("cycles must be a non-negative integer")

    def __init__(self, device_id: str, *, simulation: bool = False, backend_factory: Any = None):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="liquid_handler",
            manufacturer="Generic",
            model="Pipetting Robot",
            capabilities=[
                DeviceCapability(
                    name="status",
                    description="Get liquid handler status",
                    parameters={},
                    read_only=True,
                ),
                DeviceCapability(
                    name="aspirate",
                    description="Draw liquid into pipette",
                    parameters={"volume": "ul", "speed": "slow/medium/fast"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="dispense",
                    description="Dispense liquid",
                    parameters={"volume": "ul", "location": "well/plate"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="move",
                    description="Move pipette to position",
                    parameters={"x": "mm", "y": "mm", "z": "mm"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="wash",
                    description="Wash pipette tips",
                    parameters={"cycles": "number"},
                    read_only=False,
                ),
            ],
            safety_limits=[
                SafetyLimit("volume", 0, 1000, "ul", "Pipette volume limit"),
                SafetyLimit("x", 0, 300, "mm", "X-axis range"),
                SafetyLimit("y", 0, 200, "mm", "Y-axis range"),
                SafetyLimit("z", 0, 150, "mm", "Z-axis range"),
            ],
            tags=["lab", "liquid_handler", "pipetting", "biology", "chemistry"],
            natural_language_description=(
                "An automated liquid handling robot for high-throughput pipetting, "
                "dilution, and sample preparation in biological and chemical laboratories."
            ),
            driver_class="liquid_handler",
        )
        super().__init__(
            metadata,
            BackendPolicy(
                simulation=simulation,
                backend_factory=backend_factory,
                backend_name="simulation",
                supported=False,
            ),
        )
        self._position = {"x": 0, "y": 0, "z": 0}

    async def connect(self) -> bool:
        return await super().connect()

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "status":
            return {"position": self._position, "ready": True}
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "aspirate":
            volume = params.get("volume", 100)
            return {"aspirated": volume, "unit": "ul"}
        elif capability == "dispense":
            volume = params.get("volume", 100)
            location = params.get("location", "A1")
            return {"dispensed": volume, "location": location}
        elif capability == "move":
            x = params.get("x", 0)
            y = params.get("y", 0)
            z = params.get("z", 0)
            self._position = {"x": x, "y": y, "z": z}
            return {"position": self._position}
        elif capability == "wash":
            cycles = params.get("cycles", 3)
            return {"washed": True, "cycles": cycles}
        return {"error": f"Unknown capability: {capability}"}


@register_driver
class LiquidHandlerDriver(Driver):
    """Driver for liquid handling robots."""

    def __init__(
        self, config: DriverConfig, *, simulation: bool | None = None, backend_factory: Any = None
    ):
        super().__init__(config, simulation=simulation, backend_factory=backend_factory)

    DRIVER_NAME = "liquid_handler"
    SUPPORTED_DEVICES = ["liquid_handler", "pipetting"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "liquid_001")
        return await self._connect_candidate(
            LiquidHandlerDevice(
                device_id, simulation=self.simulation, backend_factory=self.backend_factory
            )
        )


# === Laser ===


class LaserDevice(BackendDevice):
    """Controllable laser system."""

    def __init__(self, device_id: str, *, simulation: bool = False, backend_factory: Any = None):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="laser",
            manufacturer="Generic",
            model="Diode Laser",
            capabilities=[
                DeviceCapability(
                    name="power",
                    description="Set laser power output",
                    parameters={"power": "0-100%", "wavelength": "nm"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="status",
                    description="Get laser status",
                    parameters={},
                    read_only=True,
                ),
                DeviceCapability(
                    name="align",
                    description="Auto-align laser beam",
                    parameters={"method": "manual/auto"},
                    read_only=False,
                ),
            ],
            safety_limits=[
                SafetyLimit("power", 0, 100, "%", "Laser power output", hard_limit=True),
            ],
            tags=["lab", "laser", "optics", "physics"],
            natural_language_description=(
                "A controllable diode laser system with power adjustment and beam alignment. "
                "Used in spectroscopy, microscopy, and quantum computing experiments."
            ),
            driver_class="laser",
        )
        super().__init__(
            metadata,
            BackendPolicy(
                simulation=simulation,
                backend_factory=backend_factory,
                backend_name="simulation",
                supported=False,
            ),
        )
        self._power = 0.0
        self._enabled = False

    async def connect(self) -> bool:
        return await super().connect()

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "status":
            return {"power": self._power, "enabled": self._enabled}
        return {"error": f"Unknown capability: {capability}"}

    def _strict_validate(self, capability_name: str, params: dict[str, Any]) -> None:
        super()._strict_validate(capability_name, params)
        if capability_name == "power":
            power = params.get("power")
            if (
                not isinstance(power, (int, float))
                or isinstance(power, bool)
                or not 0 <= power <= 100
            ):
                raise CapabilityError("laser power must be between 0 and 100")
        if capability_name == "align" and params.get("method") not in {"manual", "auto"}:
            raise CapabilityError("laser alignment method must be manual or auto")

    async def close(self) -> None:
        self._power = 0.0
        self._enabled = False
        await super().close()

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "power":
            power = float(params.get("power", 0))
            self._power = power
            self._enabled = power > 0
            return {"power": power, "enabled": self._enabled}
        elif capability == "align":
            method = params.get("method", "auto")
            await asyncio.sleep(0.5)  # Simulate alignment
            return {"aligned": True, "method": method}
        return {"error": f"Unknown capability: {capability}"}


@register_driver
class LaserDriver(Driver):
    """Driver for laser systems."""

    def __init__(
        self, config: DriverConfig, *, simulation: bool | None = None, backend_factory: Any = None
    ):
        super().__init__(config, simulation=simulation, backend_factory=backend_factory)

    DRIVER_NAME = "laser"
    SUPPORTED_DEVICES = ["laser", "optics"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "laser_001")
        return await self._connect_candidate(
            LaserDevice(device_id, simulation=self.simulation, backend_factory=self.backend_factory)
        )
