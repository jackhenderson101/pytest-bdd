"""A pytest fixture that runs an asyncio event loop on a background thread.

Lets synchronous tests schedule coroutines that make progress in parallel with
the main test body.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Coroutine, Iterator
from typing import Any, TypeVar

import pytest

T = TypeVar("T")

#: How long to wait for the loop thread to come up / wind down.
_STARTUP_TIMEOUT = 5.0
_SHUTDOWN_TIMEOUT = 10.0


class BackgroundLoop:
    """Handle for an event loop owned by another thread.

    All methods are safe to call from the test (main) thread. Once the fixture
    has torn the loop down, every scheduling method raises ``RuntimeError``
    rather than hanging or silently dropping the coroutine.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
        self._loop = loop
        self._thread = thread
        self._closed = False
        self._errors: list[dict[str, Any]] = []
        self._errors_lock = threading.Lock()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The underlying loop, for APIs that need it (e.g. ``loop.time()``)."""
        return self._loop

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """Schedule *coro* on the background loop and return immediately.

        The returned future resolves on the loop thread; call ``.result()`` on
        it whenever the test is ready to join.
        """
        self._check_open(coro)
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """Schedule *coro* and block until it finishes, re-raising its exception.

        On timeout the coroutine is cancelled on the loop thread so it cannot
        outlive the call, and ``TimeoutError`` is raised.
        """
        future = self.submit(coro)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(f"coroutine did not finish within {timeout}s") from None

    def call_soon(self, callback: Any, *args: Any) -> None:
        """Run a plain (non-async) callback on the loop thread."""
        self._check_open(None)
        self._loop.call_soon_threadsafe(callback, *args)

    def _check_open(self, coro: Coroutine[Any, Any, Any] | None) -> None:
        if self._closed or self._loop.is_closed():
            # Close the coroutine explicitly, otherwise Python emits a
            # "coroutine was never awaited" warning on top of our error.
            if coro is not None:
                coro.close()
            raise RuntimeError("background event loop has already been shut down")

    # -- error collection -------------------------------------------------

    def _on_loop_exception(self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        """Loop-level handler for exceptions nobody retrieved."""
        with self._errors_lock:
            self._errors.append(context)
        loop.default_exception_handler(context)

    def pop_errors(self) -> list[dict[str, Any]]:
        """Return and clear unhandled errors seen by the loop so far."""
        with self._errors_lock:
            errors, self._errors = self._errors, []
        return errors


@pytest.fixture
def background_loop() -> Iterator[BackgroundLoop]:
    """Yield a :class:`BackgroundLoop` running on a daemon thread.

    The loop is stopped at teardown: pending tasks are cancelled, async
    generators and the default executor are shut down, and any exception the
    loop reported but no one retrieved fails the test.
    """
    loop = asyncio.new_event_loop()
    started = threading.Event()
    startup_error: list[BaseException] = []

    def _run() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.call_soon(started.set)
            loop.run_forever()
        except BaseException as exc:  # pragma: no cover - loop bootstrap failure
            startup_error.append(exc)
        finally:
            started.set()

    thread = threading.Thread(target=_run, name="pytest-background-loop", daemon=True)
    handle = BackgroundLoop(loop, thread)
    loop.set_exception_handler(handle._on_loop_exception)

    thread.start()
    if not started.wait(_STARTUP_TIMEOUT):
        loop.call_soon_threadsafe(loop.stop)
        thread.join(_SHUTDOWN_TIMEOUT)
        loop.close()
        raise RuntimeError(f"background event loop did not start within {_STARTUP_TIMEOUT}s")
    if startup_error:
        thread.join(_SHUTDOWN_TIMEOUT)
        loop.close()
        raise RuntimeError("background event loop failed to start") from startup_error[0]

    try:
        yield handle
    finally:
        handle._closed = True
        _shutdown(loop, thread)
        errors = handle.pop_errors()

    if errors:
        messages = [str(ctx.get("exception") or ctx.get("message")) for ctx in errors]
        raise AssertionError(
            "unhandled exception(s) in background event loop:\n  " + "\n  ".join(messages)
        )


def _shutdown(loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
    """Cancel outstanding work, stop the loop, and join its thread."""

    async def _drain() -> None:
        pending = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await loop.shutdown_asyncgens()
        await loop.shutdown_default_executor()

    try:
        if loop.is_running():
            drain = asyncio.run_coroutine_threadsafe(_drain(), loop)
            try:
                drain.result(_SHUTDOWN_TIMEOUT)
            except concurrent.futures.TimeoutError:
                drain.cancel()
    finally:
        # Stop and join even if draining failed, so the thread cannot leak.
        if not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        thread.join(_SHUTDOWN_TIMEOUT)
        if thread.is_alive():
            raise RuntimeError(f"background loop thread did not stop within {_SHUTDOWN_TIMEOUT}s")
        loop.close()
