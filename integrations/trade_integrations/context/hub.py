"""Persist and load structured research for TradingAgents and downstream chat UIs."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.company_research.market import _IN_INDEX_TICKERS
from trade_integrations.dataflows.company_research.models import CompanyResearchDoc
from trade_integrations.dataflows.company_research.format import format_research_report
from trade_integrations.dataflows.options_research.models import OptionsResearchDoc
from trade_integrations.dataflows.options_research.format import format_options_report

_HUB_ENV = "TRADE_STACK_HUB_DIR"
_ROOT_ENV = "TRADE_STACK_ROOT"
_CACHE_MINUTES_ENV = "TRADINGAGENTS_RESEARCH_CACHE_MINUTES"
_PREFETCH_ENV = "TRADINGAGENTS_RESEARCH_PREFETCH"
_OPTIONS_CACHE_MINUTES_ENV = "TRADINGAGENTS_OPTIONS_CACHE_MINUTES"
_OPTIONS_PREFETCH_ENV = "TRADINGAGENTS_OPTIONS_PREFETCH"
_STOCK_PREFETCH_ENV = "TRADINGAGENTS_STOCK_PREFETCH"
_INDEX_PREFETCH_ENV = "TRADINGAGENTS_INDEX_PREFETCH"


def _trade_stack_root() -> Path:
    """Repo root for resolving relative hub paths (cwd-independent)."""
    if custom := os.getenv(_ROOT_ENV, "").strip():
        return Path(custom).expanduser().resolve()
    # integrations/trade_integrations/context/hub.py -> parents[3]
    return Path(__file__).resolve().parents[3]


def get_hub_dir() -> Path:
    """Return the shared context hub root directory."""
    if custom := os.getenv(_HUB_ENV, "").strip():
        path = Path(custom).expanduser()
        if not path.is_absolute():
            path = _trade_stack_root() / path
        return path.resolve()
    return _trade_stack_root() / "reports" / "hub"


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


def _company_history_retention() -> int:
    try:
        return max(7, int(os.getenv("COMPANY_RESEARCH_HISTORY_RETENTION", "365")))
    except ValueError:
        return 90


def _options_stock_history_retention() -> int:
    try:
        return max(7, int(os.getenv("OPTIONS_STOCK_RESEARCH_HISTORY_RETENTION", "30")))
    except ValueError:
        return 30


def _append_json_history(out_dir: Path, payload_text: str, *, retention: int) -> None:
    """Write a timestamped snapshot under ``history/`` and prune to retention count."""
    history_dir = out_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    (history_dir / f"{stamp}.json").write_text(payload_text, encoding="utf-8")
    snapshots = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in snapshots[retention:]:
        old.unlink(missing_ok=True)


def archive_research_snapshots(
    kind: str,
    *,
    as_of_date: str | None = None,
) -> dict[str, int]:
    """Copy each symbol's latest.json to history/YYYY-MM-DD.json for options or stock research."""
    hub = get_hub_dir()
    if not hub.is_dir():
        return {"archived": 0, "skipped": 0, "kind": kind}

    subdir = f"{kind}_research"
    day = as_of_date or datetime.now(timezone.utc).date().isoformat()
    archived = 0
    skipped = 0
    for symbol_dir in hub.iterdir():
        if not symbol_dir.is_dir() or symbol_dir.name.startswith("_"):
            continue
        latest = symbol_dir / subdir / "latest.json"
        if not latest.is_file():
            continue
        history_dir = symbol_dir / subdir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        daily_path = history_dir / f"{day}.json"
        if daily_path.is_file():
            skipped += 1
            continue
        daily_path.write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
        archived += 1

        snapshots = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        retention = _options_stock_history_retention()
        for old in snapshots[retention:]:
            old.unlink(missing_ok=True)

    return {"archived": archived, "skipped": skipped, "date": day, "kind": kind}


def archive_options_stock_snapshots(*, as_of_date: str | None = None) -> dict[str, Any]:
    """Archive latest options and stock research for all hub symbols."""
    options = archive_research_snapshots("options", as_of_date=as_of_date)
    stock = archive_research_snapshots("stock", as_of_date=as_of_date)
    return {
        "options": options,
        "stock": stock,
        "archived": int(options.get("archived", 0)) + int(stock.get("archived", 0)),
        "skipped": int(options.get("skipped", 0)) + int(stock.get("skipped", 0)),
    }


