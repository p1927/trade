"""Shared spot resolution for hub research docs (options + stock)."""

from __future__ import annotations

from typing import Any, Literal

ResearchKind = Literal["options", "stock", "auto"]


def _positive_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _get_attr(doc: Any, name: str, default: Any = None) -> Any:
    if isinstance(doc, dict):
        return doc.get(name, default)
    return getattr(doc, name, default)


def resolve_doc_spot(doc: Any, *, kind: ResearchKind = "auto") -> float | None:
    """Resolve actionable spot from hub doc using the same fallbacks as widgets."""
    if doc is None:
        return None

    resolved_kind = kind
    if resolved_kind == "auto":
        instrument = str(_get_attr(doc, "instrument_type") or "").lower()
        if instrument == "stock" or _get_attr(doc, "ticker"):
            resolved_kind = "stock"
        else:
            resolved_kind = "options"

    top = _positive_float(_get_attr(doc, "spot"))
    if top is not None:
        return top

    if resolved_kind == "options":
        chain = _get_attr(doc, "chain_snapshot") or {}
        if isinstance(chain, dict):
            from_chain = _positive_float(chain.get("underlying_ltp"))
            if from_chain is not None:
                return from_chain
        browse = _get_attr(doc, "browse_summary") or {}
        if isinstance(browse, dict):
            from_browse = _positive_float(browse.get("spot"))
            if from_browse is not None:
                return from_browse

    if resolved_kind == "stock":
        browse = _get_attr(doc, "browse_summary") or {}
        if isinstance(browse, dict):
            from_price = _positive_float(browse.get("last_price") or browse.get("spot"))
            if from_price is not None:
                return from_price

    return None
