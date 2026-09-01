import asyncio
import threading
import time

import pytest

from openmhs.adapters.visa import VisaSession, VisaWriteError, VisaWriteOutcome


class Instrument:
    def __init__(self):
        self.timeout = None
        self.close_count = 0
        self.query_started = threading.Event()
        self.query_finished = threading.Event()
        self.write_calls = 0

    def query(self, command):
        self.query_started.set()
        time.sleep(0.03)
        self.query_finished.set()
        return "ok"

    def write(self, command):
        self.write_calls += 1
        raise RuntimeError("Invalid session")

    def close(self):
        self.close_count += 1


class Manager:
    def __init__(self, instrument):
        self.instrument = instrument
        self.close_count = 0

    def open_resource(self, resource):
        return self.instrument

    def close(self):
        self.close_count += 1


@pytest.mark.asyncio
async def test_cancelled_query_drains_worker_before_lock_is_released():
    instrument = Instrument()
    manager = Manager(instrument)
    session = VisaSession("resource", lambda: manager)
    await session.open()

    query = asyncio.create_task(session.query("*IDN?"))
    await asyncio.to_thread(instrument.query_started.wait)
    query.cancel()
    with pytest.raises(asyncio.CancelledError):
        await query
    assert instrument.query_finished.is_set()

    # The lock was not released while the physical query was still running.
    await session.close()
    assert instrument.close_count == 1
    assert manager.close_count == 1


@pytest.mark.asyncio
async def test_write_failure_is_typed_and_never_replayed():
    instrument = Instrument()
    manager = Manager(instrument)
    session = VisaSession("resource", lambda: manager)
    await session.open()

    with pytest.raises(VisaWriteError) as raised:
        await session.write("CHANGE STATE")
    assert raised.value.outcome is VisaWriteOutcome.UNKNOWN
    assert instrument.write_calls == 1
    assert manager.close_count == 0


@pytest.mark.asyncio
async def test_timeout_failure_closes_instrument_and_manager():
    # Use an instrument whose timeout setter fails after the resource opens.
    class BadInstrument(Instrument):
        @property
        def timeout(self):
            return None

        @timeout.setter
        def timeout(self, value):
            if value is not None:
                raise RuntimeError("timeout failed")

    bad = BadInstrument()
    manager = Manager(bad)
    session = VisaSession("resource", lambda: manager)
    with pytest.raises(RuntimeError, match="timeout failed"):
        await session.open()
    assert bad.close_count == 1
    assert manager.close_count == 1
    assert not session.is_open


@pytest.mark.asyncio
async def test_partial_open_preserves_primary_and_cleanup_diagnostics():
    class BrokenInstrument(Instrument):
        @property
        def timeout(self):
            return None

        @timeout.setter
        def timeout(self, value):
            if value is not None:
                raise RuntimeError("timeout failed")

        def close(self):
            self.close_count += 1
            raise RuntimeError("instrument close failed")

    class BrokenManager(Manager):
        def close(self):
            self.close_count += 1
            raise RuntimeError("manager close failed")

    instrument = BrokenInstrument()
    manager = BrokenManager(instrument)
    session = VisaSession("resource", lambda: manager)
    with pytest.raises(RuntimeError, match="timeout failed") as raised:
        await session.open()
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert instrument.close_count == 1
    assert manager.close_count == 1


@pytest.mark.asyncio
async def test_write_timeout_is_unknown_and_never_replayed():
    class TimeoutInstrument(Instrument):
        def write(self, command):
            self.write_calls += 1
            raise TimeoutError("write timed out")

    instrument = TimeoutInstrument()
    session = VisaSession("resource", lambda: Manager(instrument))
    await session.open()
    with pytest.raises(VisaWriteError) as raised:
        await session.write("CHANGE STATE")
    assert raised.value.outcome is VisaWriteOutcome.UNKNOWN
    assert instrument.write_calls == 1


@pytest.mark.asyncio
async def test_local_write_rejection_is_not_sent():
    instrument = Instrument()
    session = VisaSession("resource", lambda: Manager(instrument))
    await session.open()
    with pytest.raises(VisaWriteError) as raised:
        await session.write("")
    assert raised.value.outcome is VisaWriteOutcome.NOT_SENT
    assert instrument.write_calls == 0


@pytest.mark.asyncio
async def test_write_when_closed_is_typed_not_sent():
    session = VisaSession("resource", lambda: Manager(Instrument()))
    with pytest.raises(VisaWriteError) as raised:
        await session.write("CHANGE STATE")
    assert raised.value.outcome is VisaWriteOutcome.NOT_SENT


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf"), 10**1000, 3_600_001])
def test_timeout_validation_rejects_unsafe_values(timeout):
    with pytest.raises(ValueError):
        VisaSession("resource", lambda: Manager(Instrument()), timeout_ms=timeout)