def archive_company_research_snapshots(*, as_of_date: str | None = None) -> dict[str, int]:
    """Copy each symbol's latest.json to history/YYYY-MM-DD.json (one per calendar day)."""
    hub = get_hub_dir()
    if not hub.is_dir():
        return {"archived": 0, "skipped": 0}

    day = as_of_date or datetime.now(timezone.utc).date().isoformat()
    archived = 0
    skipped = 0
    for symbol_dir in hub.iterdir():
        if not symbol_dir.is_dir():
            continue
        latest = symbol_dir / "company_research" / "latest.json"
        if not latest.is_file():
            continue
        history_dir = symbol_dir / "company_research" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        daily_path = history_dir / f"{day}.json"
        if daily_path.is_file():
            skipped += 1
            continue
        daily_path.write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
        archived += 1

        snapshots = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        retention = _company_history_retention()
        for old in snapshots[retention:]:
            old.unlink(missing_ok=True)

    return {"archived": archived, "skipped": skipped, "date": day}


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

    history_dir = out_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = doc.as_of.strftime("%Y-%m-%dT%H%M%S") if hasattr(doc.as_of, "strftime") else "snapshot"
    snap_path = history_dir / f"{stamp}.json"
    snap_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    snapshots = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    retention = _company_history_retention()
    for old in snapshots[retention:]:
        old.unlink(missing_ok=True)

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


