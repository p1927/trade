"""Job status rollup helpers — detect silent ok / skipped-with-failure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class JobRollup:
    status: str
    had_errors: bool = False
    had_work: bool = True
    children_failed: int = 0
    job_type: str = ""
    job_id: str = ""
    expected_work: bool = True
    detail: dict[str, Any] | None = None

    def silent_failure(self) -> bool:
        """True when outward status looks fine but work failed or never ran."""
        if self.had_errors:
            return True
        if self.status in {"ok", "partial", "skipped"} and self.expected_work and not self.had_work:
            return True
        return False
