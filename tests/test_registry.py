import asyncio

import pytest

from openmhs.core.device import DeviceMetadata, DeviceState
from openmhs.core.registry import DeviceRegistry


class FakeDevice:
    def __init__(self, device_id):
        self.metadata = DeviceMetadata(device_id, "fake")
        self.state = DeviceState.ONLINE
        self.closed = 0

    async def close(self):
        self.closed += 1
        self.state = DeviceState.OFFLINE

    async def health_check(self):
        return {"healthy": self.state is DeviceState.ONLINE}

    async def reset(self):
        return True


@pytest.mark.asyncio
async def test_owned_replacement_closes_old_outside_lock_and_detach_never_closes():
    registry = DeviceRegistry()
    old, new = FakeDevice("same"), FakeDevice("same")
    await registry.register(old, owned=True)
    await registry.replace(new, owned=False)
    assert old.closed == 1
    assert registry.get_device("same") is new
    assert await registry.detach_if_same("same", new)
    assert new.closed == 0


@pytest.mark.asyncio
async def test_close_all_removes_atomically_and_only_closes_owned():
    registry = DeviceRegistry()
    owned, borrowed = FakeDevice("owned"), FakeDevice("borrowed")
    await registry.register(owned, owned=True)
    await registry.register(borrowed)
    await registry.close_all()
    assert await registry.snapshot() == ()
    assert owned.closed == 1
    assert borrowed.closed == 0


@pytest.mark.asyncio
async def test_reattach_waits_for_old_owned_cleanup_before_indexing_same_object():
    class BlockingDevice(FakeDevice):
        def __init__(self, device_id):
            super().__init__(device_id)
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()

        async def close(self):
            self.closed += 1
            self.close_started.set()
            await self.release_close.wait()
            self.state = DeviceState.OFFLINE

    registry = DeviceRegistry()
    old, replacement = BlockingDevice("same"), FakeDevice("same")
    await registry.register(old, owned=True)
    replacing = asyncio.create_task(registry.replace(replacement, owned=False))
    await old.close_started.wait()

    reattaching = asyncio.create_task(registry.register(old, owned=True))
    await asyncio.sleep(0)
    assert not reattaching.done()
    assert registry.get_device("same") is replacement

    old.release_close.set()
    await replacing
    await reattaching
    assert registry.get_device("same") is old
    assert old.closed == 1


@pytest.mark.asyncio
async def test_close_exception_still_notifies_after_committed_replace():
    class BrokenClose(FakeDevice):
        async def close(self):
            self.closed += 1
            raise RuntimeError("close failed")

    registry = DeviceRegistry()
    notifications = 0

    async def listener():
        nonlocal notifications
        notifications += 1

    registry.add_change_listener(listener)
    old, new = BrokenClose("same"), FakeDevice("same")
    await registry.register(old, owned=True)
    notifications = 0
    with pytest.raises(RuntimeError):
        await registry.replace(new)
    assert registry.get_device("same") is new
    assert notifications == 1


@pytest.mark.asyncio
async def test_cancelled_cleanup_still_notifies_after_commit():
    class BlockingDevice(FakeDevice):
        def __init__(self, device_id):
            super().__init__(device_id)
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()

        async def close(self):
            self.closed += 1
            self.close_started.set()
            await self.release_close.wait()
            self.state = DeviceState.OFFLINE

    registry = DeviceRegistry()
    notifications = 0

    async def listener():
        nonlocal notifications
        notifications += 1

    registry.add_change_listener(listener)
    old, new = BlockingDevice("same"), FakeDevice("same")
    await registry.register(old, owned=True)
    notifications = 0
    replacing = asyncio.create_task(registry.replace(new))
    await old.close_started.wait()
    replacing.cancel()
    old.release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await replacing
    assert registry.get_device("same") is new
    assert notifications == 1


