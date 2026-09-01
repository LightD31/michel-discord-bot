"""Tests for the fire-and-forget background task helper."""

import asyncio
import gc
import logging

from src.core import tasks
from src.core.tasks import pending_task_count, spawn


async def test_task_is_held_alive_until_it_finishes():
    """The loop only holds tasks weakly; spawn() must hold a strong reference."""
    started = asyncio.Event()
    done = asyncio.Event()

    async def work():
        started.set()
        await asyncio.sleep(0)
        done.set()

    spawn(work(), name="held")  # deliberately not keeping the returned handle
    await started.wait()
    assert pending_task_count() == 1

    gc.collect()  # an unreferenced task would be collectable right here

    await asyncio.wait_for(done.wait(), timeout=1)
    await asyncio.sleep(0)
    assert pending_task_count() == 0


async def test_the_handle_is_still_returned_for_callers_that_want_it():
    async def work():
        return 7

    task = spawn(work(), name="returns")
    assert await task == 7


async def test_failures_are_logged_instead_of_vanishing(caplog):
    async def boom():
        raise ValueError("eventsub died")

    with caplog.at_level(logging.ERROR, logger=tasks.logger.name):
        spawn(boom(), name="boom")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert any("boom" in r.message and "eventsub died" in r.message for r in caplog.records)
    assert pending_task_count() == 0


async def test_a_caller_supplied_logger_is_used(caplog):
    custom = logging.getLogger("tests.spawn.custom")

    async def boom():
        raise RuntimeError("nope")

    with caplog.at_level(logging.ERROR, logger=custom.name):
        spawn(boom(), name="custom-logger", log=custom)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert any(r.name == custom.name for r in caplog.records)


async def test_cancellation_is_not_reported_as_a_failure(caplog):
    async def forever():
        await asyncio.sleep(3600)

    with caplog.at_level(logging.ERROR, logger=tasks.logger.name):
        task = spawn(forever(), name="cancelled")
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert caplog.records == []
    assert pending_task_count() == 0
