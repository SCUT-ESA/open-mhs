"""Example: Control a simulated robot arm with Open MHS."""

from __future__ import annotations

import asyncio

from openmhs.adapters.robots import RobotArmDriver
from openmhs.core.driver import DriverConfig


async def main():
    # Connect to a 6-DOF robot arm in simulation mode
    config = DriverConfig(
        driver_name="robot_arm",
        connection_params={"device_id": "arm_001", "dof": 6},
        simulation=True,
    )
    driver = RobotArmDriver(config)
    ok = await driver.connect()
    if not ok or driver.device is None:
        print("Failed to connect to robot arm.")
        return

    try:
        arm = driver.device

        # Get current position
        status = await arm.read("status")
        print(f"Current status: {status}")

        # Move to home position
        result = await arm.write("home")
        print(f"Homed: {result}")

        # Move to a Cartesian position
        result = await arm.write("cartesian_position", x=300.0, y=100.0, z=400.0, speed=50.0)
        print(f"Moved to: {result}")

        # Set joint angles
        result = await arm.write(
            "joint_position", joints=[0.0, 45.0, 90.0, 0.0, 90.0, 0.0], speed=30.0
        )
        print(f"Joints set: {result}")

        # Control gripper
        result = await arm.write("gripper", position=75.0, force=60.0)
        print(f"Gripper: {result}")

    finally:
        await driver.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
