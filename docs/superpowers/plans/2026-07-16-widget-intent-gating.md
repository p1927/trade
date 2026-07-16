# Widget Intent Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit trade-plan widgets in Vibe chat only when user intent and hub data warrant it (Option A opt-in), with mode-specific UI sections so FNO charges/payoff never appear on browse or index-outlook answers.

**Architecture:** Add `classify_widget_intent()` + `is_widget_presentable()` shared gates in the agent trade layer. Prefetch always loads hub research but auto-emits widgets only on non-`none` intent with presentable payloads. Flip auto-emit env defaults to false. Add `presentation_mode` to widget payloads; frontend renders sections conditionally. Fix index widget persist + SSE relay.

**Tech Stack:** Python (`vibetrading/agent`, `integrations/trade_integrations`, `openalgo/mcp`), React (`TradePlanWidgetCard`, `Agent.tsx`), existing SSE `trade_plan.widget` events.

## Global Constraints

- India options/stocks via **OpenAlgo** only; free/open-source data sources only.
- Hub artifacts remain source of truth: `reports/hub/{SYMBOL}/options_research/latest.json`.
- Widgets live in **Vibe chat**; Strategy Builder stays deep-link only.
- All P&L and charges visible in widget when `presentation_mode` is `options_strategy` or `stock_trade`.
- Live orders only after explicit user confirmation in widget dialog.
- No new summary docs, demos, or mocks beyond tasks listed here.
- Do not commit unless user requests.

**Design spec:** `docs/superpowers/specs/2026-07-16-widget-intent-gating-design.md`

---

### Task 1: Widget intent classifier

**Files:**
- Create: `vibetrading/agent/src/trade/widget_intent.py`
- Modify: `vibetrading/agent/src/trade/symbol_detect.py` (re-export or delegate)
- Test: `vibetrading/agent/tests/test_widget_intent.py`

**Interfaces:**
- Produces: `WidgetIntent` literal type, `classify_widget_intent(text: str) -> WidgetIntent`, `detect_browse_intent(text: str) -> bool`, `detect_index_outlook_intent(text: str) -> bool`, `detect_stock_trade_intent(text: str) -> bool`
- Consumes: existing `detect_options_intent`, `detect_finalize_intent` from `symbol_detect.py`

- [ ] **Step 1: Write failing tests**

```python
# vibetrading/agent/tests/test_widget_intent.py
import pytest
from src.trade.widget_intent import classify_widget_intent

@pytest.mark.parametrize("msg,expected", [
    ("Show RELIANCE option chain", "none"),
    ("What's NIFTY doing today?", "index_outlook"),
    ("Iron condor on NIFTY", "options_strategy"),
    ("Best option strategy for RELIANCE", "options_strategy"),
    ("Should I buy RELIANCE shares?", "stock_trade"),
    ("Finalize and execute the plan", "execute_refresh"),
    ("NIFTY events this week", "none"),
])
def test_classify_widget_intent(msg, expected):
    assert classify_widget_intent(msg) == expected

def test_browse_overrides_strategy_keywords():
    assert classify_widget_intent("Browse NIFTY strikes and OI") == "none"

def test_options_beats_index_on_mixed():
    assert classify_widget_intent("NIFTY direction and iron condor") == "options_strategy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vibetrading/agent && python -m pytest tests/test_widget_intent.py -v`  
Expected: FAIL — module not found

- [ ] **Step 3: Implement classifier**

