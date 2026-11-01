"""Server-Sent Events helpers: a queue-backed stream with sequence numbers and idle pings."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi.responses import StreamingResponse

PING_S = 15.0
_END = object()


def format_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


class EventChannel:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.seq = 0

    async def emit(self, event: str, data: dict) -> None:
        self.seq += 1
        await self.q.put((event, {"seq": self.seq, **data}))

    async def close(self) -> None:
        await self.q.put(_END)

    async def stream(self) -> AsyncIterator[bytes]:
        while True:
            try:
                item = await asyncio.wait_for(self.q.get(), PING_S)
            except asyncio.TimeoutError:
                yield b": ping\n\n"
                continue
            if item is _END:
                return
            yield format_event(*item)


def sse_response(gen: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
