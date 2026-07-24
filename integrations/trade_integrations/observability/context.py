"""Correlation context propagated through watch / job / LLM flows."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Iterator

_obs_ctx: ContextVar["ObservabilityContext | None"] = ContextVar("trade_observability_ctx", default=None)


@dataclass(frozen=True)
class ObservabilityContext:
    trace_id: str = ""
    agent_id: str = ""
    session_id: str = ""
    job_id: str = ""
    ticker: str = ""


def observability_context() -> ObservabilityContext:
    return _obs_ctx.get() or ObservabilityContext()


def set_observability_context(**kwargs: str) -> None:
    current = observability_context()
    _obs_ctx.set(replace(current, **{k: v for k, v in kwargs.items() if v is not None}))


@contextmanager
def bind_observability_context(**kwargs: str) -> Iterator[None]:
    token = _obs_ctx.set(replace(observability_context(), **kwargs))
    try:
        yield
    finally:
        _obs_ctx.reset(token)