```python
# vibetrading/agent/src/trade/widget_intent.py
from __future__ import annotations
import re
from typing import Literal
from src.trade.symbol_detect import detect_finalize_intent, detect_options_intent

WidgetIntent = Literal["none", "index_outlook", "options_strategy", "stock_trade", "execute_refresh"]

_BROWSE_HINT = re.compile(
    r"\b(chain|OI|open\s+interest|browse|expiries|expiry\s+list|list\s+strikes|"
    r"available\s+strikes|show\s+chain)\b", re.I)
_INDEX_OUTLOOK_HINT = re.compile(
    r"\b(where\s+(is|are)|headed|direction|outlook|factors?|macro|scenario|"
    r"index\s+view|market\s+view)\b", re.I)
_STOCK_TRADE_HINT = re.compile(
    r"\b(buy|sell|hold|accumulate|reduce|shares|equity|stock\s+position)\b", re.I)
_STRATEGY_HINT = re.compile(
    r"\b(strategy|iron\s+condor|straddle|strangle|spread|recommend\s+trade|"
    r"which\s+option|trade\s+plan)\b", re.I)

def detect_browse_intent(text: str) -> bool:
    return bool(_BROWSE_HINT.search(text or ""))

def detect_index_outlook_intent(text: str) -> bool:
    return bool(_INDEX_OUTLOOK_HINT.search(text or ""))

def detect_stock_trade_intent(text: str) -> bool:
    t = text or ""
    return bool(_STOCK_TRADE_HINT.search(t)) and not detect_options_intent(t)

def classify_widget_intent(text: str) -> WidgetIntent:
    t = text or ""
    if detect_finalize_intent(t):
        return "execute_refresh"
    if detect_browse_intent(t):
        return "none"
    if detect_options_intent(t) or _STRATEGY_HINT.search(t):
        return "options_strategy"
    if detect_stock_trade_intent(t):
        return "stock_trade"
    if detect_index_outlook_intent(t):
        return "index_outlook"
    return "none"
```

- [ ] **Step 4: Run tests**

Run: `cd vibetrading/agent && python -m pytest tests/test_widget_intent.py -v`  
Expected: PASS

- [ ] **Step 5: Commit** (only if user requested)

---

### Task 2: Widget presentability gates

**Files:**
- Create: `integrations/trade_integrations/trade_widgets/presentability.py`
- Test: `tests/test_widget_presentability.py`

**Interfaces:**
- Produces: `is_widget_presentable(widget: dict, intent: str) -> bool`, `presentation_mode_for(widget: dict, intent: str) -> str`
- Consumes: widget dicts from payload builders

- [ ] **Step 1: Write failing tests**

```python
# tests/test_widget_presentability.py
from trade_integrations.trade_widgets.presentability import (
    is_widget_presentable,
    presentation_mode_for,
)

def test_options_not_presentable_without_legs():
    w = {"asset_type": "options", "plan_status": "partial", "ranked_strategies": [], "charges": {}}
    assert not is_widget_presentable(w, "options_strategy")

def test_options_presentable_when_ready():
    w = {
        "asset_type": "options",
        "plan_status": "ready",
        "ranked_strategies": [{"name": "IC"}],
        "strategy_variants": {"iron_condor": {"legs": [{"symbol": "X"}]}},
        "payoff": {"samples": [{"spot": 100, "pnl": 0}]},
        "charges": {"net_debit_credit": 500},
    }
    assert is_widget_presentable(w, "options_strategy")
    assert presentation_mode_for(w, "options_strategy") == "options_strategy"

def test_index_presentable_with_factors():
    w = {"asset_type": "index", "plan_status": "ready", "factor_explanation": {"contributors": [{}]}}
    assert is_widget_presentable(w, "index_outlook")
    assert presentation_mode_for(w, "index_outlook") == "index_outlook"
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `python -m pytest tests/test_widget_presentability.py -v`

- [ ] **Step 3: Implement**

```python
# integrations/trade_integrations/trade_widgets/presentability.py
from __future__ import annotations
from typing import Any

def _has_legs(widget: dict[str, Any]) -> bool:
    rec = widget.get("recommended") or {}
    if rec.get("legs"):
        return True
    for v in (widget.get("strategy_variants") or {}).values():
        if isinstance(v, dict) and v.get("legs"):
            return True
    return False

