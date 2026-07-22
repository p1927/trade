"""Automated intraday paper trading engine."""

from __future__ import annotations

import json
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trade_integrations.auto_paper.config import AutoPaperConfig, get_auto_paper_config, is_auto_paper_active
from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient
from trade_integrations.auto_paper.session_store import load_session, record_tick_result, save_session
from trade_integrations.context.hub import load_options_research_json, save_options_research
from trade_integrations.dataflows.options_research.aggregator import run_options_research
from trade_integrations.dataflows.options_research.widget_payload import build_options_trade_widget_from_doc
from trade_integrations.monitor.execution_ledger import (
    close_ledger_entry,
    list_open_entries,
    record_execution_from_widget,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def is_market_session_open(config: AutoPaperConfig, *, now: datetime | None = None) -> bool:
    """Return True during configured NSE intraday window on weekdays."""
    try:
        from trade_integrations.stock_simulator.integration import sim_market_session_open

        if sim_market_session_open(market="IN"):
            return True
    except Exception:
        pass
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return False
    start = _parse_hhmm(config.market_open)
    end = _parse_hhmm(config.market_close)
    current = now.time()
    return start <= current <= end


def _effective_watchlist(config: AutoPaperConfig) -> list[str]:
    session = load_session()
    session_list = session.get("watchlist")
    if isinstance(session_list, list) and session_list:
        return [str(item).strip().upper() for item in session_list if str(item).strip()]
    return list(config.watchlist)


def _effective_budget(config: AutoPaperConfig) -> float:
    session = load_session()
    value = session.get("budget_inr")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return config.budget_inr


def _orders_from_widget(widget: dict[str, Any], *, product: str) -> list[dict[str, Any]]:
    for step in widget.get("implementation_steps") or []:
        if step.get("action") != "execute_basket":
            continue
        payload = step.get("payload") or {}
        orders = payload.get("orders") or []
        normalized: list[dict[str, Any]] = []
        for order in orders:
            if not isinstance(order, dict) or not order.get("symbol"):
                continue
            row = dict(order)
            row["product"] = product
            normalized.append(row)
        return normalized
    return []


def _margin_positions_from_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for order in orders:
        positions.append(
            {
                "symbol": order.get("symbol"),
                "exchange": order.get("exchange"),
                "action": order.get("action"),
                "quantity": str(order.get("quantity")),
                "product": order.get("product", "NRML"),
                "pricetype": order.get("pricetype", "MARKET"),
                "price": "0",
            }
        )
    return positions


def _net_max_loss(widget: dict[str, Any]) -> float | None:
    recommended = widget.get("recommended") or {}
    for key in ("net_max_loss", "max_loss"):
        value = recommended.get(key)
        if value is None:
            value = (widget.get("payoff") or {}).get(key)
        if value is None:
            continue
        try:
            return abs(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _strategy_score(widget: dict[str, Any]) -> float:
    recommended = widget.get("recommended") or {}
    try:
        return float(recommended.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _persist_widget(widget: dict[str, Any]) -> str:
    widget_id = str(widget.get("widget_id") or "").strip()
    widget_dir = Path.home() / ".vibe-trading" / "trade_widgets"
    widget_dir.mkdir(parents=True, exist_ok=True)
    widget_path = widget_dir / f"{widget_id}.json"
    widget_path.write_text(json.dumps(widget, indent=2, default=str), encoding="utf-8")
    return widget_id


def _ensure_fresh_research(ticker: str) -> Any:
    doc = load_options_research_json(ticker)
    if doc is None:
        doc = run_options_research(ticker)
        save_options_research(doc)
        return doc

    as_of = getattr(doc, "as_of", None)
    stale = True
    if as_of is not None:
        if isinstance(as_of, str):
            try:
                as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            except ValueError:
                as_of_dt = None
        else:
            as_of_dt = as_of
        if as_of_dt is not None:
            age_minutes = (datetime.now(as_of_dt.tzinfo or IST) - as_of_dt).total_seconds() / 60.0
            stale = age_minutes > 30

    if stale:
        doc = run_options_research(ticker)
        save_options_research(doc)
    return doc


def _budget_allows(
    *,
    config: AutoPaperConfig,
    widget: dict[str, Any],
    orders: list[dict[str, Any]],
    client: OpenAlgoClient,
    open_count: int,
) -> tuple[bool, str]:
    if open_count >= config.max_open_positions:
        return False, "max_open_positions_reached"

    budget = _effective_budget(config)
    max_loss = _net_max_loss(widget)
    if max_loss is not None and max_loss > budget:
        return False, f"max_loss_{max_loss:.0f}_exceeds_budget_{budget:.0f}"

    margin = client.calculate_margin(_margin_positions_from_orders(orders))
    if margin is not None and margin > budget:
        return False, f"margin_{margin:.0f}_exceeds_budget_{budget:.0f}"

    funds = client.get_funds()
    available = funds.get("availablecash") or funds.get("available_balance")
    if available is not None:
        try:
            available_f = float(available)
            need = margin if margin is not None else (max_loss or 0.0)
            if need > available_f:
                return False, f"insufficient_funds_{available_f:.0f}"
        except (TypeError, ValueError):
            pass
    return True, "ok"


def _check_daily_loss_halt(config: AutoPaperConfig, client: OpenAlgoClient, session: dict[str, Any]) -> str | None:
    funds = client.get_funds()
    available = funds.get("availablecash") or funds.get("available_balance")
    if available is None:
        return None
    try:
        current = float(available)
    except (TypeError, ValueError):
        return None

    starting = session.get("starting_balance")
    if starting is None:
        session["starting_balance"] = current
        save_session(session)
        return None

    try:
        starting_f = float(starting)
    except (TypeError, ValueError):
        return None

    loss = starting_f - current
    if loss >= config.max_daily_loss_inr:
        return f"daily_loss_limit_{loss:.0f}_inr"
    return None


def _evaluate_exit(entry: dict[str, Any]) -> tuple[bool, str]:
    from trade_integrations.monitor.execution_ledger import fetch_position_book, match_positions_for_entry
    from trade_integrations.monitor.live_quotes import fetch_underlying_ltp
    from trade_integrations.monitor.thesis_break import evaluate_thesis_break

    underlying = str(entry.get("underlying") or "").strip().upper()
    doc = load_options_research_json(underlying)
    live_spot = fetch_underlying_ltp(underlying)
    position_book = fetch_position_book()
    _, position_pnl = match_positions_for_entry(entry, position_book)
    report = evaluate_thesis_break(
        doc,
        entry,
        live_spot=live_spot,
        position_pnl=position_pnl,
    )
    if report.broken:
        return True, "; ".join(report.reasons) or "thesis_break"
    return False, ""


def _handle_open_positions(
    *,
    client: OpenAlgoClient,
    actions: list[dict[str, Any]],
) -> None:
    for entry in list_open_entries():
        widget_id = str(entry.get("widget_id") or "")
        underlying = str(entry.get("underlying") or "")
        if not widget_id:
            continue

        should_exit, reason = _evaluate_exit(entry)

        if should_exit:
            client.close_all_positions(strategy="auto_paper_exit")
            close_ledger_entry(widget_id)
            actions.append(
                {
                    "action": "exit",
                    "underlying": underlying,
                    "widget_id": widget_id,
                    "reason": reason,
                }
            )
            logger.info("auto paper exit %s (%s)", underlying, reason)


def _try_entry(
    *,
    ticker: str,
    config: AutoPaperConfig,
    client: OpenAlgoClient,
    open_count: int,
    actions: list[dict[str, Any]],
) -> bool:
    if any(str(entry.get("underlying", "")).upper() == ticker for entry in list_open_entries()):
        actions.append({"action": "skip", "ticker": ticker, "reason": "already_open"})
        return False

    doc = _ensure_fresh_research(ticker)
    recommended = getattr(doc, "recommended", None) or {}
    if not recommended.get("name"):
        actions.append({"action": "skip", "ticker": ticker, "reason": "no_recommended_strategy"})
        return False

    widget = build_options_trade_widget_from_doc(doc)
    score = _strategy_score(widget)
    if score < config.min_strategy_score:
        actions.append(
            {
                "action": "skip",
                "ticker": ticker,
                "reason": f"score_{score:.2f}_below_{config.min_strategy_score}",
            }
        )
        return False

    orders = _orders_from_widget(widget, product=config.product)
    if not orders:
        actions.append({"action": "skip", "ticker": ticker, "reason": "no_orders"})
        return False

    allowed, reason = _budget_allows(
        config=config,
        widget=widget,
        orders=orders,
        client=client,
        open_count=open_count,
    )
    if not allowed:
        actions.append({"action": "skip", "ticker": ticker, "reason": reason})
        return False

    if not client.ensure_analyzer_mode():
        actions.append({"action": "skip", "ticker": ticker, "reason": "analyzer_mode_failed"})
        return False

    results = client.place_basket(orders, strategy="auto_paper")
    widget_id = _persist_widget(widget)
    record_execution_from_widget(widget, results, execution_mode="paper")
    actions.append(
        {
            "action": "enter",
            "ticker": ticker,
            "widget_id": widget_id,
            "strategy": (widget.get("recommended") or {}).get("name"),
            "score": score,
            "orders": len(orders),
        }
    )
    logger.info("auto paper entered %s via %s", ticker, widget_id)
    return True


def run_auto_paper_tick(*, dry_run: bool = False) -> dict[str, Any]:
    """Run one auto paper trading cycle."""
    config = get_auto_paper_config()
    session = load_session()
    if not is_auto_paper_active() and not session.get("enabled"):
        return {"status": "skipped", "reason": "auto_paper_disabled"}

    if session.get("halted"):
        return {"status": "halted", "halt_reason": session.get("halt_reason")}

    if not is_market_session_open(config):
        return {"status": "skipped", "reason": "outside_market_hours"}

    actions: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "status": "ok",
        "actions": actions,
        "dry_run": dry_run,
        "trade_executed": False,
    }

    try:
        client = OpenAlgoClient()
    except RuntimeError as exc:
        result["status"] = "error"
        result["reason"] = str(exc)
        record_tick_result(result)
        return result

    if dry_run:
        result["watchlist"] = _effective_watchlist(config)
        result["open_positions"] = len(list_open_entries())
        record_tick_result(result)
        return result

    try:
        if not client.is_trading_day():
            result["status"] = "skipped"
            result["reason"] = "market_holiday"
            record_tick_result(result)
            return result
    except Exception:
        logger.debug("market timings check failed; continuing with time window", exc_info=True)

    halt_reason = _check_daily_loss_halt(config, client, session)
    if halt_reason:
        result["status"] = "halted"
        result["halted"] = True
        result["halt_reason"] = halt_reason
        record_tick_result(result)
        return result

    if not client.ensure_analyzer_mode():
        result["status"] = "error"
        result["reason"] = "analyzer_mode_unavailable"
        record_tick_result(result)
        return result

    _handle_open_positions(client=client, actions=actions)

    open_entries = list_open_entries()
    open_count = len(open_entries)
    if open_count >= config.max_open_positions:
        result["open_positions"] = open_count
        record_tick_result(result)
        return result

    for ticker in _effective_watchlist(config):
        if _try_entry(
            ticker=ticker,
            config=config,
            client=client,
            open_count=open_count,
            actions=actions,
        ):
            result["trade_executed"] = True
            open_count += 1
            if open_count >= config.max_open_positions:
                break

    result["open_positions"] = len(list_open_entries())
    record_tick_result(result)
    return result
