"""Persist and load structured research for TradingAgents and downstream chat UIs."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from trade_integrations.dataflows.company_research.market import _IN_INDEX_TICKERS
from trade_integrations.dataflows.company_research.models import CompanyResearchDoc
from trade_integrations.dataflows.company_research.format import format_research_report

_HUB_ENV = "TRADE_STACK_HUB_DIR"
_CACHE_MINUTES_ENV = "TRADINGAGENTS_RESEARCH_CACHE_MINUTES"
_PREFETCH_ENV = "TRADINGAGENTS_RESEARCH_PREFETCH"


def get_hub_dir() -> Path:
    """Return the shared context hub root directory."""
    if custom := os.getenv(_HUB_ENV, "").strip():
        return Path(custom).expanduser().resolve()
    # trade repo root: integrations/trade_integrations/context/hub.py -> parents[3]
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "reports" / "hub"


def is_prefetch_enabled() -> bool:
    raw = os.getenv(_PREFETCH_ENV, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_company_research_eligible(ticker: str, *, asset_type: str = "stock") -> bool:
    """Return True when the company research pipeline applies to this instrument."""
    if asset_type != "stock":
        return False
    raw = ticker.strip().upper()
    if not raw:
        return False
    if raw in _IN_INDEX_TICKERS or raw.startswith("^"):
        return False
    return True


def _ticker_key(ticker: str) -> str:
    return ticker.strip().upper().replace("/", "_")


def _company_research_dir(ticker: str) -> Path:
    return get_hub_dir() / _ticker_key(ticker) / "company_research"


def _cache_max_age_minutes() -> int:
    try:
        return max(0, int(os.getenv(_CACHE_MINUTES_ENV, "60")))
    except ValueError:
        return 60


def _doc_from_json(payload: dict) -> CompanyResearchDoc:
    from trade_integrations.dataflows.company_research.models import StageResult

    stages = [
        StageResult(
            stage=s["stage"],
            status=s["status"],
            vendor=s["vendor"],
            fetched_at=datetime.fromisoformat(s["fetched_at"]),
            data=s.get("data") or {},
            errors=list(s.get("errors") or []),
        )
        for s in payload.get("stages") or []
    ]
    as_of = datetime.fromisoformat(payload["as_of"])
    return CompanyResearchDoc(
        ticker=payload["ticker"],
        as_of=as_of,
        lookahead_days=int(payload["lookahead_days"]),
        market=payload.get("market", "IN"),
        identity=dict(payload.get("identity") or {}),
        peers=list(payload.get("peers") or []),
        calendar_events=list(payload.get("calendar_events") or []),
        fundamentals=dict(payload.get("fundamentals") or {}),
        filings=dict(payload.get("filings") or {}),
        news=dict(payload.get("news") or {}),
        sentiment=dict(payload.get("sentiment") or {}),
        corp_events=dict(payload.get("corp_events") or {}),
        earnings_signal=dict(payload.get("earnings_signal") or {}),
        macro=dict(payload.get("macro") or {}),
        stages=stages,
    )


def save_company_research(doc: CompanyResearchDoc) -> Path:
    """Write latest company research markdown + JSON under the shared hub."""
    out_dir = _company_research_dir(doc.ticker)
    out_dir.mkdir(parents=True, exist_ok=True)

    markdown = format_research_report(doc)
    (out_dir / "latest.md").write_text(markdown, encoding="utf-8")

    payload = asdict(doc)
    payload["as_of"] = doc.as_of.isoformat()
    payload["stages"] = [
        {
            **asdict(stage),
            "fetched_at": stage.fetched_at.isoformat(),
        }
        for stage in doc.stages
    ]
    json_path = out_dir / "latest.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return json_path


def load_company_research_markdown(ticker: str) -> str | None:
    """Load cached company research markdown when present."""
    path = _company_research_dir(ticker) / "latest.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def load_company_research_json(ticker: str) -> CompanyResearchDoc | None:
    """Load cached company research JSON when present."""
    path = _company_research_dir(ticker) / "latest.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _doc_from_json(payload)


def is_cache_fresh(ticker: str) -> bool:
    """Return True when cached research is younger than the configured TTL."""
    max_age = _cache_max_age_minutes()
    if max_age == 0:
        return False
    path = _company_research_dir(ticker) / "latest.json"
    if not path.is_file():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - mtime).total_seconds() / 60.0
    return age_minutes <= max_age


def prefetch_company_research(ticker: str, *, asset_type: str = "stock") -> bool:
    """Warm the hub cache before a TradingAgents run when enabled."""
    if not is_prefetch_enabled():
        return False
    if not is_company_research_eligible(ticker, asset_type=asset_type):
        return False
    from trade_integrations.tools.company_research_tools import fetch_company_research_report

    fetch_company_research_report(ticker)
    return True
