"""Rule-first claim extraction from raw news refs (Phase 3)."""

from __future__ import annotations

import re
from typing import Any

_PERCENT = re.compile(r"\b([+-]?\d+(?:\.\d+)?)\s*(?:%|percent|pct)", re.IGNORECASE)
_LEVEL = re.compile(
    r"\b(?:nifty(?:\s*50)?|sensex|banknifty)\b[^0-9]{0,24}(\d{4,6}(?:\.\d+)?)",
    re.IGNORECASE,
)
_BPS = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:bps|basis points?)\b", re.IGNORECASE)
_DATE = re.compile(
    r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
_ACTOR = re.compile(
    r"\b(RBI|Fed|ECB|SEBI|FIIs?|DIIs?|IMF|OPEC|Crude|Brent|WTI)\b",
    re.IGNORECASE,
)


def extract_claims(title: str, summary: str = "") -> list[dict[str, Any]]:
    """Extract structured numeric/actor claims from headline + summary."""
    text = f"{title or ''} {summary or ''}".strip()
    if not text:
        return []

    claims: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(kind: str, value: Any, *, span: str = "", status: str = "claimed") -> None:
        key = f"{kind}:{value}:{span[:40]}"
        if key in seen:
            return
        seen.add(key)
        claims.append(
            {
                "kind": kind,
                "value": value,
                "text": span[:200],
                "status": status,
            }
        )

    for match in _PERCENT.finditer(text):
        add("percent_move", float(match.group(1)), span=match.group(0))

    for match in _LEVEL.finditer(text):
        add("index_level", float(match.group(1)), span=match.group(0))

    for match in _BPS.finditer(text):
        add("rate_change_bps", float(match.group(1)), span=match.group(0))

    for match in _DATE.finditer(text):
        add("date_reference", match.group(1), span=match.group(0))

    for match in _ACTOR.finditer(text):
        add("actor", match.group(1).upper(), span=match.group(0))

    return claims[:20]


def enrich_ref_with_claims(ref: dict[str, Any]) -> dict[str, Any]:
    """Attach ``extracted_claims`` on a staging ref dict (in-place friendly copy)."""
    out = dict(ref)
    claims = extract_claims(str(ref.get("title") or ""), str(ref.get("summary") or ""))
    if claims:
        out["extracted_claims"] = claims
    return out
