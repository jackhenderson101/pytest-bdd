import asyncio
import threading
import time

import pytest

from background_loop import BackgroundLoop, background_loop  # noqa: F401

pytest_plugins = ["pytester"]


def test_runs_in_parallel_with_the_test_body(background_loop: BackgroundLoop) -> None:
    ticks = []

    async def ticker() -> str:
        for _ in range(5):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)
        return "done"

    future = background_loop.submit(ticker())
    # Main thread keeps working while the loop ticks.
    time.sleep(0.06)
    assert ticks, "background loop made no progress during the test body"
    assert future.result(1) == "done"


def test_runs_on_a_different_thread(background_loop: BackgroundLoop) -> None:
    async def who() -> int:
        return threading.get_ident()

    assert background_loop.run(who(), timeout=1) != threading.get_ident()


def test_run_propagates_exceptions(background_loop: BackgroundLoop) -> None:
    async def boom() -> None:
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        background_loop.run(boom(), timeout=1)


def test_run_times_out_and_cancels(background_loop: BackgroundLoop) -> None:
    cancelled = threading.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(TimeoutError):
        background_loop.run(slow(), timeout=0.05)
    assert cancelled.wait(1)


def test_call_soon_runs_sync_callback(background_loop: BackgroundLoop) -> None:
    seen = threading.Event()
    background_loop.call_soon(seen.set)
    assert seen.wait(1)


def test_teardown_cancels_pending_tasks_and_rejects_new_work(pytester: pytest.Pytester) -> None:
    pytester.makeconftest("from background_loop import background_loop  # noqa: F401")
    pytester.makepyfile(
        """
        import asyncio, pytest

        handle = None

        def test_leaves_a_task_running(background_loop):
            global handle
            handle = background_loop
            background_loop.submit(asyncio.sleep(30))

        def test_loop_is_unusable_afterwards():
            with pytest.raises(RuntimeError, match="already been shut down"):
                handle.submit(asyncio.sleep(0))
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)


def test_unhandled_background_error_fails_teardown(pytester: pytest.Pytester) -> None:
    pytester.makeconftest("from background_loop import background_loop  # noqa: F401")
    pytester.makepyfile(
        """
        import asyncio

        def test_fire_and_forget(background_loop):
            async def spawn():
                asyncio.get_running_loop().create_task(explode())
            async def explode():
                raise RuntimeError("silent failure")
            background_loop.run(spawn(), timeout=1)
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*unhandled exception(s) in background event loop*"])
