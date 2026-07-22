"""IST session clock accelerated by replay speed."""

from __future__ import annotations

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
    ) -> None:
        self.replay_date = replay_date[:10]
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
                self.reset()
            else:
                self._paused = True
                self._completed = True
                self._sim_now = self._session_close

    def step(self, *, minutes: int = 5) -> datetime:
        """Advance sim clock by fixed minutes (stepped eval mode)."""
        self._sim_now = min(self._sim_now + timedelta(minutes=minutes), self._session_close)
        if self._sim_now >= self._session_close:
            if self.loop:
                self.reset()
            else:
                self._completed = True
        self._persist_if_stepped()
        return self._sim_now

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

    def status(self) -> dict[str, str | float | bool]:
        now = self.now_ist()
        return {
            "replay_date": self.replay_date,
            "sim_now": now.isoformat(),
            "speed": self.speed,
            "loop": self.loop,
            "stepped": self.stepped,
            "session_open": self.is_session_open(now=now),
            "completed": self._completed,
        }

    def _clamp_to_session(self, candidate: datetime) -> datetime:
        if candidate < self._session_open:
            return self._session_open
        if candidate > self._session_close:
            if self.loop:
                return self._session_open
            return self._session_close
        return candidate