def is_widget_presentable(widget: dict[str, Any], intent: str) -> bool:
    if not widget or intent == "none":
        return False
    asset = widget.get("asset_type", "options")
    status = widget.get("plan_status", "")

    if intent in ("options_strategy", "execute_refresh") or asset == "options":
        ranked = widget.get("ranked_strategies") or []
        variants = widget.get("strategy_variants") or {}
        charges = widget.get("charges") or {}
        net = charges.get("net_debit_credit")
        has_payoff = bool((widget.get("payoff") or {}).get("samples"))
        if intent == "options_strategy" and not (ranked or variants):
            return False
        if status not in ("ready", "partial"):
            return False
        if not _has_legs(widget) and not ranked:
            return False
        if net is None:
            return False
        return has_payoff or _has_legs(widget)

    if intent == "index_outlook" or asset == "index":
        if status not in ("ready", "partial"):
            return False
        fe = widget.get("factor_explanation") or {}
        return bool(
            fe.get("contributors")
            or widget.get("top_factors")
            or widget.get("scenarios")
        )

    if intent == "stock_trade" or asset == "stock":
        if status != "ready":
            return False
        steps = widget.get("implementation_steps") or []
        rec = widget.get("recommended") or {}
        return bool(steps or rec.get("action") or rec.get("side"))

    return False

def presentation_mode_for(widget: dict[str, Any], intent: str) -> str:
    asset = widget.get("asset_type", "options")
    if asset == "index" or intent == "index_outlook":
        return "index_outlook"
    if asset == "stock" or intent == "stock_trade":
        return "stock_trade"
    return "options_strategy"
```

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit** (if requested)

---

### Task 3: Wire intent gates into hub prefetch

**Files:**
- Modify: `vibetrading/agent/src/trade/hub_bridge.py:531-595`
- Modify: `tests/test_hub_bridge_index_prefetch.py`
- Create: `tests/test_hub_bridge_widget_intent.py`

**Interfaces:**
- Consumes: `classify_widget_intent`, `is_widget_presentable`, `presentation_mode_for`
- Produces: gated `_maybe_emit_*_widget` calls; passes `widget_intent` into context builder

- [ ] **Step 1: Write failing test — no widget on ticker-only message**

```python
def test_prefetch_no_widget_when_intent_none():
    bus = _FakeEventBus()
    artifact = {"ticker": "NIFTY", "plan_status": "ready", "ranked_strategies": [{"name": "IC"}]}
    with (
        patch("src.trade.hub_bridge.extract_primary_ticker", return_value="NIFTY"),
        patch("src.trade.hub_bridge.infer_asset_type", return_value="options"),
        patch("src.trade.hub_bridge.prefetch_hub_plan", return_value=artifact),
        patch("src.trade.hub_bridge._maybe_emit_options_widget") as emit_opt,
        patch.dict(os.environ, {"OPTIONS_AUTO_WIDGET_ON_PREFETCH": "true"}),
    ):
        prefetch_research_for_message("s1", "NIFTY", bus)
    emit_opt.assert_not_called()
```

- [ ] **Step 2: Run — expect FAIL** (currently emits)

- [ ] **Step 3: Change defaults and gate prefetch**

In `hub_bridge.py`:

1. Change `_options_auto_widget_enabled` / `_index_auto_widget_enabled` default env to `"false"`.
2. At start of widget emit block in `prefetch_research_for_message`:

```python
from src.trade.widget_intent import classify_widget_intent
intent = classify_widget_intent(content)
```

3. Options emit only when:
   - `_options_auto_widget_enabled()` AND
   - `intent in ("options_strategy", "execute_refresh")` AND
   - `has_strategy_options_to_present(artifact)` AND
   - built widget passes `is_widget_presentable(widget, intent)`

4. Index emit only when:
   - `_index_auto_widget_enabled()` AND
   - `intent == "index_outlook"` AND
   - `intent != "options_strategy"` path already taken (mutual exclusion)

5. Pass `widget_intent=intent` into `format_research_context_for_agent`.

- [ ] **Step 4: Update existing index prefetch test** — index widget only fires when message has outlook intent.

- [ ] **Step 5: Run all hub_bridge tests**

Run: `python -m pytest tests/test_hub_bridge_index_prefetch.py tests/test_hub_bridge_widget_intent.py vibetrading/agent/tests/test_widget_intent.py -v`

- [ ] **Step 6: Commit** (if requested)

---

### Task 4: Tighten widget guard

**Files:**
- Modify: `vibetrading/agent/src/trade/widget_guard.py`
- Modify: `vibetrading/agent/tests/test_widget_guard.py`

- [ ] **Step 1: Update failing test — guard requires options_strategy intent**

```python
def test_guard_skips_when_intent_none():
    assert not needs_widget_guard(
        "What's NIFTY doing?",
        "Iron condor max loss is limited.",
        set(),
    )
