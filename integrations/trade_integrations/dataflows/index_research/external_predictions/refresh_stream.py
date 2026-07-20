"""SSE streaming for external predictions refresh."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from trade_integrations.dataflows.index_research.external_predictions.refresh import (
    refresh_all_external_predictions,
)
from trade_integrations.dataflows.index_research.external_predictions.refresh_lock import (
    external_refresh_lock,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

_HEARTBEAT_SECONDS = 15.0


def sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _boot_log(message: str, *, symbol: str, horizon_days: int) -> dict[str, Any]:
    return {
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "stage": "refresh",
        "level": "info",
        "message": message,
        "symbol": symbol,
        "horizon_days": horizon_days,
    }


async def stream_external_predictions_refresh(
    *,
    symbol: str,
    horizon_days: int,
    disconnected: Any,
) -> AsyncIterator[str]:
    """Run refresh in a worker thread and yield SSE log/done/error frames."""
    sym = symbol.upper()
    yield sse_frame(
        "log",
        {
            "entry": _boot_log(
                f"Starting external predictions refresh for {sym} ({horizon_days}d horizon)…",
                symbol=sym,
                horizon_days=horizon_days,
            )
        },
    )

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def emit(kind: str, payload: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))

    def on_entry(entry: Any) -> None:
        emit("log", entry.to_dict())

    def worker() -> None:
        pipeline = PipelineLogger(on_entry=on_entry)
        try:
            with external_refresh_lock(symbol=sym, horizon_days=horizon_days):
                snapshot = refresh_all_external_predictions(
                    symbol=sym,
                    horizon_days=horizon_days,
                    pipeline=pipeline,
                )
                emit("done", {"snapshot": snapshot.to_dict(), "ticker": sym})
        except RuntimeError as exc:
            emit("error", str(exc))
        except Exception as exc:
            emit("error", str(exc))

    threading.Thread(target=worker, daemon=True, name=f"ext-pred-refresh-{sym}").start()

    while True:
        if await disconnected():
            return
        try:
            kind, payload = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            yield ": keepalive\n\n"
            continue
        if kind == "log":
            yield sse_frame("log", {"entry": payload})
        elif kind == "done":
            yield sse_frame("done", payload)
            return
        elif kind == "error":
            yield sse_frame("error", {"message": payload})
            return
