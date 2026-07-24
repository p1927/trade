"""Environment-driven simulator configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SimConfig:
    mode: str
    replay_date: str
    replay_time: str
    speed: float
    loop: bool
    eval_mode: str
    data_root: Path
    hub_no_learn: bool
    week_mode: bool
    week_days_count: int
    week_dates: tuple[str, ...]

    @property
    def is_replay(self) -> bool:
        return self.mode.strip().lower() == "replay"

    @property
    def is_stepped(self) -> bool:
        return self.eval_mode.strip().lower() == "stepped"


def load_sim_config() -> SimConfig:
    mode = os.getenv("STOCK_SIMULATOR_MODE", "").strip().lower()
    week_mode = _truthy(os.getenv("NSE_REPLAY_WEEK_MODE", "1"))
    try:
        week_days_count = max(1, int(os.getenv("NSE_REPLAY_WEEK_COUNT", "5") or "5"))
    except ValueError:
        week_days_count = 5
    replay_time = os.getenv("NSE_REPLAY_TIME", "09:15").strip()
    try:
        speed = float(os.getenv("NSE_REPLAY_SPEED", "1") or "1")
    except ValueError:
        speed = 1.0
    speed = max(0.0, speed)
    loop = _truthy(os.getenv("NSE_REPLAY_LOOP", "1"))
    eval_mode = os.getenv("SIM_EVAL_MODE", "continuous").strip().lower()
    data_root = Path(os.getenv("NSE_REPLAY_DATA_ROOT", str(_repo_root() / "data/nse/historic_data")))
    hub_flag = _truthy(os.getenv("HUB_NO_LEARN")) or mode == "replay"
    explicit_replay = os.getenv("NSE_REPLAY_DATE", "").strip()[:10]
    week_dates: tuple[str, ...] = ()
    if week_mode:
        from trade_integrations.stock_simulator.week_rotation import resolve_week_replay_date

        replay_date, resolved_days = resolve_week_replay_date(
            data_root,
            explicit_replay or None,
            n=week_days_count,
        )
        week_dates = tuple(resolved_days)
    else:
        replay_date = explicit_replay or "2021-03-25"
    return SimConfig(
        mode=mode,
        replay_date=replay_date,
        replay_time=replay_time,
        speed=speed,
        loop=loop,
        eval_mode=eval_mode,
        data_root=data_root,
        hub_no_learn=hub_flag,
        week_mode=week_mode,
        week_days_count=week_days_count,
        week_dates=week_dates,
    )
