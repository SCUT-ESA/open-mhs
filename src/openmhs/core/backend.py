"""Shared backend policy and device implementation for non-VISA adapters.

A backend is deliberately explicit: simulation is selected by configuration, never
by import availability.  Real backends are injected factories so importing this
package can never touch hardware.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .device import BaseDevice, CapabilityError, DeviceMetadata, DeviceState

BackendFactory = Callable[[], Any]
Probe = Callable[[Any], Any]


@dataclass(frozen=True)
class BackendPolicy:
    """Policy selecting either the deterministic simulated or an injected backend."""

    simulation: bool = False
    backend_factory: BackendFactory | None = None
    backend_name: str = "unknown"
    supported: bool = True
    probe: Probe | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.simulation, bool):
            raise TypeError("simulation must be a bool")
        if not isinstance(self.supported, bool):
            raise TypeError("supported must be a bool")
        if not isinstance(self.backend_name, str) or not self.backend_name.strip():
            raise ValueError("backend_name must be a non-empty string")


class BackendDevice(BaseDevice):
    """Base device that owns explicit backend selection and result decoration."""

    def __init__(
        self,
        metadata: DeviceMetadata,
        policy: BackendPolicy | None = None,
        *,
        backend_policy: BackendPolicy | None = None,
    ) -> None:
        if policy is not None and backend_policy is not None and policy != backend_policy:
            raise ValueError("policy and backend_policy conflict")
        self.policy = backend_policy or policy or BackendPolicy()
        metadata = copy.deepcopy(metadata)
        metadata.connection_info = copy.deepcopy(metadata.connection_info)
        metadata.connection_info.update(
            {"backend": self.policy.backend_name, "simulated": self.policy.simulation}
        )
        # Keep marker fields discoverable while preserving DeviceMetadata compatibility.
        setattr(metadata, "backend", self.policy.backend_name)  # noqa: B010
        setattr(metadata, "simulated", self.policy.simulation)  # noqa: B010
        if self.policy.simulation and "simulated" not in metadata.tags:
            metadata.tags = [*metadata.tags, "simulated"]
        super().__init__(metadata)
        self._backend: Any = None

    @property
    def backend(self) -> Any:
        return self._backend

    @property
    def simulated(self) -> bool:
        return self.policy.simulation

    async def connect(self) -> bool:
        if self.policy.simulation:
            self._set_state(DeviceState.SIMULATED)
            return True
        if not self.policy.supported or self.policy.backend_factory is None:
            self._set_state(DeviceState.ERROR)
            return False
        candidate: Any = None
        try:
            candidate = await _off_loop(self.policy.backend_factory)
            if self.policy.probe is not None:
                result = await _off_loop(self.policy.probe, candidate)
                if result is False:
                    raise RuntimeError("backend probe failed")
            elif hasattr(candidate, "probe"):
                result = await _off_loop(candidate.probe)
                if result is False:
                    raise RuntimeError("backend probe failed")
            self._backend = candidate
            self._set_state(DeviceState.ONLINE)
            return True
        except asyncio.CancelledError:
            await _close_candidate(candidate)
            self._set_state(DeviceState.ERROR)
            raise
        except Exception:  # noqa: BLE001
            await _close_candidate(candidate)
            self._set_state(DeviceState.ERROR)
            return False

    async def read(self, capability: str, **params: Any) -> dict[str, Any]:
        self._strict_validate(capability, params)
        result = await super().read(capability, **params)
        return self._decorate(result)

    async def write(self, capability: str, **params: Any) -> dict[str, Any]:
        self._strict_validate(capability, params)
        result = await super().write(capability, **params)
        return self._decorate(result)

    async def health_check(self) -> dict[str, Any]:
        result = await super().health_check()
        result.update({"backend": self.policy.backend_name, "simulated": self.policy.simulation})
        return copy.deepcopy(result)

    async def close(self) -> None:
        backend, self._backend = self._backend, None
        await _close_candidate(backend)
        await super().close()

    def _decorate(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            result = {"value": result}
        decorated: dict[str, Any] = copy.deepcopy(result)
        decorated.update({"backend": self.policy.backend_name, "simulated": self.policy.simulation})
        return decorated

    def _strict_validate(self, capability_name: str, params: dict[str, Any]) -> None:
        capability = self._get_capability(capability_name)
        if capability is None:
            return
        schema = capability.schema
        required = capability.required
        missing = [name for name in required if name not in params]
        if missing:
            raise CapabilityError(
                f"Capability '{capability_name}' requires parameters: {', '.join(missing)}"
            )
        if not schema:
            _validate_finite(params)
            return
        extra = [name for name in params if name not in schema]
        if extra:
            raise CapabilityError(
                f"Capability '{capability_name}' does not accept parameters: {', '.join(extra)}"
            )
        for name, value in params.items():
            _validate_schema(value, schema[name], f"{capability_name}.{name}")


def _is_finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _validate_finite(value: Any, path: str = "parameters") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CapabilityError(f"{path} contains a non-finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_finite(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_finite(item, f"{path}[{index}]")


def _matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": _is_finite(value),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, True)


def _validate_schema(value: Any, definition: Any, path: str) -> None:
    if not isinstance(definition, dict):
        _validate_finite(value, path)
        return
    expected = definition.get("type")
    if expected is not None and not _matches_type(value, expected):
        raise CapabilityError(f"{path} has an invalid type")
    _validate_finite(value, path)
    if "enum" in definition and value not in definition["enum"]:
        raise CapabilityError(f"{path} is not an allowed value")
    if _is_finite(value):
        num_val = float(value)
        if "minimum" in definition and num_val < definition["minimum"]:
            raise CapabilityError(f"{path} is below minimum")
        if "maximum" in definition and num_val > definition["maximum"]:
            raise CapabilityError(f"{path} is above maximum")
        if "exclusiveMinimum" in definition and num_val <= definition["exclusiveMinimum"]:
            raise CapabilityError(f"{path} is below exclusive minimum")
        if "exclusiveMaximum" in definition and num_val >= definition["exclusiveMaximum"]:
            raise CapabilityError(f"{path} is above exclusive maximum")
    if isinstance(value, str):
        if "minLength" in definition and len(value) < definition["minLength"]:
            raise CapabilityError(f"{path} is too short")
        if "maxLength" in definition and len(value) > definition["maxLength"]:
            raise CapabilityError(f"{path} is too long")
    if isinstance(value, list):
        if "minItems" in definition and len(value) < definition["minItems"]:
            raise CapabilityError(f"{path} has too few items")
        if "maxItems" in definition and len(value) > definition["maxItems"]:
            raise CapabilityError(f"{path} has too many items")
        if "items" in definition:
            for index, item in enumerate(value):
                _validate_schema(item, definition["items"], f"{path}[{index}]")
    if isinstance(value, dict) and isinstance(definition.get("properties"), dict):
        required = definition.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise CapabilityError(f"{path} requires parameters: {', '.join(missing)}")
        properties = definition["properties"]
        if definition.get("additionalProperties") is False:
            extra = [name for name in value if name not in properties]
            if extra:
                raise CapabilityError(f"{path} does not accept parameters: {', '.join(extra)}")
        for name, item in value.items():
            if name in properties:
                _validate_schema(item, properties[name], f"{path}.{name}")


async def _off_loop(function: Callable[..., Any], *args: Any) -> Any:
    result = await asyncio.to_thread(function, *args)
    if inspect.isawaitable(result):
        return await result
    return result


async def _close_candidate(candidate: Any) -> None:
    if candidate is None:
        return
    close = getattr(candidate, "close", None)
    if close is None:
        return
    try:
        result = await _off_loop(close)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001,S110
        pass


__all__ = ["BackendDevice", "BackendPolicy"]
