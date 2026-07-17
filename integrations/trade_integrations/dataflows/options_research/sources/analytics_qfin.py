"""qfinindia analytics on option chain snapshot."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _chain_to_dataframe(chain_snapshot: dict[str, Any]):
    import pandas as pd

    rows = []
    spot = chain_snapshot.get("underlying_ltp") or 0
    expiry = chain_snapshot.get("expiry_date") or ""
    for row in chain_snapshot.get("chain") or []:
        strike = row.get("strike")
        for side, key in (("CE", "ce"), ("PE", "pe")):
            leg = row.get(key) or {}
            ltp = leg.get("ltp") or 0
            if not strike or not ltp:
                continue
            rows.append(
                {
                    "type": side,
                    "strike": float(strike),
                    "expiry": expiry,
                    "price": float(ltp),
                    "iv": float(leg.get("iv") or leg.get("implied_volatility") or 0),
                    "oi": int(leg.get("oi") or 0),
                }
            )
    if not rows:
        return None
    return pd.DataFrame(rows), float(spot)


def fetch_analytics_qfin(chain_snapshot: dict[str, Any]) -> StageResult:
    """Compute expected move, skew, and tail metrics via qfinindia when available."""
    now = _stage_now()
    try:
        import numpy as np

        if not hasattr(np, "trapz") and hasattr(np, "trapezoid"):
            np.trapz = np.trapezoid  # numpy 2.x compat for qfinindia
        from qfinindia import Analytics, OptionChain
    except ImportError:
        return StageResult(
            stage="analytics",
            status="skipped",
            vendor="qfinindia",
            fetched_at=now,
            data={"reason": "pip install qfinindia"},
        )

    built = _chain_to_dataframe(chain_snapshot)
    if built is None:
        return StageResult(
            stage="analytics",
            status="skipped",
            vendor="qfinindia",
            fetched_at=now,
            data={"reason": "empty chain"},
        )
    frame, spot = built
    if spot <= 0:
        return StageResult(
            stage="analytics",
            status="skipped",
            vendor="qfinindia",
            fetched_at=now,
            data={"reason": "missing spot"},
        )

    try:
        chain = OptionChain.from_dataframe(frame, underlying=spot)
        analytics = Analytics(chain)
        payload = {
            "expected_move": getattr(analytics, "expected_move", None),
            "forward": getattr(analytics, "forward", None),
            "skew": getattr(analytics, "skew", None),
            "atm_vol": getattr(analytics, "atm_vol", None),
            "bias": getattr(analytics, "bias", None),
        }
        try:
            payload["var_5"] = analytics.var(0.05)
            payload["cvar_5"] = analytics.cvar(0.05)
        except Exception:
            pass
        return StageResult(
            stage="analytics",
            status="ok",
            vendor="qfinindia",
            fetched_at=now,
            data=payload,
        )
    except Exception as exc:
        logger.warning("qfinindia analytics failed: %s", exc)
        return StageResult(
            stage="analytics",
            status="error",
            vendor="qfinindia",
            fetched_at=now,
            errors=[str(exc)],
        )


def simple_analytics_fallback(chain_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Lightweight analytics when qfinindia is unavailable."""
    spot = float(chain_snapshot.get("underlying_ltp") or 0)
    pcr = chain_snapshot.get("pcr")
    atm = chain_snapshot.get("atm_strike")
    ivs = []
    for row in chain_snapshot.get("chain") or []:
        for key in ("ce", "pe"):
            leg = row.get(key) or {}
            iv = leg.get("iv") or leg.get("implied_volatility")
            if iv:
                ivs.append(float(iv))
    atm_iv = sum(ivs) / len(ivs) if ivs else None
    payload: dict[str, Any] = {
        "pcr": pcr,
        "atm_strike": atm,
        "atm_iv": atm_iv,
        "source": "fallback",
    }
    if atm_iv is not None:
        payload["expected_move_pct"] = round(atm_iv * 0.4, 2)
        payload["iv_regime"] = (
            "high" if atm_iv >= 20 else "moderate" if atm_iv >= 14 else "low"
        )
    else:
        payload["expected_move_pct"] = None
        payload["iv_regime"] = None
    return payload
