"""Async, serialized access to an optional PyVISA resource."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, cast

logger = logging.getLogger(__name__)

try:
    import pyvisa
except ImportError:  # pragma: no cover
    pyvisa = None  # type: ignore[assignment]


def probe_resource(manager: Any, resource: str, timeout_ms: int = 2000) -> str:
    """Query a resource identity and always release its instrument handle.

    Discovery calls this from its worker thread, so all PyVISA operations stay
    off the event loop while using the same timeout and cleanup convention as
    :class:`VisaSession`.
    """
    instrument = None
    identity: str | None = None
    failure: BaseException | None = None
    try:
        instrument = manager.open_resource(resource)
        instrument.timeout = timeout_ms
        identity = cast(str, instrument.query("*IDN?")).strip()
    except BaseException as exc:  # noqa: BLE001 - preserve worker-thread failures
        failure = exc
    finally:
        if instrument is not None:
            try:
                instrument.close()
            except BaseException as exc:  # noqa: BLE001 - report close failures too
                if failure is None:
                    failure = exc
                else:
                    failure = RuntimeError(f"{failure}; {exc}")
    if failure is not None:
        raise failure
    assert identity is not None
    return identity


class VisaSession:
    """Own a VISA resource manager and instrument without exposing PyVISA types."""

    def __init__(
        self,
        resource: str,
        resource_manager_factory: Callable[[], Any] | None = None,
        timeout_ms: int = 2000,
    ) -> None:
        self.resource = resource
        self.timeout_ms = timeout_ms
        self._resource_manager_factory = (
            resource_manager_factory
            if resource_manager_factory is not None
            else (pyvisa.ResourceManager if pyvisa is not None else None)
        )
        self._resource_manager: Any = None
        self._instrument: Any = None
        self._lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        return self._instrument is not None

    async def open(self) -> None:
        async with self._lock:
            if self._instrument is not None:
                return
            await self._open_locked()

    async def query(self, command: str) -> str:
        async with self._lock:
            if self._instrument is None:
                raise RuntimeError("VISA session is not open")
            try:
                return await asyncio.to_thread(self._instrument.query, command)
            except Exception as exc:
                if not self._is_invalid_session(exc):
                    raise
                await self._reopen_locked()
                return await asyncio.to_thread(self._instrument.query, command)

    async def write(self, command: str) -> None:
        async with self._lock:
            if self._instrument is None:
                raise RuntimeError("VISA session is not open")
            try:
                await asyncio.to_thread(self._instrument.write, command)
            except Exception as exc:
                if not self._is_invalid_session(exc):
                    raise
                await self._reopen_locked()
                await asyncio.to_thread(self._instrument.write, command)

    async def close(self) -> None:
        async with self._lock:
            instrument = self._instrument
            manager = self._resource_manager
            self._instrument = None
            self._resource_manager = None
            await asyncio.to_thread(self._close_resources, instrument, manager)

    async def _open_locked(self) -> None:
        if self._resource_manager_factory is None:
            raise RuntimeError("PyVISA is not installed")
        manager = await asyncio.to_thread(self._resource_manager_factory)
        try:
            instrument = await asyncio.to_thread(manager.open_resource, self.resource)
            await asyncio.to_thread(self._set_timeout, instrument)
        except BaseException:
            await asyncio.to_thread(self._close_resources, None, manager)
            raise
        self._resource_manager = manager
        self._instrument = instrument

    async def _reopen_locked(self) -> None:
        instrument = self._instrument
        manager = self._resource_manager
        self._instrument = None
        self._resource_manager = None
        try:
            await asyncio.to_thread(self._close_resources, instrument, manager)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to close stale VISA handles: %s", exc)
        await self._open_locked()

    def _set_timeout(self, instrument: Any) -> None:
        instrument.timeout = self.timeout_ms

    @staticmethod
    def _is_invalid_session(exc: Exception) -> bool:
        text = str(exc)
        return "Invalid session" in text or "VI_ERROR_INV_OBJECT" in text

    @staticmethod
    def _close_resources(instrument: Any, manager: Any) -> None:
        first_error: BaseException | None = None
        if instrument is not None:
            try:
                instrument.close()
            except BaseException as exc:  # noqa: BLE001
                first_error = exc
        if manager is not None:
            try:
                manager.close()
            except BaseException as exc:  # noqa: BLE001
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