def list_company_research_history(
    ticker: str,
    *,
    days: int = 90,
) -> list[dict[str, Any]]:
    """Load archived company research snapshots for trend charts."""
    from datetime import date, timedelta

    history_dir = _company_research_dir(ticker) / "history"
    if not history_dir.is_dir():
        return []

    cutoff = date.today() - timedelta(days=max(7, days))
    rows: list[dict[str, Any]] = []
    for path in sorted(history_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        as_of_raw = str(payload.get("as_of") or path.stem)[:10]
        try:
            as_of_date = date.fromisoformat(as_of_raw)
        except ValueError:
            continue
        if as_of_date < cutoff:
            continue
        rows.append(
            {
                "date": as_of_date.isoformat(),
                "as_of": payload.get("as_of"),
                "sentiment": payload.get("sentiment") or {},
                "earnings_signal": payload.get("earnings_signal") or {},
                "news": payload.get("news") or {},
                "calendar_events": list(payload.get("calendar_events") or []),
                "source_file": path.name,
            }
        )

    rows.sort(key=lambda row: row["date"])
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduped[row["date"]] = row
    return [deduped[key] for key in sorted(deduped)]


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


def _options_research_dir(ticker: str) -> Path:
    return get_hub_dir() / _ticker_key(ticker) / "options_research"


def _options_cache_max_age_minutes() -> int:
    try:
        return max(0, int(os.getenv(_OPTIONS_CACHE_MINUTES_ENV, "30")))
    except ValueError:
        return 30


def is_options_prefetch_enabled() -> bool:
    raw = os.getenv(_OPTIONS_PREFETCH_ENV, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _options_doc_from_json(payload: dict) -> OptionsResearchDoc:
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
    return OptionsResearchDoc(
        underlying=payload["underlying"],
        as_of=as_of,
        lookahead_days=int(payload["lookahead_days"]),
        instrument_type=payload.get("instrument_type", "stock"),
        market=payload.get("market", "IN"),
        expiry=str(payload.get("expiry") or ""),
        spot=payload.get("spot"),
        meta=dict(payload.get("meta") or {}),
        prediction=dict(payload.get("prediction") or {}),
        events=list(payload.get("events") or []),
        scenarios=list(payload.get("scenarios") or []),
        chain_snapshot=dict(payload.get("chain_snapshot") or {}),
        ranked_strategies=list(payload.get("ranked_strategies") or []),
        recommended=dict(payload.get("recommended") or {}),
        payoff=dict(payload.get("payoff") or {}),
        payoff_over_time=dict(payload.get("payoff_over_time") or {}),
        browse_summary=dict(payload.get("browse_summary") or {}),
        charges=dict(payload.get("charges") or {}),
        implementation_steps=list(payload.get("implementation_steps") or []),
        stages=stages,
    )


def save_options_research(doc: OptionsResearchDoc) -> Path:
    """Write latest options research markdown + JSON under the shared hub."""
    out_dir = _options_research_dir(doc.underlying)
    out_dir.mkdir(parents=True, exist_ok=True)

    markdown = format_options_report(doc)
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
    payload_text = json.dumps(payload, indent=2, default=str)
    json_path.write_text(payload_text, encoding="utf-8")
    _append_json_history(out_dir, payload_text, retention=_options_stock_history_retention())
    return json_path


def load_options_research_markdown(ticker: str) -> str | None:
    """Load cached options research markdown when present."""
    path = _options_research_dir(ticker) / "latest.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def load_options_research_json(ticker: str) -> OptionsResearchDoc | None:
    """Load cached options research JSON when present."""
    path = _options_research_dir(ticker) / "latest.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _options_doc_from_json(payload)


def is_options_cache_fresh(ticker: str) -> bool:
    """Return True when cached options research is younger than the configured TTL."""
    max_age = _options_cache_max_age_minutes()
    if max_age == 0:
        return False
    path = _options_research_dir(ticker) / "latest.json"
    if not path.is_file():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - mtime).total_seconds() / 60.0
    return age_minutes <= max_age


def prefetch_options_research(ticker: str) -> bool:
    """Warm the hub cache for options research when enabled."""
    from trade_integrations.dataflows.options_research.market import is_options_research_eligible

    if not is_options_prefetch_enabled():
        return False
    if not is_options_research_eligible(ticker):
        return False
    from trade_integrations.tools.options_research_tools import fetch_options_research_report

    fetch_options_research_report(ticker)
    return True


def _stock_research_dir(ticker: str) -> Path:
    return get_hub_dir() / _ticker_key(ticker) / "stock_research"


def save_stock_research(doc) -> Path:
    """Write latest stock trade plan under the shared hub."""
    from dataclasses import asdict

    from trade_integrations.dataflows.stock_research.format import format_stock_report

    out_dir = _stock_research_dir(doc.ticker)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.md").write_text(format_stock_report(doc), encoding="utf-8")
    payload = asdict(doc)
    payload["as_of"] = doc.as_of.isoformat()
    payload["stages"] = [
        {**asdict(stage), "fetched_at": stage.fetched_at.isoformat()} for stage in doc.stages
    ]
    json_path = out_dir / "latest.json"
    payload_text = json.dumps(payload, indent=2, default=str)
    json_path.write_text(payload_text, encoding="utf-8")
    _append_json_history(out_dir, payload_text, retention=_options_stock_history_retention())
    return json_path


def load_stock_research_json(ticker: str):
    """Load cached stock trade plan JSON when present."""
    from trade_integrations.dataflows.stock_research.models import StockResearchDoc

    path = _stock_research_dir(ticker) / "latest.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
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
    return StockResearchDoc(
        ticker=payload["ticker"],
        as_of=datetime.fromisoformat(payload["as_of"]),
        lookahead_days=int(payload.get("lookahead_days") or 14),
        market=payload.get("market", "IN"),
        spot=payload.get("spot"),
        meta=dict(payload.get("meta") or {}),
        browse_summary=dict(payload.get("browse_summary") or {}),
        prediction=dict(payload.get("prediction") or {}),
        events=list(payload.get("events") or []),
        scenarios=list(payload.get("scenarios") or []),
        ranked_strategies=list(payload.get("ranked_strategies") or []),
        recommended=dict(payload.get("recommended") or {}),
        payoff=dict(payload.get("payoff") or {}),
        payoff_over_time=dict(payload.get("payoff_over_time") or {}),
        charges=dict(payload.get("charges") or {}),
        implementation_steps=list(payload.get("implementation_steps") or []),
        stages=stages,
    )


def load_stock_research_markdown(ticker: str) -> str | None:
    """Load cached stock trade plan markdown when present."""
    path = _stock_research_dir(ticker) / "latest.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def is_stock_prefetch_enabled() -> bool:
    raw = os.getenv(_STOCK_PREFETCH_ENV, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_stock_cache_fresh(ticker: str) -> bool:
    """Return True when cached stock plan is younger than company research TTL."""
    max_age = _cache_max_age_minutes()
    if max_age == 0:
        return False
    path = _stock_research_dir(ticker) / "latest.json"
    if not path.is_file():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - mtime).total_seconds() / 60.0
    return age_minutes <= max_age


def is_stock_research_eligible(ticker: str) -> bool:
    """Stock trade plans apply to the same equity tickers as company research."""
    return is_company_research_eligible(ticker, asset_type="stock")


def prefetch_stock_research(ticker: str) -> bool:
    """Warm the hub cache for stock trade plans when enabled."""
    if not is_stock_prefetch_enabled():
        return False
    if not is_stock_research_eligible(ticker):
        return False
    from trade_integrations.tools.stock_research_tools import fetch_stock_research_report

    fetch_stock_research_report(ticker)
    return True


_DEBATE_CACHE_MINUTES_ENV = "TRADINGAGENTS_DEBATE_CACHE_MINUTES"


def _agent_debate_dir(ticker: str) -> Path:
    return get_hub_dir() / _ticker_key(ticker) / "agent_debate"


def _debate_cache_max_age_minutes() -> int:
    try:
        return max(0, int(os.getenv(_DEBATE_CACHE_MINUTES_ENV, "720")))
    except ValueError:
        return 720


def save_agent_debate(ticker: str, payload: dict) -> Path:
    """Write latest TradingAgents debate summary under the shared hub."""
    from trade_integrations.dataflows.agent_debate.format import format_agent_debate_report

    out_dir = _agent_debate_dir(ticker)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.md").write_text(format_agent_debate_report(payload), encoding="utf-8")
    json_path = out_dir / "latest.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return json_path


def load_agent_debate_json(ticker: str) -> dict | None:
    """Load cached agent debate JSON when present."""
    path = _agent_debate_dir(ticker) / "latest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_agent_debate_markdown(ticker: str) -> str | None:
    """Load cached agent debate markdown when present."""
    path = _agent_debate_dir(ticker) / "latest.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def is_agent_debate_cache_fresh(ticker: str) -> bool:
    """Return True when cached debate is younger than the configured TTL."""
    max_age = _debate_cache_max_age_minutes()
    if max_age == 0:
        return False
    path = _agent_debate_dir(ticker) / "latest.json"
    if not path.is_file():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - mtime).total_seconds() / 60.0
    return age_minutes <= max_age


