"""Simulator activation helpers shared by Trade stack and hub gates."""

from __future__ import annotations

import os
import re

from trade_integrations.stock_simulator.config import load_sim_config


def _broker_from_redirect() -> str:
    redirect = os.getenv("REDIRECT_URL", "")
    match = re.search(r"/([^/]+)/callback$", redirect)
    return match.group(1).lower() if match else ""


def is_simulator_active() -> bool:
    cfg = load_sim_config()
    if cfg.is_replay:
        return True
    if _broker_from_redirect() == "stock_simulator":
        return True
    return os.getenv("OPENALGO_BROKER", "").strip().lower() == "stock_simulator"


def hub_no_learn() -> bool:
    cfg = load_sim_config()
    if cfg.hub_no_learn:
        return True
    return is_simulator_active()


def sim_overrides_market_hours() -> bool:
    return is_simulator_active()


def sim_market_session_open(*, market: str = "IN") -> bool:
    if not sim_overrides_market_hours():
        return False
    if str(market or "IN").strip().upper() == "US":
        return False
    from trade_integrations.stock_simulator.replay import get_replay_service

    svc = get_replay_service()
    return svc.clock.is_session_open()


def maybe_advance_sim_after_watch(*, minutes: int = 5) -> dict[str, str] | None:
    """Step sim clock after a watch tick when SIM_EVAL_MODE=stepped."""
    if not is_simulator_active():
        return None
    from trade_integrations.stock_simulator.config import load_sim_config

    cfg = load_sim_config()
    if not cfg.is_stepped:
        return None
    from trade_integrations.stock_simulator.replay import get_replay_service

    new_ts = get_replay_service().step(minutes=minutes)
    return {"sim_now": new_ts.isoformat()}
