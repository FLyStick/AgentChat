import anyio
from functools import partial
from loguru import logger
from typing import Callable
from starlette._utils import collapse_excgroups
from starlette.types import Receive, Scope, Send
from fastapi.responses import StreamingResponse
from agentchat.utils.events import current_trace_id


class WatchedStreamingResponse(StreamingResponse):
    """
    重写 StreamingResponse类 保证流式输出的时候可随时暂停
    """
    def __init__(
        self,
        content,
        callback: Callable = None,
        status_code: int = 200,
        headers = None,
        media_type: str | None = None,
        background = None,
    ):
        super().__init__(content, status_code, headers, media_type, background)

        self.callback = callback

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        # Always run the disconnect listener in a task group, even on ASGI
        # spec >= 2.4 where Starlette otherwise skips it. This guarantees a
        # real SSE disconnect cancels the in-flight streaming task.
        with collapse_excgroups():
            async with anyio.create_task_group() as task_group:

                async def wrap(func):
                    await func()
                    task_group.cancel_scope.cancel()

                task_group.start_soon(wrap, partial(self.stream_response, send))
                await wrap(partial(self.listen_for_disconnect, receive))

        if self.background is not None:
            await self.background()

    async def listen_for_disconnect(self, receive: Receive) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                logger.info(f"http.disconnect. stop task and streaming. trace_id={current_trace_id()}")

                if self.callback:
                    self.callback()

                break

    async def stream_response(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        try:
            async for chunk in self.body_iterator:
                if not isinstance(chunk, (bytes, memoryview)):
                    chunk = chunk.encode(self.charset)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        finally:
            # Starlette cancels the response task when the client disconnects and
            # may leave the body generator suspended. Close it explicitly so the
            # completion handler can persist history/stream_cancel before the
            # request is torn down.
            with anyio.CancelScope(shield=True):
                try:
                    aclose = getattr(self.body_iterator, "aclose", None)
                    if aclose is not None:
                        await aclose()
                except BaseException as exc:
                    logger.warning(
                        f"Failed to finalize streaming body on disconnect: {type(exc).__name__}: {exc}"
                    )

        await send({"type": "http.response.body", "body": b"", "more_body": False})
