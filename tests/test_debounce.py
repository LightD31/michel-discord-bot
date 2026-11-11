"""A burst of reactions folds into a few refreshes (B10)."""

import asyncio

from src.core.debounce import Coalescer


class Recorder:
    def __init__(self, delay=0.0):
        self.runs = []
        self.delay = delay

    def refresh(self, label):
        async def run():
            self.runs.append(label)
            await asyncio.sleep(self.delay)

        return run


async def settle(coalescer):
    for _ in range(100):
        if not coalescer.pending():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the refreshes never finished")


async def test_a_burst_runs_once_with_the_latest_refresh():
    coalescer = Coalescer(0.02, name="test")
    recorder = Recorder()
    for i in range(10):
        coalescer.request("poll", recorder.refresh(i))
    await settle(coalescer)
    assert recorder.runs == [9]


async def test_a_request_during_a_run_gets_one_more_run():
    coalescer = Coalescer(0.01, name="test")
    recorder = Recorder(delay=0.05)
    coalescer.request("poll", recorder.refresh("first"))
    await asyncio.sleep(0.03)  # the first run has started
    coalescer.request("poll", recorder.refresh("second"))
    coalescer.request("poll", recorder.refresh("third"))
    await settle(coalescer)
    assert recorder.runs == ["first", "third"]


async def test_keys_run_independently():
    coalescer = Coalescer(0.01, name="test")
    recorder = Recorder()
    coalescer.request("a", recorder.refresh("a"))
    coalescer.request("b", recorder.refresh("b"))
    await settle(coalescer)
    assert sorted(recorder.runs) == ["a", "b"]


async def test_a_failing_refresh_doesnt_stop_the_next(caplog):
    coalescer = Coalescer(0.01, name="test")
    recorder = Recorder()

    async def boom():
        raise RuntimeError("Discord said no")

    coalescer.request("poll", boom)
    await settle(coalescer)
    coalescer.request("poll", recorder.refresh("after"))
    await settle(coalescer)
    assert recorder.runs == ["after"]
    assert "refresh of poll failed" in caplog.text