```

- [ ] **Step 2: Implement in `needs_widget_guard`**

Add after keyword check:

```python
from src.trade.widget_intent import classify_widget_intent
intent = classify_widget_intent(user_message)
if intent not in ("options_strategy", "execute_refresh"):
    return False
```

In `maybe_inject_widget`, call `is_widget_presentable(widget, intent)` before emit; persist widget to disk like prefetch path.

- [ ] **Step 3: Run tests**

Run: `cd vibetrading/agent && python -m pytest tests/test_widget_guard.py -v`

- [ ] **Step 4: Commit** (if requested)

---

### Task 5: Intent-aware agent context + payload `presentation_mode`

**Files:**
- Modify: `integrations/trade_integrations/bridge/hub_context.py`
- Modify: `integrations/trade_integrations/dataflows/options_research/widget_payload.py`
- Modify: `integrations/trade_integrations/dataflows/index_research/widget_payload.py`
- Modify: `integrations/trade_integrations/dataflows/stock_research/widget_payload.py`
- Modify: `tests/test_hub_context.py`

- [ ] **Step 1: Extend `format_research_context_for_agent` signature**

```python
def format_research_context_for_agent(
    artifact: dict | None,
    *,
    index_artifact: dict | None = None,
    widget_intent: str = "none",
) -> str:
```

- [ ] **Step 2: Replace unconditional MUST-call widget lines**

Inject block at top:

```python
lines.append(f"[widget_intent: {widget_intent}]")
```

Then branch per spec (none → do not call; index_outlook → index tool only; etc.).

Remove index block line: "MANDATORY: ... MUST call get_index_trade_widget ... not markdown-only" for all cases.

- [ ] **Step 3: Add `presentation_mode` + `widget_intent` to each `build_*_trade_widget_from_doc` return dict**

Use `presentation_mode_for()` from presentability module.

- [ ] **Step 4: Update hub_context tests**

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_hub_context.py -v`

- [ ] **Step 6: Commit** (if requested)

---

### Task 6: Index widget persist + SSE relay

**Files:**
- Modify: `openalgo/mcp/mcpserver.py` (~get_index_trade_widget)
- Modify: `vibetrading/agent/src/api/trade_routes.py:27-36, 45-47`

- [ ] **Step 1: Extend regex and tool names**

```python
_WIDGET_ID_RE = re.compile(r"(?:tp|ts|ti)_[A-Z][A-Z0-9]*_[0-9a-f]{12}")
_WIDGET_TOOL_NAMES = frozenset({
    ...,
    "get_index_trade_widget",
    "mcp_openalgo_get_index_trade_widget",
})
```

- [ ] **Step 2: Persist index widget in MCP handler**

Mirror options handler:

```python
from trade_integrations.trade_widgets.store import persist_trade_widget
widget = build_index_trade_widget(...)
persist_trade_widget(widget)
return {"status": "ok", "widget_id": widget["widget_id"], ...}
```

- [ ] **Step 3: Manual smoke**

Call MCP tool or hit build path; verify `~/.vibe-trading/trade_widgets/ti_NIFTY_*.json` exists.

- [ ] **Step 4: Commit** (if requested)

---