def _quant_review_dir(ticker: str) -> Path:
    return get_hub_dir() / _ticker_key(ticker) / "quant_review"


_QUANT_REVIEW_CACHE_MINUTES_ENV = "QUANT_REVIEW_CACHE_MINUTES"


def _quant_review_cache_max_age_minutes() -> int:
    try:
        return max(0, int(os.getenv(_QUANT_REVIEW_CACHE_MINUTES_ENV, "360")))
    except ValueError:
        return 360


def save_quant_review(ticker: str, payload: dict) -> Path:
    """Write latest quant review under reports/hub/{TICKER}/quant_review/."""
    out_dir = _quant_review_dir(ticker)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "latest.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return json_path


def save_quant_review_history(ticker: str, payload: dict, *, keep: int = 48) -> Path | None:
    """Append rolling snapshot under quant_review/history/ (for quant monitor diffs)."""
    out_dir = _quant_review_dir(ticker) / "history"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{ts}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    files = sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass
    return path


def load_quant_review_json(ticker: str) -> dict | None:
    path = _quant_review_dir(ticker) / "latest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_quant_review_cache_fresh(ticker: str) -> bool:
    max_age = _quant_review_cache_max_age_minutes()
    if max_age == 0:
        return False
    path = _quant_review_dir(ticker) / "latest.json"
    if not path.is_file():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - mtime).total_seconds() / 60.0
    return age_minutes <= max_age


def _index_research_dir(ticker: str) -> Path:
    return get_hub_dir() / _ticker_key(ticker) / "index_research"


