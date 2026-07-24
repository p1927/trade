"""Per-owner symbol baselines — same semantics as Nautilus poll_loop / WatchActor.

Within one Nautilus owner (``aa_*`` or ``ws_{session}``), spot/OI/volume baselines are keyed
by **symbol only** in process-local dicts. Cross-request telemetry caches use
``{nautilus_owner}:{SYMBOL}`` so multiple owners can coexist in the API worker.
"""

from __future__ import annotations


def owner_baseline_key(nautilus_owner: str, symbol: str) -> str:
    owner = str(nautilus_owner or "").strip()
    sym = str(symbol or "").strip().upper()
    if not owner or not sym:
        raise ValueError("nautilus_owner and symbol required")
    return f"{owner}:{sym}"


def seed_symbol_baseline(
    store: dict[str, float],
    symbol: str,
    value: float | None,
) -> float | None:
    """Seed baseline in an owner-scoped dict (poll_loop / WatchActor)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    if value is None:
        return store.get(sym)
    if sym not in store:
        store[sym] = float(value)
    return store[sym]


def seed_quote_symbol_baselines(
    *,
    ltp_baselines: dict[str, float],
    symbol: str,
    ltp: float | None,
    oi: float | None = None,
    volume: float | None = None,
    oi_baselines: dict[str, float] | None = None,
    volume_baselines: dict[str, float] | None = None,
) -> None:
    """Seed LTP/OI/volume baselines from a quote snapshot (Nautilus poll paths)."""
    seed_symbol_baseline(ltp_baselines, symbol, ltp)
    if oi_baselines is not None and oi is not None:
        seed_symbol_baseline(oi_baselines, symbol, oi)
    if volume_baselines is not None and volume is not None:
        seed_symbol_baseline(volume_baselines, symbol, volume)


def seed_owner_baseline(
    store: dict[str, float],
    *,
    nautilus_owner: str,
    symbol: str,
    value: float | None,
) -> float | None:
    """Seed baseline in a multi-owner telemetry cache."""
    key = owner_baseline_key(nautilus_owner, symbol)
    if value is None:
        return store.get(key)
    if key not in store:
        store[key] = float(value)
    return store[key]


def prune_owner_baselines(
    stores: tuple[dict[str, float], ...],
    *,
    nautilus_owner: str,
    active_symbols: set[str] | frozenset[str],
) -> None:
    """Drop cached baselines for symbols no longer watched by this owner."""
    owner = str(nautilus_owner or "").strip()
    if not owner:
        return
    active = {str(s).strip().upper() for s in active_symbols if str(s).strip()}
    prefix = f"{owner}:"
    for store in stores:
        for key in list(store.keys()):
            if not key.startswith(prefix):
                continue
            sym = key[len(prefix) :]
            if sym not in active:
                store.pop(key, None)
