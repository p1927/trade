"""Detect runaway loops and emit structured loop events."""

from __future__ import annotations

from trade_integrations.observability.emitter import emit
from trade_integrations.observability.schema import ObservabilityModule


class LoopLimitReached(RuntimeError):
    """Raised when a guarded loop exceeds max_iterations in strict mode."""


class LoopGuard:
    """Call ``tick()`` each loop iteration; emits events and stops at limit."""

    def __init__(
        self,
        loop_name: str,
        *,
        module: ObservabilityModule = "system",
        max_iterations: int = 100,
        warn_every: int = 0,
        strict: bool = False,
    ) -> None:
        self.loop_name = loop_name
        self.module = module
        self.max_iterations = max(1, max_iterations)
        self.warn_every = warn_every
        self.strict = strict
        self.iteration = 0
        self._limit_logged = False

    def tick(self) -> bool:
        """Advance one iteration. Returns False when limit reached."""
        self.iteration += 1
        if self.warn_every and self.iteration % self.warn_every == 0:
            emit(
                self.module,
                "loop_iteration",
                level="warn",
                detail={"loop_name": self.loop_name, "iteration": self.iteration},
            )
        elif self.iteration == 1 or self.iteration % max(1, self.max_iterations // 2) == 0:
            emit(
                self.module,
                "loop_iteration",
                level="info",
                detail={"loop_name": self.loop_name, "iteration": self.iteration},
            )

        if self.iteration >= self.max_iterations and not self._limit_logged:
            self._limit_logged = True
            emit(
                self.module,
                "loop_limit_reached",
                level="error",
                detail={"loop_name": self.loop_name, "iteration": self.iteration},
            )
            if self.strict:
                raise LoopLimitReached(
                    f"{self.loop_name} exceeded {self.max_iterations} iterations"
                )
            return False
        return self.iteration < self.max_iterations
