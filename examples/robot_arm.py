"""Example: Control a robot arm with Open MHS."""

import asyncio
from openmhs.adapters.robots import RobotArmDriver
from openmhs.core.driver import DriverConfig

async def main():
    # Connect to a 6-DOF robot arm
    config = DriverConfig(
        driver_name="robot_arm",
        connection_params={"device_id": "arm_001", "dof": 6}
    )
    driver = RobotArmDriver(config)
    await driver.connect()
    
    arm = driver.device
    
    # Get current position
    status = await arm.read("status")
    print(f"Current status: {status}")
    
    # Move to home position
    result = await arm.write("home")
    print(f"Homed: {result}")
    
    # Move to a Cartesian position
    result = await arm.write("cartesian_position", x=300, y=100, z=400, speed=50)
    print(f"Moved to: {result}")
    
    # Set joint angles
    result = await arm.write("joint_position", joints=[0, 45, 90, 0, 90, 0], speed=30)
    print(f"Joints set: {result}")
    
    # Control gripper
    result = await arm.write("gripper", position=75, force=60)
    print(f"Gripper: {result}")
    
    # Disconnect
    await driver.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
