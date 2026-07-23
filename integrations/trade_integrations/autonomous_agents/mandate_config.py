"""Structured mandate configuration for autonomous agent instances."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

HoldingPeriod = Literal["intraday", "overnight", "multi_day", "until_expiry"]
FlattenPolicy = Literal["session_close", "manual", "on_thesis_break", "on_max_loss", "never"]
ProductType = Literal["MIS", "NRML", "auto"]
RevisionPolicy = Literal["re_rank_on_alert", "scheduled_only", "user_guidance_only"]
StrategyStyle = Literal["event_vol", "directional", "income", "user_defined"]
PrimaryInstrument = Literal["options", "equity"]
AgentMode = Literal["trade", "observe"]

_OBSERVE_INTENT_RE = re.compile(
    r"\b("
    r"watch\s+(?:the\s+)?(?:index|nifty|banknifty|market)\s+and\s+report|"
    r"watch\s+and\s+report|monitor\s+and\s+report|observe\s+only|report\s+only|"
    r"just\s+watch|watch\s+only|monitor\s+only|no\s+trading|"
    r"don'?t\s+trade|do\s+not\s+trade"
    r")\b",
    re.I,
)
_TRADE_INTENT_RE = re.compile(
    r"\b(paper\s+trade|enter\s+(?:a\s+)?(?:trade|position)|execute|buy\s+|sell\s+|"
    r"option\s+chain|straddle|strangle|iron\s+condor|budget|max\s+loss)\b",
    re.I,
)


def detect_observe_intent(text: str) -> bool:
    """True when user wants watch/report only — not autonomous trading."""
    blob = str(text or "").strip()
    if not blob:
        return False
    if _TRADE_INTENT_RE.search(blob):
        return False
    return bool(_OBSERVE_INTENT_RE.search(blob))


def is_observe_agent(agent: dict[str, Any]) -> bool:
    mc = agent.get("mandate_config") if isinstance(agent.get("mandate_config"), dict) else {}
    mode = str(mc.get("agent_mode") or agent.get("agent_mode") or "trade").strip().lower()
    return mode == "observe"


def observe_mandate_text(symbol: str) -> str:
    sym = str(symbol or "NIFTY").strip().upper() or "NIFTY"
    return (
        f"Observe {sym}; watch index and post concise reports. "
        "No trading unless user explicitly asks."
    )


@dataclass
class AlertRules:
    spot_move_pct: float = 0.5
    vix_above: float | None = None
    vix_below: float | None = None
    thesis_break: bool = True
    news_enabled: bool = True
    pnl_loss_inr: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> AlertRules:
        if not payload:
            return cls()
        vix_above = payload.get("vix_above")
        vix_below = payload.get("vix_below")
        pnl_loss = payload.get("pnl_loss_inr")
        return cls(
            spot_move_pct=float(payload.get("spot_move_pct") or 0.5),
            vix_above=(float(vix_above) if vix_above is not None else None),
            vix_below=(float(vix_below) if vix_below is not None else None),
            thesis_break=bool(payload.get("thesis_break", True)),
            news_enabled=bool(payload.get("news_enabled", True)),
            pnl_loss_inr=(float(pnl_loss) if pnl_loss is not None else None),
        )


@dataclass
class MandateConfig:
    holding_period: HoldingPeriod = "multi_day"
    flatten_policy: FlattenPolicy = "manual"
    product_type: ProductType = "auto"
    market_hours_only: bool = True
    allowed_instruments: list[str] = field(default_factory=lambda: ["options"])
    strategy_style: StrategyStyle = "user_defined"
    max_open_positions: int = 1
    confidence_threshold: int = 75
    alert_rules: AlertRules = field(default_factory=AlertRules)
    revision_policy: RevisionPolicy = "re_rank_on_alert"
    agent_mode: AgentMode = "trade"
    watch_spec: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "holding_period": self.holding_period,
            "flatten_policy": self.flatten_policy,
            "product_type": self.product_type,
            "market_hours_only": self.market_hours_only,
            "allowed_instruments": list(self.allowed_instruments),
            "strategy_style": self.strategy_style,
            "max_open_positions": self.max_open_positions,
            "confidence_threshold": self.confidence_threshold,
            "alert_rules": self.alert_rules.to_dict(),
            "revision_policy": self.revision_policy,
            "agent_mode": self.agent_mode,
            "watch_spec": dict(self.watch_spec),
        }
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> MandateConfig:
        if not payload:
            return cls()
        holding = str(payload.get("holding_period") or "multi_day").lower()
        if holding not in ("intraday", "overnight", "multi_day", "until_expiry"):
            holding = "multi_day"
        flatten = str(payload.get("flatten_policy") or "manual").lower()
        if flatten not in ("session_close", "manual", "on_thesis_break", "on_max_loss", "never"):
            flatten = "manual"
        product = str(payload.get("product_type") or "auto").upper()
        if product not in ("MIS", "NRML", "AUTO"):
            product = "auto"
        revision = str(payload.get("revision_policy") or "re_rank_on_alert").lower()
        if revision not in ("re_rank_on_alert", "scheduled_only", "user_guidance_only"):
            revision = "re_rank_on_alert"
        agent_mode = str(payload.get("agent_mode") or "trade").lower()
        if agent_mode not in ("trade", "observe"):
            agent_mode = "trade"
        style = str(payload.get("strategy_style") or "user_defined").lower()
        if style not in ("event_vol", "directional", "income", "user_defined"):
            style = "user_defined"
        instruments = payload.get("allowed_instruments")
        if not isinstance(instruments, list):
            instruments = ["options"]
        watch_spec = payload.get("watch_spec")
        if not isinstance(watch_spec, dict):
            watch_spec = {}
        try:
            max_pos = int(payload.get("max_open_positions") or 1)
        except (TypeError, ValueError):
            max_pos = 1
        try:
            threshold = int(payload.get("confidence_threshold") or 75)
        except (TypeError, ValueError):
            threshold = 75
        return cls(
            holding_period=holding,  # type: ignore[arg-type]
            flatten_policy=flatten,  # type: ignore[arg-type]
            product_type=product if product != "AUTO" else "auto",  # type: ignore[arg-type]
            market_hours_only=bool(payload.get("market_hours_only", True)),
            allowed_instruments=[str(x) for x in instruments if str(x).strip()],
            strategy_style=style,  # type: ignore[arg-type]
            max_open_positions=max(1, max_pos),
            confidence_threshold=max(0, min(100, threshold)),
            alert_rules=AlertRules.from_dict(
                payload.get("alert_rules") if isinstance(payload.get("alert_rules"), dict) else None
            ),
            revision_policy=revision,  # type: ignore[arg-type]
            agent_mode=agent_mode,  # type: ignore[arg-type]
            watch_spec=watch_spec,
        )

    def resolve_product(self) -> str:
        if self.product_type == "MIS":
            return "MIS"
        if self.product_type == "NRML":
            return "NRML"
        return "MIS" if self.holding_period == "intraday" else "NRML"

    def needs_session_close_flatten(self) -> bool:
        return self.flatten_policy == "session_close" or (
            self.holding_period == "intraday" and self.flatten_policy not in ("never", "manual")
        )


def _watch_exchange_for_symbol(symbol: str) -> str:
    try:
        from trade_integrations.dataflows.company_research.market import Market, detect_market

        return "US" if detect_market(symbol) == Market.US else "NSE"
    except Exception:
        return "NSE"


def build_us_mandate_config(
    *,
    symbols: list[str] | None = None,
    confidence_threshold: int = 75,
    spot_move_pct: float = 0.5,
    allowed_instruments: list[str] | None = None,
) -> MandateConfig:
    """Mandate defaults for US paper via Alpaca."""
    focus = (symbols[0] if symbols else "SPY").upper()
    instruments = allowed_instruments or ["equity"]
    cfg = MandateConfig(
        holding_period="multi_day",
        flatten_policy="manual",
        product_type="auto",
        market_hours_only=False,
        allowed_instruments=list(instruments),
        strategy_style="directional" if "equity" in instruments else "event_vol",
        max_open_positions=1,
        confidence_threshold=confidence_threshold,
    )
    cfg.alert_rules = AlertRules(
        spot_move_pct=spot_move_pct,
        vix_above=None,
        vix_below=None,
        thesis_break=True,
        news_enabled=True,
        pnl_loss_inr=None,
    )
    cfg.watch_spec = to_watch_spec(cfg, symbols=[focus])
    return cfg


def to_watch_spec(mandate: MandateConfig, *, symbols: list[str]) -> dict[str, Any]:
    """Build Nautilus-compatible watch_spec from mandate alert rules."""
    if mandate.watch_spec.get("rules"):
        spec = dict(mandate.watch_spec)
        rules = []
        for row in spec.get("rules") or []:
            if not isinstance(row, dict):
                continue
            patched = dict(row)
            sym = str(patched.get("symbol") or "").upper()
            if sym and not patched.get("exchange"):
                patched["exchange"] = _watch_exchange_for_symbol(sym)
            rules.append(patched)
        spec["rules"] = rules
        return spec
    rules: list[dict[str, Any]] = []
    focus = (symbols[0] if symbols else "NIFTY").upper()
    exchange = _watch_exchange_for_symbol(focus)
    spot_pct = mandate.alert_rules.spot_move_pct
    if spot_pct and spot_pct > 0:
        rules.append(
            {
                "symbol": focus,
                "metric": "spot_move_pct",
                "threshold": spot_pct,
                "direction": "either",
                "exchange": exchange,
            }
        )
    if mandate.alert_rules.vix_above is not None:
        rules.append(
            {
                "symbol": "INDIAVIX",
                "metric": "level_above",
                "threshold": mandate.alert_rules.vix_above,
            }
        )
    if mandate.alert_rules.vix_below is not None:
        rules.append(
            {
                "symbol": "INDIAVIX",
                "metric": "level_below",
                "threshold": mandate.alert_rules.vix_below,
            }
        )
    cooldown = int(mandate.watch_spec.get("cooldown_sec") or 300)
    return {
        "rules": rules,
        "gate": {"skip_if_unchanged_minutes": int(mandate.watch_spec.get("skip_if_unchanged_minutes") or 5)},
        "cooldown_sec": cooldown,
        "review_triggers": ["watch_rule_fired", "thesis_break", "news_material"],
    }


def parse_mandate_from_text(
    mandate_text: str,
    *,
    symbols: list[str] | None = None,
    budget_inr: float = 20_000.0,
    max_daily_loss_inr: float = 2_000.0,
    confidence_threshold: int = 75,
    alert_spot_move_pct: float = 0.5,
) -> MandateConfig:
    """Heuristic parser from user natural language + proposal defaults."""
    text = (mandate_text or "").lower()
    cfg = MandateConfig(confidence_threshold=confidence_threshold)
    cfg.alert_rules.spot_move_pct = alert_spot_move_pct
    if cfg.alert_rules.pnl_loss_inr is None:
        cfg.alert_rules.pnl_loss_inr = max_daily_loss_inr * 0.75

    if any(w in text for w in ("intraday", "same day", "same-day", "day trade", "by close", "flatten by close")):
        cfg.holding_period = "intraday"
        cfg.flatten_policy = "session_close"
        cfg.product_type = "MIS"
    elif any(w in text for w in ("overnight", "carry", "next day")):
        cfg.holding_period = "overnight"
        cfg.flatten_policy = "manual"
        cfg.product_type = "NRML"
    elif any(w in text for w in ("until expiry", "through expiry", "expiry")):
        cfg.holding_period = "until_expiry"
        cfg.flatten_policy = "on_thesis_break"
        cfg.product_type = "NRML"
    elif any(w in text for w in ("swing", "multi-day", "multi day", "week")):
        cfg.holding_period = "multi_day"
        cfg.flatten_policy = "on_thesis_break"
        cfg.product_type = "NRML"

    if any(w in text for w in ("event vol", "event volatility", "straddle", "rbi", "earnings")):
        cfg.strategy_style = "event_vol"
    elif any(w in text for w in ("directional", "trend", "breakout")):
        cfg.strategy_style = "directional"
    elif any(w in text for w in ("income", "premium", "credit spread", "iron condor")):
        cfg.strategy_style = "income"

    if any(w in text for w in ("watch only", "don't trade", "do not trade", "wait until")):
        cfg.revision_policy = "user_guidance_only"

    if detect_observe_intent(text):
        cfg.agent_mode = "observe"
        cfg.revision_policy = "user_guidance_only"
        cfg.allowed_instruments = ["equity"]
        cfg.max_open_positions = 0

    if any(
        w in text
        for w in (
            "equity",
            "stock",
            "shares",
            "share",
            "alpaca",
            "us paper",
            "spy",
            "qqq",
            "nvda",
            "aapl",
        )
    ):
        if "options" not in text:
            cfg.allowed_instruments = ["equity"]
        else:
            cfg.allowed_instruments = ["options", "equity"]
        cfg.market_hours_only = False

    vix_above = re.search(r"vix\s*(?:>|above|over)\s*(\d+(?:\.\d+)?)", text)
    if vix_above:
        cfg.alert_rules.vix_above = float(vix_above.group(1))
    vix_below = re.search(r"vix\s*(?:<|below|under)\s*(\d+(?:\.\d+)?)", text)
    if vix_below:
        cfg.alert_rules.vix_below = float(vix_below.group(1))

    if "24" in text or "us session" in text or "nvda" in text:
        cfg.market_hours_only = False

    sym_list = symbols or ["NIFTY"]
    cfg.watch_spec = to_watch_spec(cfg, symbols=sym_list)
    return cfg


def _infer_allowed_instruments(
    mandate_text: str,
    symbols: list[str],
    *,
    is_us: bool,
) -> list[str] | None:
    """Return allowed_instruments when mandate text clearly signals equity vs options."""
    text = mandate_text.lower()
    index_symbols = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY"}
    sym0 = (symbols[0] if symbols else "").upper()

    equity_only_phrases = (
        "stocks only",
        "stock only",
        "equity only",
        "shares only",
        "no options",
        "not options",
        "without options",
        "no option",
        "not option",
    )
    if any(p in text for p in equity_only_phrases):
        return ["equity"]

    options_phrases = (
        "iron condor",
        "straddle",
        "strangle",
        "option chain",
        "credit spread",
        "debit spread",
        "sell otm",
        "buy wings",
    )
    if any(p in text for p in options_phrases):
        return ["options"]

    equity_signals = (
        "equity",
        "stock",
        "stocks",
        "shares",
        "share",
        "cnc",
        "cash market",
        "underlying stock",
    )
    options_signals = (
        "options",
        "option chain",
        "calls",
        "puts",
    )

    has_equity = any(w in text for w in equity_signals)
    has_options = any(w in text for w in options_signals)

    if not has_options and any(w in text for w in ("buy", "sell")) and sym0 not in index_symbols and len(sym0) > 2:
        has_equity = True

    if "mis" in text and "nrml" not in text and not has_options:
        has_equity = True

    if has_equity and not has_options:
        return ["equity"]
    if has_options and not has_equity:
        return ["options"]
    if has_options and has_equity:
        if sym0 in index_symbols and not is_us:
            return ["options"]
        if is_us:
            return ["equity"]
        return ["equity"]
    return None


def primary_instrument_from_mandate(
    mc: MandateConfig,
    *,
    market: str,
    mandate_text: str = "",
    symbols: list[str] | None = None,
) -> PrimaryInstrument:
    """Choose primary instrument class when mandate lists both options and equity."""
    allowed = {str(x).strip().lower() for x in (mc.allowed_instruments or []) if str(x).strip()}
    if allowed == {"equity"} or (allowed and "options" not in allowed):
        return "equity"
    if allowed == {"options"} or (allowed and "equity" not in allowed):
        return "options"

    text = mandate_text.lower()
    index_symbols = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY"}
    sym0 = ((symbols or ["NIFTY"])[0] or "NIFTY").upper()

    equity_only_phrases = (
        "stocks only",
        "stock only",
        "equity only",
        "shares only",
        "no options",
        "not options",
        "without options",
        "no option",
    )
    if any(p in text for p in equity_only_phrases):
        return "equity"

    options_phrases = (
        "iron condor",
        "straddle",
        "strangle",
        "option chain",
        "credit spread",
        "debit spread",
    )
    if any(p in text for p in options_phrases):
        return "options"

    if sym0 in index_symbols and market == "IN":
        return "options"
    if market == "US" and sym0 not in index_symbols:
        return "equity"
    if "equity" in allowed and "options" in allowed:
        return "equity" if sym0 not in index_symbols else "options"
    return "options"


def resolve_mandate_config(
    *,
    symbols: list[str],
    mandate_text: str = "",
    stored: dict[str, Any] | None = None,
    budget_inr: float = 20_000.0,
    max_daily_loss_inr: float = 2_000.0,
    confidence_threshold: int = 75,
    alert_spot_move_pct: float = 0.5,
    execution_market: str | None = None,
) -> MandateConfig:
    """Single entry point for mandate resolution (proposals, agents, sessions)."""
    sym_list = [str(s).strip().upper() for s in symbols if str(s).strip()] or ["NIFTY"]
    primary = sym_list[0]

    if execution_market:
        is_us = str(execution_market).upper() == "US"
    else:
        try:
            from trade_integrations.dataflows.company_research.market import Market, detect_market

            is_us = detect_market(primary) == Market.US
        except Exception:
            is_us = False

    if isinstance(stored, dict) and stored:
        cfg = MandateConfig.from_dict(stored)
        explicit_instruments = stored.get("allowed_instruments")
        if (
            is_us
            and cfg.allowed_instruments == ["options"]
            and explicit_instruments != ["options"]
        ):
            cfg.allowed_instruments = ["equity"]
            cfg.market_hours_only = False
        elif is_us:
            cfg.market_hours_only = False
    elif is_us:
        cfg = build_us_mandate_config(
            symbols=sym_list,
            confidence_threshold=confidence_threshold,
            spot_move_pct=alert_spot_move_pct,
        )
    else:
        cfg = parse_mandate_from_text(
            mandate_text,
            symbols=sym_list,
            budget_inr=budget_inr,
            max_daily_loss_inr=max_daily_loss_inr,
            confidence_threshold=confidence_threshold,
            alert_spot_move_pct=alert_spot_move_pct,
        )

    if not cfg.watch_spec.get("rules"):
        cfg.watch_spec = to_watch_spec(cfg, symbols=sym_list)

    explicit_from_stored: list[str] | None = None
    if isinstance(stored, dict):
        raw = stored.get("allowed_instruments")
        if isinstance(raw, list) and raw:
            explicit_from_stored = [str(x) for x in raw]

    inferred = _infer_allowed_instruments(mandate_text, sym_list, is_us=is_us)
    if inferred is not None:
        cfg.allowed_instruments = inferred
    else:
        resolved = resolve_allowed_instruments(
            sym_list,
            mandate_text,
            execution_market="US" if is_us else "IN",
            explicit=explicit_from_stored,
        )
        if resolved is not None:
            cfg.allowed_instruments = resolved

    return cfg


_INDEX_SYMBOLS = frozenset(
    {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY", "NIFTY50"}
)


def resolve_allowed_instruments(
    symbols: list[str],
    mandate_text: str = "",
    *,
    execution_market: str | None = None,
    explicit: list[str] | None = None,
) -> list[str] | None:
    """Return allowed_instruments or None when index instrument type is ambiguous."""
    if explicit:
        normalized = [str(x).strip().lower() for x in explicit if str(x).strip()]
        if normalized:
            return normalized

    sym_list = [str(s).strip().upper() for s in symbols if str(s).strip()]
    sym0 = sym_list[0] if sym_list else ""
    is_us = str(execution_market or "").upper() == "US"

    if detect_observe_intent(mandate_text):
        return ["equity"]

    inferred = _infer_allowed_instruments(mandate_text, sym_list, is_us=is_us)
    if inferred is not None:
        return inferred

    if is_us:
        return ["equity"]

    if sym0 in _INDEX_SYMBOLS:
        text = mandate_text.lower()
        if any(w in text for w in ("equity", "stock", "shares", "etf", "not options", "no options")):
            return ["equity"]
        if any(
            w in text
            for w in (
                "option",
                "options",
                "straddle",
                "strangle",
                "iron condor",
                "intraday",
                "event vol",
                "directional",
            )
        ):
            return ["options"]
        return None

    if sym0:
        return ["equity"]

    return ["equity"]


def mandate_config_from_agent(agent: dict[str, Any]) -> MandateConfig:
    sym_list = list(agent.get("symbols") or ["NIFTY"])
    constraints = dict(agent.get("constraints") or {})
    alert_rules = dict(agent.get("alert_rules") or {})
    return resolve_mandate_config(
        symbols=sym_list,
        mandate_text=str(agent.get("mandate") or ""),
        stored=agent.get("mandate_config") if isinstance(agent.get("mandate_config"), dict) else None,
        budget_inr=float(constraints.get("budget_inr") or 20_000),
        max_daily_loss_inr=float(constraints.get("max_daily_loss_inr") or 2_000),
        confidence_threshold=int(constraints.get("confidence_threshold") or 75),
        alert_spot_move_pct=float(alert_rules.get("spot_move_pct") or 0.5),
        execution_market=str(agent.get("execution_market") or "") or None,
    )


def mandate_config_from_session(session: dict[str, Any]) -> MandateConfig:
    sym_list = list(session.get("watchlist") or ["NIFTY"])
    stored = session.get("mandate_config") if isinstance(session.get("mandate_config"), dict) else None
    return resolve_mandate_config(
        symbols=sym_list,
        mandate_text=str(session.get("mandate") or ""),
        stored=stored,
        max_daily_loss_inr=float(session.get("max_daily_loss_inr") or 2_000),
    )


def scheduled_actions_for(mandate: MandateConfig) -> list[str]:
    """Return scheduler job kinds to register for this mandate."""
    actions: list[str] = ["agent_turn", "scheduler_health"]
    if mandate.needs_session_close_flatten():
        actions.append("session_close_flatten")
    if mandate.holding_period == "until_expiry":
        actions.append("expiry_check")
    return actions
