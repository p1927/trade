"""Research/data-feed preflight before autonomous ENTER or basket execution."""

from __future__ import annotations

from typing import Any, Literal

ResearchKind = Literal["options", "stock", "index"]


def hub_doc_spot_missing(doc: Any, *, kind: str) -> bool:
    from trade_integrations.monitor.doc_spot import resolve_doc_spot

    kind_norm = "stock" if kind == "stock" else "options"
    return resolve_doc_spot(doc, kind=kind_norm) is None  # type: ignore[arg-type]


def evaluate_research_preflight(
    ticker: str,
    *,
    kind: str,
    staleness: Any | None = None,
) -> dict[str, Any]:
    """Return structured preflight block for mandate enforcement and turn context."""
    symbol = ticker.strip().upper()
    block: dict[str, Any] = {
        "ticker": symbol,
        "kind": kind,
        "data_feed_ok": True,
        "hub_ok": True,
        "blocking_reasons": [],
    }

    if staleness is not None:
        status = getattr(staleness, "status", None) or (
            staleness.get("status") if isinstance(staleness, dict) else None
        )
        reasons = list(getattr(staleness, "reasons", None) or (
            staleness.get("reasons") if isinstance(staleness, dict) else []
        ) or [])
        block["staleness_status"] = status
        block["staleness_reasons"] = reasons
        if status == "broken":
            block["hub_ok"] = False
            block["blocking_reasons"].append(f"hub_broken:{','.join(reasons) or status}")

    try:
        from trade_integrations.monitor.live_quotes import fetch_underlying_ltp

        live = fetch_underlying_ltp(symbol)
        block["live_spot"] = live
        if live is None and kind in ("options", "stock"):
            block["data_feed_ok"] = False
            block["blocking_reasons"].append("live_spot_unavailable")
    except Exception as exc:
        block["data_feed_ok"] = False
        block["blocking_reasons"].append(f"quote_fetch_error:{exc}")

    return block


def preflight_blocks_enter(block: dict[str, Any]) -> bool:
    return bool(block.get("blocking_reasons"))
