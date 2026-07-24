"""IST session clock accelerated by replay speed."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_SESSION_OPEN = time(9, 15)
_SESSION_CLOSE = time(15, 30)


class SimClock:
    def __init__(
        self,
        *,
        replay_date: str,
        replay_time: str = "09:15",
        speed: float = 1.0,
        loop: bool = True,
        stepped: bool = False,
        week_dates: list[str] | None = None,
        week_index: int | None = None,
        on_replay_date_change: Callable[[str, str], None] | None = None,
    ) -> None:
        self.replay_date = replay_date[:10]
        self.replay_time = replay_time
        self.week_dates = [d[:10] for d in (week_dates or []) if d]
        self._week_index = week_index if week_index is not None else self._index_for_date(self.replay_date)
        self.on_replay_date_change = on_replay_date_change
        hour, minute = (int(x) for x in replay_time.split(":", 1))
        self._session_open = datetime.strptime(self.replay_date, "%Y-%m-%d").replace(
            hour=hour, minute=minute, second=0, microsecond=0, tzinfo=IST
        )
        self._session_close = self._session_open.replace(
            hour=_SESSION_CLOSE.hour, minute=_SESSION_CLOSE.minute, second=0, microsecond=0
        )
        self.speed = max(0.0, float(speed))
        self.loop = loop
        self.stepped = stepped
        self._sim_now = self._session_open
        self._wall_anchor: datetime | None = None
        self._paused = False
        self._completed = False

    @property
    def week_mode(self) -> bool:
        return len(self.week_dates) > 1

    def _index_for_date(self, replay_date: str) -> int:
        day = replay_date[:10]
        if day in self.week_dates:
            return self.week_dates.index(day)
        return max(0, len(self.week_dates) - 1) if self.week_dates else 0

    def reset(self) -> None:
        self._sim_now = self._session_open
        self._wall_anchor = None
        self._paused = False
        self._completed = False

    def now_ist(self) -> datetime:
        if self.stepped or self.speed == 0.0:
            self._maybe_load_persisted()
            return self._sim_now
        if self._wall_anchor is None:
            self._wall_anchor = datetime.now(tz=IST)
            return self._sim_now
        elapsed = datetime.now(tz=IST) - self._wall_anchor
        sim_elapsed = timedelta(seconds=elapsed.total_seconds() * self.speed)
        candidate = self._session_open + sim_elapsed
        return self._clamp_to_session(candidate)

    def is_session_open(self, *, now: datetime | None = None) -> bool:
        now = (now or self.now_ist()).astimezone(IST)
        if now.weekday() >= 5:
            return False
        if now.date().isoformat() != self.replay_date:
            return False
        t = now.time()
        return _SESSION_OPEN <= t <= _SESSION_CLOSE

    def advance_wall(self) -> None:
        """Advance sim time from wall clock (continuous replay mode)."""
        if self.stepped or self.speed == 0.0 or self._paused or self._completed:
            return
        self._sim_now = self.now_ist()
        if self._sim_now >= self._session_close:
            if self.loop:
                self._on_session_end()
            else:
                self._paused = True
                self._completed = True
                self._sim_now = self._session_close

    def step(self, *, minutes: int = 5) -> datetime:
        """Advance sim clock by fixed minutes (stepped eval mode)."""
        self._sim_now = min(self._sim_now + timedelta(minutes=minutes), self._session_close)
        if self._sim_now >= self._session_close:
            if self.loop:
                self._on_session_end()
            else:
                self._completed = True
        self._persist_if_stepped()
        return self._sim_now

    def _on_session_end(self) -> None:
        if self.week_mode:
            self._advance_week_day()
            return
        self.reset()

    def _advance_week_day(self) -> None:
        if not self.week_dates:
            self.reset()
            return
        old_date = self.replay_date
        self._week_index = (self._week_index + 1) % len(self.week_dates)
        new_date = self.week_dates[self._week_index]
        self._apply_replay_date(new_date)
        if old_date != new_date and self.on_replay_date_change:
            self.on_replay_date_change(old_date, new_date)

    def _apply_replay_date(self, replay_date: str) -> None:
        self.replay_date = replay_date[:10]
        hour, minute = (int(x) for x in self.replay_time.split(":", 1))
        self._session_open = datetime.strptime(self.replay_date, "%Y-%m-%d").replace(
            hour=hour, minute=minute, second=0, microsecond=0, tzinfo=IST
        )
        self._session_close = self._session_open.replace(
            hour=_SESSION_CLOSE.hour, minute=_SESSION_CLOSE.minute, second=0, microsecond=0
        )
        self._sim_now = self._session_open
        self._wall_anchor = None
        self._paused = False
        self._completed = False

    def _maybe_load_persisted(self) -> None:
        if not self.stepped:
            return
        from trade_integrations.stock_simulator.state_store import load_sim_now

        loaded = load_sim_now(replay_date=self.replay_date)
        if loaded is not None:
            self._sim_now = min(max(loaded, self._session_open), self._session_close)

    def _persist_if_stepped(self) -> None:
        if not self.stepped:
            return
        from trade_integrations.stock_simulator.state_store import persist_sim_now

        persist_sim_now(replay_date=self.replay_date, sim_now=self._sim_now)

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.0, float(speed))
        self._wall_anchor = datetime.now(tz=IST)
        self._sim_now = self.now_ist()

    def status(self) -> dict[str, str | float | bool | list[str]]:
        now = self.now_ist()
        return {
            "replay_date": self.replay_date,
            "sim_now": now.isoformat(),
            "speed": self.speed,
            "loop": self.loop,
            "stepped": self.stepped,
            "session_open": self.is_session_open(now=now),
            "completed": self._completed,
            "week_mode": self.week_mode,
            "week_dates": list(self.week_dates),
            "week_index": self._week_index,
        }

    def _clamp_to_session(self, candidate: datetime) -> datetime:
        if candidate < self._session_open:
            return self._session_open
        if candidate > self._session_close:
            if self.loop:
                if self.week_mode:
                    return self._session_close
                return self._session_open
            return self._session_close
        return candidate
