"""Robot arm and motion control adapters for MHS."""

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


class RobotArmDevice(BackendDevice):
    """Generic 6-DOF robot arm."""

    def __init__(
        self, device_id: str, dof: int = 6, *, simulation: bool = False, backend_factory: Any = None
    ):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="robot_arm",
            manufacturer="Generic",
            model=f"{dof}-DOF Arm",
            capabilities=[
                DeviceCapability(
                    name="joint_position",
                    description="Set or get joint angles",
                    parameters={"joints": "List of angles in degrees", "speed": "0-100%"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="cartesian_position",
                    description="Move end-effector to XYZ position",
                    parameters={"x": "mm", "y": "mm", "z": "mm", "speed": "0-100%"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="gripper",
                    description="Control the gripper",
                    parameters={"position": "0-100% open", "force": "0-100%"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="home",
                    description="Move to home position",
                    parameters={},
                    read_only=False,
                ),
                DeviceCapability(
                    name="status",
                    description="Get arm status",
                    parameters={},
                    read_only=True,
                ),
            ],
            safety_limits=[
                SafetyLimit("speed", 0, 100, "%", "Movement speed limit"),
                SafetyLimit("x", -500, 500, "mm", "X-axis range"),
                SafetyLimit("y", -500, 500, "mm", "Y-axis range"),
                SafetyLimit("z", 0, 800, "mm", "Z-axis range"),
                SafetyLimit("force", 0, 100, "%", "Gripper force limit"),
            ],
            tags=["robot", "arm", "manipulator", "motion"],
            natural_language_description=(
                "A multi-degree-of-freedom robot arm capable of precise positioning "
                "and manipulation tasks. Supports both joint-space and Cartesian control."
            ),
            driver_class="robot_arm",
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
        self._dof = dof
        self._joint_angles: list[float] = [0.0] * dof
        self._position: dict[str, float] = {"x": 300.0, "y": 0.0, "z": 400.0}
        self._gripper_pos: float = 0.0
        self._moving: bool = False

    def _strict_validate(self, capability_name: str, params: dict[str, Any]) -> None:
        super()._strict_validate(capability_name, params)

        def bounded(name: str, low: float, high: float) -> None:
            value = params.get(name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not low <= value <= high
            ):
                raise CapabilityError(f"{name} is outside its safe range")

        if capability_name == "joint_position":
            joints = params.get("joints")
            if (
                not isinstance(joints, list)
                or len(joints) != self._dof
                or any(
                    not isinstance(v, (int, float))
                    or isinstance(v, bool)
                    or not math.isfinite(float(v))
                    for v in joints
                )
            ):
                raise CapabilityError("joints must be a finite list with exactly dof values")
            bounded("speed", 0, 100)
        elif capability_name == "cartesian_position":
            bounded("x", -500, 500)
            bounded("y", -500, 500)
            bounded("z", 0, 800)
            bounded("speed", 0, 100)
        elif capability_name == "gripper":
            bounded("position", 0, 100)
            bounded("force", 0, 100)

    async def connect(self) -> bool:
        return await super().connect()

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "joint_position":
            return {"joints": self._joint_angles, "unit": "degrees"}
        elif capability == "cartesian_position":
            return self._position.copy()
        elif capability == "gripper":
            return {"position": self._gripper_pos, "unit": "%"}
        elif capability == "status":
            return {
                "joints": self._joint_angles,
                "position": self._position,
                "gripper": self._gripper_pos,
                "moving": self._moving,
                "state": self.state.name,
            }
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "joint_position":
            joints = params.get("joints", self._joint_angles)
            speed = params.get("speed", 50)
            target = list(joints)
            if len(target) != self._dof:
                raise ValueError("joints must contain exactly dof values")
            self._moving = True
            try:
                await asyncio.sleep(0.5)
            finally:
                self._moving = False
            self._joint_angles = target
            return {"joints": list(self._joint_angles), "speed": speed}

        elif capability == "cartesian_position":
            x = params.get("x", self._position["x"])
            y = params.get("y", self._position["y"])
            z = params.get("z", self._position["z"])
            speed = params.get("speed", 50)
            target_pos = {"x": float(x), "y": float(y), "z": float(z)}
            self._moving = True
            try:
                await asyncio.sleep(0.5)
            finally:
                self._moving = False
            self._position = target_pos
            return {"position": self._position.copy(), "speed": speed}

        elif capability == "gripper":
            position = params.get("position", 0)
            force = params.get("force", 50)
            self._gripper_pos = position
            return {"position": position, "force": force}

        elif capability == "home":
            self._joint_angles = [0.0] * self._dof
            self._position = {"x": 300.0, "y": 0.0, "z": 400.0}
            self._gripper_pos = 0.0
            return {"homed": True}

        return {"error": f"Unknown capability: {capability}"}


@register_driver
class RobotArmDriver(Driver):
    """Driver for generic robot arms."""

    def __init__(
        self, config: DriverConfig, *, simulation: bool | None = None, backend_factory: Any = None
    ):
        super().__init__(config, simulation=simulation, backend_factory=backend_factory)
        self._dof = config.connection_params.get("dof", 6)

    DRIVER_NAME = "robot_arm"
    SUPPORTED_DEVICES = ["robot_arm", "manipulator", "ur", "dof"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "arm_001")
        return await self._connect_candidate(
            RobotArmDevice(
                device_id,
                self._dof,
                simulation=self.simulation,
                backend_factory=self.backend_factory,
            )
        )


# === 3D Printer ===


class Printer3DDevice(BackendDevice):
    """Generic 3D printer."""

    def __init__(self, device_id: str, *, simulation: bool = False, backend_factory: Any = None):
        metadata = DeviceMetadata(
            device_id=device_id,
            device_type="3d_printer",
            manufacturer="Generic",
            model="FDM Printer",
            capabilities=[
                DeviceCapability(
                    name="temperature",
                    description="Get or set nozzle/bed temperature",
                    parameters={"nozzle": "°C", "bed": "°C"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="position",
                    description="Get current position",
                    parameters={},
                    read_only=True,
                ),
                DeviceCapability(
                    name="print",
                    description="Start or control a print job",
                    parameters={
                        "gcode": "G-code string or file path",
                        "action": "start/pause/stop",
                    },
                    read_only=False,
                ),
                DeviceCapability(
                    name="extrude",
                    description="Extrude or retract filament",
                    parameters={"amount": "mm", "speed": "mm/min"},
                    read_only=False,
                ),
                DeviceCapability(
                    name="home_axis",
                    description="Home one or all axes",
                    parameters={"axis": "x/y/z/all"},
                    read_only=False,
                ),
            ],
            safety_limits=[
                SafetyLimit("nozzle", 0, 300, "°C", "Nozzle temperature limit"),
                SafetyLimit("bed", 0, 120, "°C", "Bed temperature limit"),
                SafetyLimit("amount", -100, 100, "mm", "Extrusion amount limit"),
            ],
            tags=["printer", "3d", "manufacturing", "fabrication"],
            natural_language_description=(
                "A fused deposition modeling (FDM) 3D printer. "
                "Supports temperature control, motion control, and G-code execution."
            ),
            driver_class="3d_printer",
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
        self._nozzle_temp = 25.0
        self._bed_temp = 25.0
        self._position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._printing = False

    async def connect(self) -> bool:
        return await super().connect()

    def _strict_validate(self, capability_name: str, params: dict[str, Any]) -> None:
        super()._strict_validate(capability_name, params)
        if capability_name == "temperature":
            for name, low, high in (("nozzle", 0, 300), ("bed", 0, 120)):
                if name in params and (
                    not isinstance(params[name], (int, float))
                    or isinstance(params[name], bool)
                    or not math.isfinite(float(params[name]))
                    or not low <= params[name] <= high
                ):
                    raise CapabilityError(f"{name} temperature is outside its safe range")
        if capability_name == "print" and params.get("action", "start") not in {
            "start",
            "pause",
            "stop",
        }:
            raise CapabilityError("print action is unsupported")
        if capability_name == "home_axis" and params.get("axis", "all") not in {
            "x",
            "y",
            "z",
            "all",
        }:
            raise CapabilityError("printer axis is unsupported")

    async def close(self) -> None:
        self._printing = False
        self._nozzle_temp = 25.0
        self._bed_temp = 25.0
        await super().close()

    async def _do_read(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "temperature":
            return {"nozzle": self._nozzle_temp, "bed": self._bed_temp, "unit": "°C"}
        elif capability == "position":
            return self._position.copy()
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> dict[str, Any]:
        if capability == "temperature":
            if "nozzle" in params:
                self._nozzle_temp = float(params["nozzle"])
            if "bed" in params:
                self._bed_temp = float(params["bed"])
            return {"nozzle": self._nozzle_temp, "bed": self._bed_temp}

        elif capability == "print":
            action = params.get("action", "start")
            if action == "start":
                self._printing = True
            elif action == "pause" or action == "stop":
                self._printing = False
            return {"printing": self._printing, "action": action}

        elif capability == "extrude":
            amount = params.get("amount", 10)
            return {"extruded": amount, "unit": "mm"}

        elif capability == "home_axis":
            axis = params.get("axis", "all")
            if axis == "all":
                self._position = {"x": 0.0, "y": 0.0, "z": 0.0}
            else:
                self._position[axis] = 0.0
            return {"homed": axis}

        return {"error": f"Unknown capability: {capability}"}


@register_driver
class Printer3DDriver(Driver):
    """Driver for 3D printers."""

    def __init__(
        self, config: DriverConfig, *, simulation: bool | None = None, backend_factory: Any = None
    ):
        super().__init__(config, simulation=simulation, backend_factory=backend_factory)

    DRIVER_NAME = "3d_printer"
    SUPPORTED_DEVICES = ["3d_printer", "printer", "fdm"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "printer_001")
        return await self._connect_candidate(
            Printer3DDevice(
                device_id, simulation=self.simulation, backend_factory=self.backend_factory
            )
        )
