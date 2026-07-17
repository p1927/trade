"""Environment configuration for the Nautilus ↔ OpenAlgo bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass

_TRUE = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in _TRUE


def _parse_symbols(raw: str) -> tuple[str, ...]:
    items = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    return items or ("NIFTY", "BANKNIFTY", "INDIAVIX")


@dataclass(frozen=True)
class BridgeConfig:
    watch_enabled: bool = False
    openalgo_host: str = "http://127.0.0.1:5001"
    openalgo_api_key: str = ""
    vibe_backend_url: str = "http://127.0.0.1:8899"
    vibe_api_key: str = ""
    quote_poll_ms: int = 2_000
    watch_symbols: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "INDIAVIX")
    alert_cooldown_sec: int = 300
    redis_url: str | None = None
    instance_id: str = "trade-watch-1"
    paper_only: bool = True
    market_open: str = "09:20"
    market_close: str = "15:10"
    handoff_dir_name: str = "nautilus_handoffs"
    intent_queue_dir_name: str = "nautilus_intents"


def get_bridge_config() -> BridgeConfig:
    try:
        from trade_integrations.env import ensure_openalgo_env, load_trade_env
        from trade_integrations.stack_ports import nautilus_redis_url, vibe_backend_url

        load_trade_env()
        oa = ensure_openalgo_env()
        host = oa["host"]
        api_key = oa["api_key"]
        vibe_url = os.getenv("VIBE_BACKEND_URL", vibe_backend_url()).rstrip("/")
        redis_default = nautilus_redis_url()
    except ImportError:
        host = os.getenv("OPENALGO_HOST", "").rstrip("/")
        api_key = os.getenv("OPENALGO_API_KEY", "").strip()
        vibe_url = os.getenv("VIBE_BACKEND_URL", "").rstrip("/")
        redis_default = os.getenv("NAUTILUS_REDIS_URL", "").strip()
    redis_raw = os.getenv("NAUTILUS_REDIS_URL", redis_default).strip()
    return BridgeConfig(
        watch_enabled=_env_bool("NAUTILUS_WATCH_ENABLE", "true"),
        openalgo_host=host,
        openalgo_api_key=api_key,
        vibe_backend_url=vibe_url,
        vibe_api_key=os.getenv("VIBE_API_AUTH_KEY", os.getenv("API_AUTH_KEY", "")).strip(),
        quote_poll_ms=max(500, int(os.getenv("NAUTILUS_QUOTE_POLL_MS", "2000"))),
        watch_symbols=_parse_symbols(os.getenv("NAUTILUS_WATCH_SYMBOLS", "NIFTY,BANKNIFTY,INDIAVIX")),
        alert_cooldown_sec=max(30, int(os.getenv("NAUTILUS_ALERT_COOLDOWN_SEC", "300"))),
        redis_url=redis_raw or None,
        instance_id=os.getenv("NAUTILUS_INSTANCE_ID", "trade-watch-1").strip() or "trade-watch-1",
        paper_only=_env_bool("OPENALGO_PAPER_MODE", "true"),
        market_open=os.getenv("AUTO_PAPER_MARKET_OPEN", "09:20"),
        market_close=os.getenv("AUTO_PAPER_MARKET_CLOSE", "15:10"),
    )


def is_watch_enabled() -> bool:
    return get_bridge_config().watch_enabled


def allow_vibe_alert_outside_market_hours(config: BridgeConfig | None = None) -> bool:
    """Opt-in only — paper mode does not bypass session gates by default."""
    explicit = os.getenv("NAUTILUS_BRIDGE_ALERT_OUTSIDE_HOURS")
    if explicit is not None:
        return explicit.strip().lower() in _TRUE
    return False


def _parse_hhmm(raw: str) -> tuple[int, int]:
    parts = raw.strip().split(":")
    if len(parts) != 2:
        return 9, 20
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 9, 20


def is_bridge_market_open(config: BridgeConfig | None = None, *, now=None) -> bool:
    """True during configured NSE intraday window (weekdays)."""
    from datetime import datetime

    from zoneinfo import ZoneInfo

    cfg = config or get_bridge_config()
    ist = ZoneInfo("Asia/Kolkata")
    if now is None:
        now = datetime.now(ist)
    else:
        now = now.astimezone(ist)
    if now.weekday() >= 5:
        return False
    open_h, open_m = _parse_hhmm(cfg.market_open)
    close_h, close_m = _parse_hhmm(cfg.market_close)
    current = now.time()
    start = datetime.min.time().replace(hour=open_h, minute=open_m)
    end = datetime.min.time().replace(hour=close_h, minute=close_m)
    return start <= current <= end


def is_bridge_exit_window_open(config: BridgeConfig | None = None, *, now=None) -> bool:
    """Extended window for EXIT intents (market close + 20 min grace)."""
    from datetime import datetime, timedelta

    from zoneinfo import ZoneInfo

    cfg = config or get_bridge_config()
    ist = ZoneInfo("Asia/Kolkata")
    if now is None:
        now = datetime.now(ist)
    else:
        now = now.astimezone(ist)
    if now.weekday() >= 5:
        return False
    if is_bridge_market_open(cfg, now=now):
        return True
    close_h, close_m = _parse_hhmm(cfg.market_close)
    close_dt = datetime.combine(
        now.date(),
        datetime.min.time().replace(hour=close_h, minute=close_m),
        tzinfo=ist,
    )
    grace_end = close_dt + timedelta(minutes=20)
    return close_dt.time() < now.time() <= grace_end.time()
