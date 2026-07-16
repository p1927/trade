"""Append-only paper trade outcomes for calibration and reflection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir

_LEDGER_REL = Path("_data") / "auto_paper" / "outcomes.parquet"
_MIN_SAMPLES = 3
_MAX_ADJ = 0.05


def ledger_path() -> Path:
    path = get_hub_dir() / _LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_ledger() -> pd.DataFrame:
    path = ledger_path()
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def save_ledger(df: pd.DataFrame) -> None:
    path = ledger_path()
    df.to_parquet(path, index=False)


def append_outcome(
    *,
    symbol: str,
    strategy: str | None,
    action: str,
    intent_source: str,
    gross_pnl_inr: float | None = None,
    net_pnl_inr: float | None = None,
    widget_id: str | None = None,
    agent_id: str | None = None,
    mandate_snapshot: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol.upper(),
        "strategy": strategy,
        "action": action.upper(),
        "intent_source": intent_source,
        "gross_pnl_inr": gross_pnl_inr,
        "net_pnl_inr": net_pnl_inr,
        "widget_id": widget_id,
        "agent_id": agent_id,
        "mandate_holding_period": (mandate_snapshot or {}).get("holding_period"),
    }
    if extra:
        row.update(extra)
    ledger = load_ledger()
    ledger = pd.concat([ledger, pd.DataFrame([row])], ignore_index=True)
    save_ledger(ledger)
    return row


def strategy_hit_rates(*, min_samples: int = _MIN_SAMPLES) -> dict[str, float]:
    ledger = load_ledger()
    if ledger.empty or "strategy" not in ledger.columns:
        return {}
    if "net_pnl_inr" not in ledger.columns:
        return {}
    closed = ledger[ledger["net_pnl_inr"].notna()].copy()
    if closed.empty:
        return {}
    rates: dict[str, float] = {}
    for strategy, group in closed.groupby("strategy"):
        if len(group) < min_samples:
            continue
        wins = (group["net_pnl_inr"].astype(float) > 0).sum()
        rates[str(strategy).strip().lower()] = float(wins / len(group))
    return rates


def compute_paper_calibration_metrics(*, min_samples: int = _MIN_SAMPLES) -> dict[str, Any]:
    """Rolling calibration from reconciled paper outcomes."""
    ledger = load_ledger()
    rates = strategy_hit_rates(min_samples=min_samples)
    closed_count = 0
    if not ledger.empty and "net_pnl_inr" in ledger.columns:
        closed_count = int(ledger["net_pnl_inr"].notna().sum())

    avg_pnl: float | None = None
    if not ledger.empty and "net_pnl_inr" in ledger.columns:
        reconciled = ledger[ledger["net_pnl_inr"].notna()]
        if not reconciled.empty:
            avg_pnl = float(reconciled["net_pnl_inr"].astype(float).mean())

    return {
        "closed_trades": closed_count,
        "strategy_hit_rates": rates,
        "strategies_calibrated": len(rates),
        "avg_net_pnl_inr": avg_pnl,
        "min_samples": min_samples,
    }


def paper_strategy_calibration_adjustment(strategy_name: str | None) -> float:
    """Per-strategy score nudge from paper outcome ledger (±0.05 max)."""
    if not strategy_name:
        return 0.0
    rates = strategy_hit_rates()
    key = strategy_name.strip().lower().replace(" ", "_").replace("-", "_")
    hit = rates.get(key)
    if hit is None:
        return 0.0
    if hit >= 0.6:
        return _MAX_ADJ
    if hit <= 0.4:
        return -_MAX_ADJ
    return 0.0


def reconcile_exit_outcome(
    *,
    symbol: str,
    strategy: str | None,
    agent_id: str | None = None,
    net_pnl_inr: float | None = None,
    intent_source: str = "reconcile",
) -> dict[str, Any] | None:
    """Attach P&L to the latest open EXIT row or append reconciled close."""
    ledger = load_ledger()
    if ledger.empty:
        if net_pnl_inr is None:
            return None
        return append_outcome(
            symbol=symbol,
            strategy=strategy,
            action="EXIT",
            intent_source=intent_source,
            net_pnl_inr=net_pnl_inr,
            agent_id=agent_id,
        )

    mask = ledger["action"].astype(str).str.upper() == "EXIT"
    if agent_id and "agent_id" in ledger.columns:
        mask &= ledger["agent_id"].astype(str) == agent_id
    if strategy and "strategy" in ledger.columns:
        mask &= ledger["strategy"].astype(str) == strategy
    if "net_pnl_inr" in ledger.columns:
        mask &= ledger["net_pnl_inr"].isna()

    idx_list = ledger.index[mask].tolist()
    if idx_list and net_pnl_inr is not None:
        idx = idx_list[-1]
        ledger.at[idx, "net_pnl_inr"] = net_pnl_inr
        ledger.at[idx, "reconciled_at"] = datetime.now(timezone.utc).isoformat()
        save_ledger(ledger)
        return ledger.iloc[idx].to_dict()

    if net_pnl_inr is None:
        return None
    return append_outcome(
        symbol=symbol,
        strategy=strategy,
        action="EXIT",
        intent_source=intent_source,
        net_pnl_inr=net_pnl_inr,
        agent_id=agent_id,
    )