def _index_doc_from_json(payload: dict):
    from trade_integrations.dataflows.company_research.models import StageResult
    from trade_integrations.dataflows.index_research.models import IndexResearchDoc

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
    return IndexResearchDoc(
        ticker=payload["ticker"],
        as_of=as_of,
        horizon=dict(payload.get("horizon") or {}),
        spot=payload.get("spot"),
        prediction=dict(payload.get("prediction") or {}),
        regime=dict(payload.get("regime") or {}),
        global_factors=list(payload.get("global_factors") or []),
        constituent_signals=list(payload.get("constituent_signals") or []),
        sector_breadth=dict(payload.get("sector_breadth") or {}),
        scenarios=list(payload.get("scenarios") or []),
        accuracy=dict(payload.get("accuracy") or {}),
        factor_explanation=dict(payload.get("factor_explanation") or {}),
        factor_sensitivity=list(payload.get("factor_sensitivity") or []),
        event_impact_curves=list(payload.get("event_impact_curves") or []),
        upcoming_events=list(payload.get("upcoming_events") or []),
        cascade_calibration=dict(payload.get("cascade_calibration") or {}),
        news_impact=dict(payload.get("news_impact") or {}),
        event_overlay=dict(payload.get("event_overlay") or {}),
        news_shock_calibration=dict(payload.get("news_shock_calibration") or {}),
        pipeline_log=list(payload.get("pipeline_log") or []),
        stages=stages,
    )


def save_index_research(doc) -> Path:
    """Write latest index research markdown + JSON under the shared hub."""
    from trade_integrations.dataflows.index_research.format import format_index_report

    if not (getattr(doc, "news_impact", None) or {}).get("items"):
        try:
            from trade_integrations.dataflows.news_hub_bridge import sync_news_impact_to_index_doc

            doc.news_impact = sync_news_impact_to_index_doc(doc)
        except Exception:
            pass

    out_dir = _index_research_dir(doc.ticker)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.md").write_text(format_index_report(doc), encoding="utf-8")
    payload = asdict(doc)
    payload["as_of"] = doc.as_of.isoformat()
    payload["stages"] = [
        {**asdict(stage), "fetched_at": stage.fetched_at.isoformat()} for stage in doc.stages
    ]
    json_path = out_dir / "latest.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    history_dir = out_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = doc.as_of.strftime("%Y-%m-%dT%H%M%S") if hasattr(doc.as_of, "strftime") else "snapshot"
    snap_path = history_dir / f"{stamp}.json"
    snap_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    snapshots = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in snapshots[30:]:
        old.unlink(missing_ok=True)

    return json_path


def load_index_research_json(ticker: str):
    """Load cached index research JSON when present."""
    path = _index_research_dir(ticker) / "latest.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    doc = _index_doc_from_json(payload)
    embedded = getattr(doc, "news_impact", None) or {}
    if not (embedded.get("items") or []):
        try:
            from trade_integrations.dataflows.news_hub_bridge import resolve_news_impact

            doc.news_impact = resolve_news_impact(ticker=ticker, doc=doc)
        except Exception:
            pass
    return doc


def load_index_research_markdown(ticker: str) -> str | None:
    """Load cached index research markdown when present."""
    path = _index_research_dir(ticker) / "latest.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def is_index_research_cache_fresh(ticker: str) -> bool:
    """Return True when cached index research is younger than company research TTL."""
    max_age = _cache_max_age_minutes()
    if max_age == 0:
        return False
    path = _index_research_dir(ticker) / "latest.json"
    if not path.is_file():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - mtime).total_seconds() / 60.0
    return age_minutes <= max_age


def is_index_prefetch_enabled() -> bool:
    raw = os.getenv(_INDEX_PREFETCH_ENV, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def prefetch_index_research(ticker: str) -> bool:
    """Warm the hub cache for index research when enabled (NIFTY and other indices)."""
    from trade_integrations.dataflows.company_research.india_symbols import india_index_tickers

    if not is_index_prefetch_enabled():
        return False
    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    if sym not in india_index_tickers():
        return False
    from trade_integrations.tools.index_research_tools import fetch_index_research_report

    fetch_index_research_report(sym)
    return True
