"""Compatibility wrapper for waveform-generator-only discovery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openmhs.adapters.visa.discovery import (
    DiscoveredWaveformGenerator,
    DiscoveryIssue,
    DiscoveryState,
    ProbeSnapshot,
    ResourceObservation,
    ScanResult,
    VisaDeviceSpec,
    VisaDiscoveryManager,
    matches_utg2062x,
)
from openmhs.adapters.waveform_generator import WaveformGeneratorDriver
from openmhs.core.driver import Driver, DriverConfig
from openmhs.core.registry import DeviceRegistry


class WaveformGeneratorDiscoveryManager(VisaDiscoveryManager):
    """Discover only UNI-T UTG2062X generators."""

    def __init__(
        self,
        registry: DeviceRegistry,
        resource_manager_factory: Callable[[], Any] | None = None,
        driver_factory: Callable[[DriverConfig], Driver] | None = None,
        interval: float = 5.0,
        discovery_interval: float | None = None,
        poll_interval: float | None = None,
    ) -> None:
        factory = driver_factory or WaveformGeneratorDriver
        spec = VisaDeviceSpec(
            "waveform_generator",
            "waveform_generator",
            "waveform_generator",
            matches_utg2062x,
            factory,
            "wavegen",
            DiscoveredWaveformGenerator,
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
    "DiscoveredWaveformGenerator",
    "DiscoveryIssue",
    "DiscoveryState",
    "ProbeSnapshot",
    "ResourceObservation",
    "ScanResult",
    "WaveformGeneratorDiscoveryManager",
]
