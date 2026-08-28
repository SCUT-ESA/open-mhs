"""
Open Model Hardware Standard (MHS)

An open-source implementation of AI agent hardware control,
inspired by Anthropic's Model Hardware Standard (MHS).

This package provides a unified interface for AI agents to discover,
monitor, and safely operate physical devices.
"""

__version__ = "0.1.0"

from openmhs.core.device import Device, DeviceCapability, DeviceMetadata, SafetyLimit
from openmhs.core.driver import Driver, DriverConfig
from openmhs.core.protocol import MHSProtocol, Command, CommandType, Response
from openmhs.core.registry import DeviceRegistry

__all__ = [
    "Device",
    "DeviceCapability",
    "DeviceMetadata",
    "SafetyLimit",
    "Driver",
    "DriverConfig",
    "MHSProtocol",
    "Command",
    "CommandType",
    "Response",
    "DeviceRegistry",
]