### Task 7: Frontend presentation modes + dedup

**Files:**
- Modify: `vibetrading/frontend/src/lib/api.ts` (~1167)
- Modify: `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx`
- Modify: `vibetrading/frontend/src/pages/Agent.tsx` (SSE handler)

- [ ] **Step 1: Add types**

```typescript
export type WidgetPresentationMode =
  | "options_strategy"
  | "index_outlook"
  | "stock_trade";

export interface TradePlanWidget {
  ...
  presentation_mode?: WidgetPresentationMode;
  widget_intent?: string;
}
```

- [ ] **Step 2: Derive mode in TradePlanWidgetCard**

```typescript
const mode =
  widget.presentation_mode ??
  (widget.asset_type === "index" ? "index_outlook"
    : widget.asset_type === "stock" ? "stock_trade"
    : "options_strategy");
const showPayoff = mode === "options_strategy";
const showCharges = mode === "options_strategy" || mode === "stock_trade";
const showIndexChart = mode === "index_outlook";
```

Wrap charges block: `{showCharges && (...)}`  
Wrap payoff: `{showPayoff && ...}`  
Ensure index chart uses `showIndexChart`.

- [ ] **Step 3: Skip empty cards**

At top of component, if mode is `options_strategy` and no legs/payoff, return `null`.

- [ ] **Step 4: Dedupe in Agent.tsx SSE handler**

```typescript
setLiveItems((prev) => {
  const id = `tw_${widget.widget_id}`;
  if (prev.some((item) => item.kind === "trade_plan_widget" && item.id === id)) return prev;
  return [...prev, { kind: "trade_plan_widget", id, widget, ts: Date.now() }];
});
```

- [ ] **Step 5: Verify in browser** — browse question → no card; strategy question → card with charges.

- [ ] **Step 6: Commit** (if requested)

---

### Task 8: Env defaults + skills

**Files:**
- Modify: `.env.example` (~57)
- Modify: `vibetrading/agent/.env.example` (~170)
- Modify: `stack/vibe/skills/options-advisor/SKILL.md`
- Modify: `stack/vibe/skills/index-advisor/SKILL.md`
- Modify: `stack/vibe/skills/stock-advisor/SKILL.md`

- [ ] **Step 1: Update env comments**

```bash
OPTIONS_AUTO_WIDGET_ON_PREFETCH=false  # opt-in: only when message intent matches
INDEX_AUTO_WIDGET_ON_PREFETCH=false
```

- [ ] **Step 2: Update skills** — document intent-gated widget policy; remove "always emit on ticker" language.

- [ ] **Step 3: Commit** (if requested)

---

### Task 9: End-to-end verification

- [ ] **Step 1: Run unit tests**

Run: `python -m pytest tests/test_widget_presentability.py tests/test_hub_context.py tests/test_hub_bridge_index_prefetch.py vibetrading/agent/tests/test_widget_intent.py vibetrading/agent/tests/test_widget_guard.py -v`  
Expected: all PASS

- [ ] **Step 2: Manual chat scenarios**

| Message | Expected widget |
|---------|-----------------|
| "Show RELIANCE option chain" | None |
| "Where is NIFTY headed?" | Index outlook (factor chart, no charges) |
| "Iron condor on NIFTY" | Options (payoff + charges) |
| "NIFTY" only | None |

- [ ] **Step 3: Confirm no dual widgets on NIFTY outlook question**

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Option A opt-in defaults | Task 3, 8 |
| Intent classifier | Task 1 |
| Presentability gates | Task 2, 3, 4 |
| One widget per turn (index vs options) | Task 3 |
| presentation_mode UI | Task 7 |
| Agent prompt alignment | Task 5 |
| Index persist + SSE | Task 6 |
| Skills + env | Task 8 |

---

**Plan complete.** Saved to `docs/superpowers/plans/2026-07-16-widget-intent-gating.md`.

**Execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — implement tasks in this session with checkpoints

Which approach do you want?
