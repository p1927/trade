"""Legacy poll-loop fallback (used for --dry-run and --legacy-poll)."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from nautilus_openalgo_bridge.config import get_bridge_config, is_bridge_market_open, is_watch_enabled
from nautilus_openalgo_bridge.data_feed import OpenAlgoQuoteFeed
from nautilus_openalgo_bridge.handoff import handoff_mtime, load_handoff
from nautilus_openalgo_bridge.intent_queue import process_pending_intents
from nautilus_openalgo_bridge.models import BridgeSignal, WatchSpec
from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client
from nautilus_openalgo_bridge.reconcile import open_positions_from_book, total_unrealized_pnl
from nautilus_openalgo_bridge.stop_eval import evaluate_stop_rules
from nautilus_openalgo_bridge.thesis_eval import evaluate_thesis_for_agent
from nautilus_openalgo_bridge.watch_eval import evaluate_watch_spec

if TYPE_CHECKING:
    from nautilus_openalgo_bridge.ws_feed import OpenAlgoWsWatchFeed

logger = logging.getLogger(__name__)

_ws_feed: Any | None = None
_ws_feed_lock = __import__("threading").Lock()
_ws_feed_generation: str | None = None


def _agent_mtime(agent_id: str) -> float | None:
    from nautilus_openalgo_bridge.hub_paths import agent_json_path

    path = agent_json_path(agent_id)
    if not path.is_file():
        return None
    return path.stat().st_mtime


def _default_watch_spec() -> WatchSpec:
    return WatchSpec.from_dict({})


def _resolve_watch_spec(agent_id: str | None) -> WatchSpec:
    if agent_id:
        handoff = load_handoff(agent_id)
        if handoff and handoff.watch_spec.rules:
            return handoff.watch_spec
        return WatchSpec.from_dict({})
    return _default_watch_spec()


def maybe_reload_watch_spec(
    agent_id: str | None,
    spec: WatchSpec,
    *,
    last_handoff_mtime: float | None,
    last_agent_mtime: float | None,
) -> tuple[WatchSpec, float | None, float | None]:
    """Re-read watch spec when handoff or agent JSON changes on disk."""
    if not agent_id:
        return spec, last_handoff_mtime, last_agent_mtime

    mt = handoff_mtime(agent_id)
    am = _agent_mtime(agent_id)
    if mt is not None and mt != last_handoff_mtime:
        return _resolve_watch_spec(agent_id), mt, am if am is not None else last_agent_mtime
    if am is not None and am != last_agent_mtime:
        return _resolve_watch_spec(agent_id), last_handoff_mtime, am
    return spec, last_handoff_mtime, last_agent_mtime


def _dispatch_alerts(
    agent_id: str,
    alerts: list,
    *,
    quotes: dict,
    trigger_vibe: bool,
    market: str | None = None,
) -> list[dict]:
    dispatch_results: list[dict] = []
    if not trigger_vibe:
        return dispatch_results
    from nautilus_openalgo_bridge.config import allow_vibe_alert_outside_market_hours
    from nautilus_openalgo_bridge.market_hours import is_agent_watch_session_open
    from nautilus_openalgo_bridge.signal_actions import dispatch_exit_intent
    from nautilus_openalgo_bridge.vibe_trigger import dispatch_thesis_alert_sync, dispatch_watch_alert_sync

    outside_hours = not allow_vibe_alert_outside_market_hours()

    for alert in alerts:
        if alert.signal == BridgeSignal.EXIT_NOW:
            dispatch_results.append(dispatch_exit_intent(agent_id, alert))
        elif alert.signal == BridgeSignal.THESIS_BROKEN:
            if outside_hours and not is_agent_watch_session_open(agent_id):
                dispatch_results.append({"status": "skipped", "reason": "outside_market_hours"})
                continue
            dispatch_results.append(dispatch_thesis_alert_sync(agent_id, alert, quotes=quotes))
        elif alert.signal == BridgeSignal.REVIEW_NEEDED:
            if outside_hours and not is_agent_watch_session_open(agent_id):
                dispatch_results.append({"status": "skipped", "reason": "outside_market_hours"})
                continue
            dispatch_results.append(dispatch_watch_alert_sync(agent_id, alert, quotes=quotes))
    return dispatch_results


def run_once(
    *,
    agent_id: str | None = None,
    baselines: dict[str, float] | None = None,
    trigger_vibe: bool = False,
    process_intents: bool = False,
) -> dict[str, Any]:
    from nautilus_openalgo_bridge.poll_latency import poll_eval_timer

    with poll_eval_timer():
        return _run_once_impl(
            agent_id=agent_id,
            baselines=baselines,
            trigger_vibe=trigger_vibe,
            process_intents=process_intents,
        )


def _resolve_poll_symbols(agent_id: str | None) -> list[str]:
    if not agent_id:
        return []
    try:
        from trade_integrations.watch_registry.scope import symbols_for_owner

        return list(symbols_for_owner(agent_id))
    except Exception:
        return []


def _close_ws_feed() -> None:
    global _ws_feed, _ws_feed_generation
    with _ws_feed_lock:
        feed = _ws_feed
        _ws_feed = None
        _ws_feed_generation = None
    if feed is not None:
        try:
            feed.close()
        except Exception:
            logger.debug("WS watch feed close failed", exc_info=True)


def _get_ws_feed(context_generation: str | None) -> OpenAlgoWsWatchFeed:
    global _ws_feed, _ws_feed_generation
    from nautilus_openalgo_bridge.ws_feed import OpenAlgoWsWatchFeed

    generation = str(context_generation or "").strip()
    stale = None
    with _ws_feed_lock:
        if _ws_feed is not None and _ws_feed_generation != generation:
            stale = _ws_feed
            _ws_feed = None
            _ws_feed_generation = None
        if _ws_feed is None:
            _ws_feed = OpenAlgoWsWatchFeed(context_generation=generation)
            _ws_feed_generation = generation or None
        feed = _ws_feed
    if stale is not None:
        try:
            stale.close()
        except Exception:
            logger.debug("WS watch feed recycle failed", exc_info=True)
    return feed


def _poll_quotes(
    symbols: list[str],
    *,
    context_generation: str | None = None,
    rest_feed: OpenAlgoQuoteFeed | None = None,
) -> dict:
    """Poll quotes via WS when configured, with REST fallback on empty or error."""
    cfg = get_bridge_config()
    if cfg.watch_feed_mode != "ws" or not symbols:
        feed = rest_feed or OpenAlgoQuoteFeed()
        return feed.poll(symbols)

    from nautilus_openalgo_bridge.ws_feed import OpenAlgoWsWatchFeed, ticks_to_quote_snapshots

    ws_feed: OpenAlgoWsWatchFeed | None = None
    try:
        ws_feed = _get_ws_feed(context_generation)
        ws_feed.subscribe(symbols)
        ticks = ws_feed.poll_ticks()
        if ticks:
            return ticks_to_quote_snapshots(ticks)
    except Exception:
        logger.debug("WS watch feed poll failed; falling back to REST", exc_info=True)
        _close_ws_feed()

    feed = rest_feed or OpenAlgoQuoteFeed()
    return feed.poll(symbols)


def _run_once_impl(
    *,
    agent_id: str | None = None,
    baselines: dict[str, float] | None = None,
    trigger_vibe: bool = False,
    process_intents: bool = False,
) -> dict[str, Any]:
    cfg = get_bridge_config()
    spec = _resolve_watch_spec(agent_id)
    poll_symbols = _resolve_poll_symbols(agent_id)
    handoff = load_handoff(agent_id) if agent_id else None
    context_generation = handoff.context_generation if handoff else None
    quotes = _poll_quotes(poll_symbols, context_generation=context_generation)
    alerts = evaluate_watch_spec(spec, quotes, baselines=baselines or {})
    if handoff:
        try:
            client = get_openalgo_client(cfg)
            rows = open_positions_from_book(client.get_position_book())
            pnl = total_unrealized_pnl(rows)
            stop_alert = evaluate_stop_rules(handoff, quotes, unrealized_pnl_inr=pnl, config=cfg)
            if stop_alert is not None:
                alerts.insert(0, stop_alert)
            if agent_id:
                from nautilus_openalgo_bridge.reconcile import maybe_reconcile_handoff_mismatch

                mismatch_alert = maybe_reconcile_handoff_mismatch(agent_id, client=client)
                if mismatch_alert is not None:
                    alerts.append(mismatch_alert)
        except RuntimeError as exc:
            logger.debug("stop rule eval skipped: %s", exc)

    if agent_id:
        focus = handoff.underlying if handoff else None
        if not focus:
            try:
                from trade_integrations.autonomous_agents.store import get_agent

                agent = get_agent(agent_id) or {}
                syms = list(agent.get("symbols") or ["NIFTY"])
                focus = syms[0]
            except Exception:
                focus = "NIFTY"
        quote = quotes.get(str(focus).upper()) if focus else None
        live_spot = quote.ltp if quote else None
        pnl = None
        if handoff:
            try:
                client = get_openalgo_client(cfg)
                rows = open_positions_from_book(client.get_position_book())
                pnl = total_unrealized_pnl(rows)
            except RuntimeError:
                pass
        thesis_alert = evaluate_thesis_for_agent(agent_id, live_spot=live_spot, position_pnl=pnl)
        if thesis_alert is not None:
            alerts.insert(0, thesis_alert)

    intent_results: list[dict] = []

    if process_intents:
        try:
            intent_results = process_pending_intents(client=get_openalgo_client(cfg))
        except RuntimeError as exc:
            logger.warning("intent queue processing failed: %s", exc)

    dispatch_results = (
        _dispatch_alerts(
            agent_id,
            alerts,
            quotes=quotes,
            trigger_vibe=bool(trigger_vibe and agent_id),
        )
        if agent_id
        else []
    )

    return {
        "quotes": {k: v.to_dict() for k, v in quotes.items()},
        "alerts": [a.to_dict() for a in alerts],
        "dispatches": dispatch_results,
        "intents_processed": intent_results,
    }


def run_once_alpaca(
    *,
    agent_id: str | None = None,
    baselines: dict[str, float] | None = None,
    trigger_vibe: bool = False,
    process_intents: bool = False,
) -> dict[str, Any]:
    """Deprecated alias — US watch uses OpenAlgo via ``run_once``."""
    return run_once(
        agent_id=agent_id,
        baselines=baselines,
        trigger_vibe=trigger_vibe,
        process_intents=process_intents,
    )


def _poll_loop_market(agent_id: str | None) -> str:
    try:
        from nautilus_openalgo_bridge.market_hours import agent_market

        return agent_market(agent_id)
    except Exception:
        return "IN"


def run_poll_loop(
    *,
    agent_id: str | None = None,
    once: bool = False,
    trigger_vibe: bool = False,
    dry_run: bool = False,
    process_intents: bool = True,
) -> int:
    if not dry_run and not is_watch_enabled():
        logger.error("NAUTILUS_WATCH_ENABLE=0 — use --dry-run or set NAUTILUS_WATCH_ENABLE=true")
        return 1

    cfg = get_bridge_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if dry_run:
        logger.info("dry-run: testing OpenAlgo quote feed only")
        try:
            client = get_openalgo_client(cfg)
            _ = client.get_funds()
        except RuntimeError as exc:
            logger.error("OpenAlgo unreachable: %s", exc)
            return 1
        result = run_once(agent_id=agent_id, trigger_vibe=False, process_intents=False)
        print(json.dumps(result, indent=2))
        return 0

    if trigger_vibe and agent_id:
        from nautilus_openalgo_bridge.vibe_trigger import ping_vibe_backend

        vibe_health = ping_vibe_backend(cfg)
        logger.info("Vibe backend probe: %s", vibe_health.get("status"))
        if vibe_health.get("status") == "unreachable":
            logger.error("Vibe backend unreachable at %s", cfg.vibe_backend_url)
            return 1

    rest_feed = OpenAlgoQuoteFeed()
    spec = _resolve_watch_spec(agent_id)
    baselines: dict[str, float] = {}
    last_alert_at: dict[str, float] = {}
    last_handoff_mtime: float | None = handoff_mtime(agent_id) if agent_id else None
    last_agent_mtime: float | None = _agent_mtime(agent_id) if agent_id else None
    last_vibe_dispatch_at: float = 0.0

    logger.info(
        "Poll-loop watch bridge (legacy) poll=%sms agent=%s trigger_vibe=%s",
        cfg.quote_poll_ms,
        agent_id or "default",
        trigger_vibe and bool(agent_id),
    )

    from nautilus_openalgo_bridge.market_hours import closed_market_poll_interval_sec, is_market_open_for_market

    loop_market = _poll_loop_market(agent_id)

    while True:
        if not is_market_open_for_market(loop_market):
            if once:
                logger.info("market closed (%s) — exiting poll loop", loop_market)
                return 0
            time.sleep(closed_market_poll_interval_sec())
            continue

        if agent_id:
            spec, last_handoff_mtime, last_agent_mtime = maybe_reload_watch_spec(
                agent_id,
                spec,
                last_handoff_mtime=last_handoff_mtime,
                last_agent_mtime=last_agent_mtime,
            )

        if process_intents:
            try:
                for row in process_pending_intents(client=get_openalgo_client(cfg), max_count=3):
                    logger.info("intent queue: %s → %s", row.get("intent_id"), row.get("status"))
            except RuntimeError as exc:
                logger.debug("intent queue tick skipped: %s", exc)

        poll_symbols = _resolve_poll_symbols(agent_id)
        handoff = load_handoff(agent_id) if agent_id else None
        context_generation = handoff.context_generation if handoff else None
        quotes = _poll_quotes(
            poll_symbols,
            context_generation=context_generation,
            rest_feed=rest_feed,
        )
        for symbol, snap in quotes.items():
            baselines.setdefault(symbol, snap.ltp)

        alerts = evaluate_watch_spec(spec, quotes, baselines=baselines)
        if handoff:
            try:
                client = get_openalgo_client(cfg)
                rows = open_positions_from_book(client.get_position_book())
                pnl = total_unrealized_pnl(rows)
                stop_alert = evaluate_stop_rules(handoff, quotes, unrealized_pnl_inr=pnl, config=cfg)
                if stop_alert is not None:
                    alerts.insert(0, stop_alert)
            except RuntimeError:
                pass

        now = time.time()
        gate_minutes = spec.gate.skip_if_unchanged_minutes if spec.gate else 30

        for alert in alerts:
            key = f"{alert.symbol}:{alert.rule.metric if alert.rule else alert.signal.value}"
            if now - last_alert_at.get(key, 0.0) < cfg.alert_cooldown_sec:
                continue
            last_alert_at[key] = now
            logger.warning("WATCH ALERT: %s", alert.message)

            if alert.signal == BridgeSignal.EXIT_NOW and agent_id:
                from nautilus_openalgo_bridge.signal_actions import dispatch_exit_intent

                result = dispatch_exit_intent(agent_id, alert, underlying=handoff.underlying if handoff else None)
                logger.info("EXIT intent: %s", result.get("status"))
                continue

            if not trigger_vibe or not agent_id:
                continue
            if alert.signal == BridgeSignal.THESIS_BROKEN:
                from nautilus_openalgo_bridge.config import allow_vibe_alert_outside_market_hours
                from nautilus_openalgo_bridge.market_hours import is_agent_watch_session_open
                from nautilus_openalgo_bridge.vibe_trigger import dispatch_thesis_alert_sync

                if not allow_vibe_alert_outside_market_hours() and not is_agent_watch_session_open(agent_id):
                    continue
                result = dispatch_thesis_alert_sync(agent_id, alert, quotes=quotes)
                if result.get("status") == "dispatched":
                    last_vibe_dispatch_at = now
                continue

            if alert.signal != BridgeSignal.REVIEW_NEEDED:
                continue
            if spec.gate and (now - last_vibe_dispatch_at) < gate_minutes * 60:
                continue
            from nautilus_openalgo_bridge.config import allow_vibe_alert_outside_market_hours
            from nautilus_openalgo_bridge.market_hours import is_agent_watch_session_open

            if not allow_vibe_alert_outside_market_hours() and not is_agent_watch_session_open(agent_id):
                continue
            from nautilus_openalgo_bridge.vibe_trigger import dispatch_watch_alert_sync

            result = dispatch_watch_alert_sync(agent_id, alert, quotes=quotes)
            if result.get("status") == "dispatched":
                last_vibe_dispatch_at = now

        if once:
            return 0
        time.sleep(cfg.quote_poll_ms / 1000.0)