@pytest.mark.asyncio
async def test_close_all_cancellation_still_notifies_after_atomic_clear():
    class BlockingDevice(FakeDevice):
        def __init__(self, device_id):
            super().__init__(device_id)
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()

        async def close(self):
            self.closed += 1
            self.close_started.set()
            await self.release_close.wait()
            self.state = DeviceState.OFFLINE

    registry = DeviceRegistry()
    notifications = 0

    async def listener():
        nonlocal notifications
        notifications += 1

    registry.add_change_listener(listener)
    device = BlockingDevice("owned")
    await registry.register(device, owned=True)
    notifications = 0
    closing = asyncio.create_task(registry.close_all())
    await device.close_started.wait()
    closing.cancel()
    device.release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert await registry.snapshot() == ()
    assert notifications == 1


@pytest.mark.asyncio
async def test_close_all_repeated_cancel_drains_failing_physical_close():
    class FailingBlockingDevice(FakeDevice):
        def __init__(self, device_id):
            super().__init__(device_id)
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()
            self.close_finished = asyncio.Event()

        async def close(self):
            self.closed += 1
            self.close_started.set()
            await self.release_close.wait()
            self.close_finished.set()
            raise RuntimeError("physical close failed")

    registry = DeviceRegistry()
    device = FailingBlockingDevice("owned")
    await registry.register(device, owned=True)
    closing = asyncio.create_task(registry.close_all())
    await device.close_started.wait()
    closing.cancel()
    closing.cancel()
    device.release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert device.close_finished.is_set()
    assert await registry.snapshot() == ()


@pytest.mark.asyncio
async def test_unregister_defaults_to_entry_ownership():
    registry = DeviceRegistry()
    borrowed, owned = FakeDevice("borrowed"), FakeDevice("owned")
    await registry.register(borrowed)
    await registry.register(owned, owned=True)
    await registry.unregister("borrowed")
    await registry.unregister("owned")
    assert borrowed.closed == 0
    assert owned.closed == 1


@pytest.mark.asyncio
async def test_alias_conflicts_cycles_and_deleted_targets_are_rejected_or_cleaned():
    registry = DeviceRegistry()
    first, second = FakeDevice("canonical"), FakeDevice("other")
    await registry.register(first)
    await registry.register(second)
    await registry.add_alias("legacy", "canonical")
    with pytest.raises(ValueError, match="conflicts"):
        await registry.add_alias("other", "canonical")
    await registry.add_alias("legacy-chain", "legacy")
    assert registry.get_device("legacy-chain") is first
    await registry.replace(FakeDevice("canonical"))
    assert registry.get_device("legacy") is None
    assert registry.get_device("legacy-chain") is None

    # A malformed cycle must not hang lookups or be accepted as a new alias.
    registry._aliases.update({"a": "b", "b": "a"})
    with pytest.raises(ValueError, match="cycle"):
        registry.get_device("a")


@pytest.mark.asyncio
async def test_detach_if_same_removes_aliases():
    registry = DeviceRegistry()
    device = FakeDevice("canonical")
    await registry.register(device)
    await registry.add_alias("legacy", "canonical")
    assert await registry.detach_if_same("canonical", device)
    assert registry.get_device("legacy") is None


@pytest.mark.asyncio
async def test_canonical_alias_key_conflicts_and_recursive_cleanup():
    registry = DeviceRegistry()
    canonical = FakeDevice("canonical")
    other = FakeDevice("other")
    await registry.register(canonical)
    await registry.register(other)
    await registry.add_alias("legacy", "canonical")
    await registry.add_alias("legacy", "canonical")
    with pytest.raises(ValueError, match="already points"):
        await registry.add_alias("legacy", "other")

    for operation in (
        registry.register,
        registry.register_if_absent,
        registry.replace,
    ):
        with pytest.raises(ValueError, match="alias key"):
            await operation(FakeDevice("legacy"))
    with pytest.raises(ValueError, match="alias key"):
        await registry.swap_if_same("canonical", canonical, FakeDevice("legacy"))

    # Simulate an existing chain to verify deletion is transitive, not just
    # direct-target cleanup. Registering an old middle ID must not resurrect it.
    registry._aliases.update({"old": "middle", "middle": "canonical"})
    await registry.unregister("canonical")
    assert registry.get_device("old") is None
    await registry.register(FakeDevice("middle"))
    assert registry.get_device("old") is None
