"""Async, serialized access to an optional PyVISA resource."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from enum import Enum
from typing import Any, cast

logger = logging.getLogger(__name__)

# PyVISA accepts milliseconds, but an unbounded value can overflow backend APIs.
MAX_TIMEOUT_MS = 3_600_000

try:
    import pyvisa
except ImportError:  # pragma: no cover
    pyvisa = None  # type: ignore[assignment]


class VisaWriteOutcome(str, Enum):
    """What can be safely concluded after a VISA write failure."""

    UNKNOWN = "UNKNOWN"
    NOT_SENT = "NOT_SENT"


class VisaWriteError(RuntimeError):
    """A write failed without a safe replay decision."""

    def __init__(
        self, message: str, *, outcome: VisaWriteOutcome, cause: BaseException | None = None
    ):
        super().__init__(message)
        self.outcome = outcome
        self.cause = cause


class VisaCleanupError(RuntimeError):
    """One or more VISA resource cleanup operations failed."""

    def __init__(self, errors: tuple[BaseException, ...]):
        super().__init__("VISA resource cleanup failed")
        self.errors = errors


def validate_timeout_ms(timeout_ms: Any) -> int:
    """Validate a bounded timeout accepted by VISA backends."""
    valid_timeout = False
    if not isinstance(timeout_ms, bool) and isinstance(timeout_ms, (int, float)):
        try:
            valid_timeout = (
                math.isfinite(float(timeout_ms))
                and timeout_ms > 0
                and timeout_ms <= MAX_TIMEOUT_MS
                and int(timeout_ms) == timeout_ms
            )
        except (OverflowError, TypeError, ValueError):
            valid_timeout = False
    if not valid_timeout:
        raise ValueError(f"timeout_ms must be a finite positive integer <= {MAX_TIMEOUT_MS}")
    return int(timeout_ms)


def _with_cleanup_diagnostic(primary: BaseException, cleanup: BaseException) -> BaseException:
    """Keep the primary failure while exposing cleanup as its explicit cause."""
    primary.__cause__ = cleanup
    primary.__suppress_context__ = True
    return primary


def probe_resource(manager: Any, resource: str, timeout_ms: int = 2000) -> str:
    """Query a resource identity and always release its instrument handle.

    Discovery calls this from its worker thread, so all PyVISA operations stay
    off the event loop while using the same timeout and cleanup convention as
    :class:`VisaSession`.
    """
    timeout_ms = validate_timeout_ms(timeout_ms)
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
                    failure = _with_cleanup_diagnostic(failure, exc)
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
        self.timeout_ms = validate_timeout_ms(timeout_ms)
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

    async def _run_worker(self, operation: Callable[[], Any]) -> Any:
        """Run one physical call and drain it before propagating cancellation."""
        worker = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    logger.exception("VISA worker failed while cancellation was draining")
                    break
            # Consume the worker exception before returning cancellation.  This
            # also prevents "Task exception was never retrieved" warnings.
            if not worker.cancelled():
                worker.exception()
            raise

    async def query(self, command: str) -> str:
        async with self._lock:
            if self._instrument is None:
                raise RuntimeError("VISA session is not open")
            try:
                return cast(str, await self._run_worker(lambda: self._instrument.query(command)))
            except Exception as exc:
                if not self._is_invalid_session(exc):
                    raise
                await self._reopen_locked()
                # A query is safe to retry after a stale handle, but only once.
                return cast(str, await self._run_worker(lambda: self._instrument.query(command)))

    async def write(self, command: str) -> None:
        # These checks are the only failures that can be known to happen before
        # touching the instrument.  Once write() is called, delivery is unknown.
        if not isinstance(command, str) or not command.strip():
            error: BaseException = TypeError("VISA command must be a non-empty string")
            raise VisaWriteError(
                "VISA write rejected before send",
                outcome=VisaWriteOutcome.NOT_SENT,
                cause=error,
            ) from error
        async with self._lock:
            if self._instrument is None:
                error = RuntimeError("VISA session is not open")
                raise VisaWriteError(
                    "VISA write rejected before send",
                    outcome=VisaWriteOutcome.NOT_SENT,
                    cause=error,
                ) from error
            try:
                await self._run_worker(lambda: self._instrument.write(command))
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                # No backend exception proves that the command was not received.
                # In particular, stale-session and timeout errors are not replayed.
                raise VisaWriteError(
                    f"VISA write failed ({VisaWriteOutcome.UNKNOWN.value}): {command}",
                    outcome=VisaWriteOutcome.UNKNOWN,
                    cause=exc,
                ) from exc

    async def close(self) -> None:
        async with self._lock:
            instrument = self._instrument
            manager = self._resource_manager
            self._instrument = None
            self._resource_manager = None
            await self._run_worker(lambda: self._close_resources(instrument, manager))

    async def _open_locked(self) -> None:
        if self._resource_manager_factory is None:
            raise RuntimeError("PyVISA is not installed")
        manager_holder: dict[str, Any] = {}

        def create_manager() -> Any:
            manager_holder["value"] = self._resource_manager_factory()
            return manager_holder["value"]

        instrument_holder: dict[str, Any] = {}
        manager = None
        instrument = None

        try:
            manager = await self._run_worker(create_manager)

            def open_instrument() -> Any:
                instrument_holder["value"] = manager.open_resource(self.resource)
                return instrument_holder["value"]

            instrument = await self._run_worker(open_instrument)
            await self._run_worker(lambda: self._set_timeout(instrument))
        except BaseException as primary:
            # A timeout setter failure is just as fatal as an open failure;
            # release both handles, including handles returned by a cancelled worker.
            cleanup_error: BaseException | None = None
            try:
                await self._run_worker(
                    lambda: self._close_resources(
                        instrument if instrument is not None else instrument_holder.get("value"),
                        manager if manager is not None else manager_holder.get("value"),
                    )
                )
            except BaseException as exc:
                cleanup_error = exc
                logger.exception("Failed to clean up a partially opened VISA session")
            if cleanup_error is not None and not isinstance(primary, asyncio.CancelledError):
                raise _with_cleanup_diagnostic(primary, cleanup_error) from primary
            if isinstance(primary, asyncio.CancelledError):
                raise
            raise
        self._resource_manager = manager
        self._instrument = instrument

    async def _reopen_locked(self) -> None:
        instrument = self._instrument
        manager = self._resource_manager
        self._instrument = None
        self._resource_manager = None
        try:
            await self._run_worker(lambda: self._close_resources(instrument, manager))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to close stale VISA handles: %s", exc)
        await self._open_locked()

    def _set_timeout(self, instrument: Any) -> None:
        instrument.timeout = self.timeout_ms

    @staticmethod
    def _is_invalid_session(exc: Exception) -> bool:
        """Recognize the explicit stale-handle errors exposed by VISA backends."""
        error_code = getattr(exc, "error_code", None)
        if error_code is not None:
            if error_code == getattr(
                getattr(pyvisa, "constants", None), "VI_ERROR_INV_OBJECT", object()
            ):
                return True
            if "VI_ERROR_INV_OBJECT" in str(error_code):
                return True
        text = str(exc).casefold()
        return "invalid session" in text or "vi_error_inv_object" in text

    @staticmethod
    def _close_resources(instrument: Any, manager: Any) -> None:
        errors: list[BaseException] = []
        if instrument is not None:
            try:
                instrument.close()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
        if manager is not None:
            try:
                manager.close()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            cleanup = VisaCleanupError(tuple(errors))
            cleanup.__cause__ = errors[0]
            cleanup.__suppress_context__ = True
            raise cleanup
