"""Robot arm and motion control adapters for MHS."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Dict, List, Optional

from openmhs.core.device import (
    BaseDevice, DeviceCapability, DeviceMetadata, DeviceState, SafetyLimit,
)
from openmhs.core.driver import Driver, DriverConfig, register_driver


class RobotArmDevice(BaseDevice):
    """Generic 6-DOF robot arm."""

    def __init__(self, device_id: str, dof: int = 6):
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
        super().__init__(metadata)
        self._dof = dof
        self._joint_angles = [0.0] * dof
        self._position = {"x": 300.0, "y": 0.0, "z": 400.0}
        self._gripper_pos = 0.0
        self._moving = False

    async def connect(self) -> bool:
        self._set_state(DeviceState.ONLINE)
        return True

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
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

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "joint_position":
            joints = params.get("joints", self._joint_angles)
            speed = params.get("speed", 50)
            self._joint_angles = list(joints)[:self._dof]
            self._moving = True
            await asyncio.sleep(0.5)  # Simulate movement
            self._moving = False
            return {"joints": self._joint_angles, "speed": speed}
        
        elif capability == "cartesian_position":
            x = params.get("x", self._position["x"])
            y = params.get("y", self._position["y"])
            z = params.get("z", self._position["z"])
            speed = params.get("speed", 50)
            self._position = {"x": x, "y": y, "z": z}
            self._moving = True
            await asyncio.sleep(0.5)
            self._moving = False
            return {"position": self._position, "speed": speed}
        
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

    DRIVER_NAME = "robot_arm"
    SUPPORTED_DEVICES = ["robot_arm", "manipulator", "ur", "dof"]

    def __init__(self, config: DriverConfig):
        super().__init__(config)
        self._dof = config.connection_params.get("dof", 6)

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "arm_001")
        self._device = RobotArmDevice(device_id, self._dof)
        result = await self._device.connect()
        self._connected = result
        return result


# === 3D Printer ===

class Printer3DDevice(BaseDevice):
    """Generic 3D printer."""

    def __init__(self, device_id: str):
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
                    parameters={"gcode": "G-code string or file path", "action": "start/pause/stop"},
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
        super().__init__(metadata)
        self._nozzle_temp = 25.0
        self._bed_temp = 25.0
        self._position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._printing = False

    async def connect(self) -> bool:
        self._set_state(DeviceState.ONLINE)
        return True

    async def _do_read(self, capability: str, **params: Any) -> Dict[str, Any]:
        if capability == "temperature":
            return {"nozzle": self._nozzle_temp, "bed": self._bed_temp, "unit": "°C"}
        elif capability == "position":
            return self._position.copy()
        return {"error": f"Unknown capability: {capability}"}

    async def _do_write(self, capability: str, **params: Any) -> Dict[str, Any]:
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
            elif action == "pause":
                self._printing = False
            elif action == "stop":
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

    DRIVER_NAME = "3d_printer"
    SUPPORTED_DEVICES = ["3d_printer", "printer", "fdm"]

    async def connect(self) -> bool:
        device_id = self.config.connection_params.get("device_id", "printer_001")
        self._device = Printer3DDevice(device_id)
        result = await self._device.connect()
        self._connected = result
        return result
