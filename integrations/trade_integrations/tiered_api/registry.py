"""Registry of tiered external API sources — limits, env keys, spacing."""

from __future__ import annotations

import os
from dataclasses import dataclass

from trade_integrations.tiered_api.errors import TieredApiNotConfiguredError, TieredApiSourceUnknownError


@dataclass(frozen=True)
class SourceSpec:
    key: str
    env_keys: tuple[str, ...]
    default_daily_limit: int
    default_min_interval: float
    default_ttl_hours: float = 168.0
    vibe_min_interval_env: str | None = None


SOURCES: dict[str, SourceSpec] = {
    "tapetide": SourceSpec(
        key="tapetide",
        env_keys=("TAPETIDE_TOKEN", "TAPETIDE_API_TOKEN"),
        default_daily_limit=100,
        default_min_interval=1.0,
        default_ttl_hours=24.0,
    ),
    "alpha_vantage": SourceSpec(
        key="alpha_vantage",
        env_keys=("ALPHA_VANTAGE_API_KEY", "ALPHAVANTAGE_API_KEY"),
        default_daily_limit=25,
        default_min_interval=12.0,
        vibe_min_interval_env="VIBE_TRADING_ALPHAVANTAGE_MIN_INTERVAL",
    ),
    "eod_historical": SourceSpec(
        key="eod_historical",
        env_keys=("EOD_HISTORICAL_API_KEY", "EODHD_API_KEY"),
        default_daily_limit=20,
        default_min_interval=1.0,
    ),
    "finnhub": SourceSpec(
        key="finnhub",
        env_keys=("FINNHUB_API_KEY",),
        default_daily_limit=60,
        default_min_interval=1.0,
        vibe_min_interval_env="VIBE_TRADING_FINNHUB_MIN_INTERVAL",
    ),
    "tiingo": SourceSpec(
        key="tiingo",
        env_keys=("TIINGO_API_KEY",),
        default_daily_limit=50,
        default_min_interval=1.0,
        vibe_min_interval_env="VIBE_TRADING_TIINGO_MIN_INTERVAL",
    ),
    "fmp": SourceSpec(
        key="fmp",
        env_keys=("FMP_API_KEY",),
        default_daily_limit=250,
        default_min_interval=0.25,
        vibe_min_interval_env="VIBE_TRADING_FMP_MIN_INTERVAL",
    ),
    "alpaca": SourceSpec(
        key="alpaca",
        env_keys=("ALPACA_API_KEY", "APCA_API_KEY_ID"),
        default_daily_limit=200,
        default_min_interval=0.2,
    ),
}

TIERED_SOURCE_KEYS: frozenset[str] = frozenset(SOURCES.keys())


def get_spec(source: str) -> SourceSpec:
    key = (source or "").strip().lower()
    spec = SOURCES.get(key)
    if spec is None:
        raise TieredApiSourceUnknownError(f"Unknown tiered API source: {source!r}")
    return spec


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def tiered_api_enabled() -> bool:
    raw = os.getenv("TIERED_API_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def daily_limit(source: str) -> int:
    spec = get_spec(source)
    env_name = f"TIERED_API_{spec.key.upper()}_DAILY_LIMIT"
    return _env_int(env_name, spec.default_daily_limit)


def min_interval(source: str) -> float:
    spec = get_spec(source)
    if spec.vibe_min_interval_env:
        vibe_val = os.getenv(spec.vibe_min_interval_env, "").strip()
        if vibe_val:
            try:
                return max(0.0, float(vibe_val))
            except ValueError:
                pass
    env_name = f"TIERED_API_{spec.key.upper()}_MIN_INTERVAL"
    return _env_float(env_name, spec.default_min_interval)


def hub_ttl_hours(source: str) -> float:
    spec = get_spec(source)
    per_source = f"TIERED_API_{spec.key.upper()}_HUB_TTL_HOURS"
    if os.getenv(per_source, "").strip():
        return _env_float(per_source, spec.default_ttl_hours)
    return _env_float("TIERED_API_HUB_TTL_HOURS", spec.default_ttl_hours)


def resolve_credential(source: str) -> str:
    """Return first non-empty env value for the source."""
    spec = get_spec(source)
    for key in spec.env_keys:
        val = os.getenv(key, "").strip()
        if val:
            return val
    raise TieredApiNotConfiguredError(
        f"{spec.key}: set one of {', '.join(spec.env_keys)}"
    )


def is_configured(source: str) -> bool:
    try:
        resolve_credential(source)
        return True
    except TieredApiNotConfiguredError:
        return False


def list_sources() -> list[str]:
    return sorted(SOURCES.keys())
