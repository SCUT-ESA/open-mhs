"""Backward-compatible oscilloscope-only VISA discovery wrapper."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openmhs.adapters.oscilloscope import OscilloscopeDriver
from openmhs.adapters.visa.discovery import (
    DiscoveredDevice,
    DiscoveredOscilloscope,
    DiscoveryIssue,
    DiscoveryState,
    ProbeSnapshot,
    ResourceObservation,
    ScanResult,
    VisaDeviceSpec,
    VisaDiscoveryManager,
    matches_upo6102n,
)
from openmhs.core.driver import Driver, DriverConfig
from openmhs.core.registry import DeviceRegistry


class OscilloscopeDiscoveryManager(VisaDiscoveryManager):
    """Compatibility facade that discovers only UNI-T UPO6102N scopes."""

    def __init__(
        self,
        registry: DeviceRegistry,
        resource_manager_factory: Callable[[], Any] | None = None,
        driver_factory: Callable[[DriverConfig], Driver] | None = None,
        interval: float = 5.0,
        discovery_interval: float | None = None,
        poll_interval: float | None = None,
    ) -> None:
        factory = driver_factory or OscilloscopeDriver
        spec = VisaDeviceSpec(
            "oscilloscope",
            "oscilloscope",
            "oscilloscope",
            matches_upo6102n,
            factory,
            "scope",
            DiscoveredOscilloscope,
        )
        super().__init__(
            registry,
            resource_manager_factory=resource_manager_factory,
            specs=(spec,),
            interval=interval,
            discovery_interval=discovery_interval,
            poll_interval=poll_interval,
        )


__all__ = [
    "DiscoveredDevice",
    "DiscoveredOscilloscope",
    "DiscoveryIssue",
    "DiscoveryState",
    "OscilloscopeDiscoveryManager",
    "ProbeSnapshot",
    "ResourceObservation",
    "ScanResult",
]
