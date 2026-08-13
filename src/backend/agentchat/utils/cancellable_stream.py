import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from agentchat.utils.events import current_trace_id


class CancellableAsyncStream:
    """Run a producer in a cancellable task and expose a synchronous cancel hook."""

    def __init__(
        self,
        producer: Callable[[asyncio.Queue], Awaitable[None]],
        sentinel: Any = None,
    ):
        self._producer = producer
        self._sentinel = sentinel
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cancel_event = asyncio.Event()
        self._run_task: Optional[asyncio.Task] = None
        self._started_at: Optional[float] = None
        self._cancel_requested_at: Optional[float] = None
        self._finished_at: Optional[float] = None
        self._reason: Optional[str] = None
        self._error: Optional[BaseException] = None

    def start(self) -> "CancellableAsyncStream":
        if self._run_task is None:
            self._started_at = time.perf_counter()
            self._run_task = asyncio.create_task(self._run())
        return self

    async def _run(self) -> None:
        try:
            await self._producer(self._queue)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = exc
        finally:
            self._queue.put_nowait(self._sentinel)

    def request_cancel(self) -> None:
        if not self._cancel_event.is_set():
            self._cancel_requested_at = time.perf_counter()
            self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    async def _finish(self, reason: str) -> None:
        if self._finished_at is not None:
            return
        self._reason = reason
        self._finished_at = time.perf_counter()
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass

    def __aiter__(self) -> "CancellableAsyncStream":
        self.start()
        return self

    async def __anext__(self) -> Any:
        if self._run_task is None:
            self.start()

        while True:
            get_task = asyncio.create_task(self._queue.get())
            cancel_task = asyncio.create_task(self._cancel_event.wait())
            done, pending = await asyncio.wait(
                {get_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if cancel_task in done and self._cancel_event.is_set():
                await self._finish("cancelled")
                raise StopAsyncIteration

            if get_task in done:
                item = get_task.result()
                if item is self._sentinel:
                    await self._finish("completed")
                    if self._error is not None:
                        raise self._error
                    raise StopAsyncIteration
                return item

    def summary(self) -> Optional[Dict[str, Any]]:
        if self._finished_at is None:
            return None

        total_ms = (self._finished_at - self._started_at) * 1000
        cancel_ms = None
        if self._cancel_requested_at is not None:
            cancel_ms = (self._finished_at - self._cancel_requested_at) * 1000

        return {
            "cancelled": self._reason == "cancelled",
            "reason": self._reason,
            "total_duration_ms": round(total_ms, 3),
            "cancel_to_terminate_ms": (
                round(cancel_ms, 3) if cancel_ms is not None else None
            ),
            "trace_id": current_trace_id(),
        }
