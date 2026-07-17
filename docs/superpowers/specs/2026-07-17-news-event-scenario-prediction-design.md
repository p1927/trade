# News Event Scenario Prediction — Design Spec

**Date:** 2026-07-17  
**Status:** Implemented (v1) — see [Implementation review](#implementation-review)  
**Goal:** On-demand, conversational **News Predictions** mode on `/prediction` that explores news-driven what-if outcomes for NIFTY using the **frozen Analysis pipeline snapshot**, with quant-backed path widgets and hub persistence.

**Related plan:** `.cursor/plans/news_scenario_agent_761fccdb.plan.md`

---

## Summary

The Prediction tab already runs `run_index_research()` and loads a full `IndexResearchDoc` (constituents, Ridge equation, factor sensitivity, embedded `news_impact`). **News Predictions** is a split-view advisor **downstream** of that artifact:

| Panel | Role |
|-------|------|
| **Left (~45%)** | Embedded Vibe agent (`session_kind: news_scenario_advisor`) — proposes/refines event outcomes in chat |
| **Right (~55%)** | `NewsScenarioCanvas` — multi-path Nifty chart, date range, outcome chips, recent scenarios |

The agent **does not** refresh hub data or re-run the index pipeline. All MCP tools bind to `pipeline_as_of` via `resolve_bound_pipeline_doc()`. Quant uses the same hybrid model as Analysis: frozen bottom-up + simulated macro delta via `simulate_index_prediction()`.

**v1 scope:** NIFTY, single event, on-demand, paper/research only (no execution).

**Not in v1:** multi-news clubbing, auto-run inside `run_index_research()`, live orders, independent data refresh.

---

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Data binding | Session + tools bound to `artifact.as_of` from Analysis tab |
| Quant engine | `simulate_index_prediction()` on frozen T0; no `aggregator.py` changes |
| Agent surface | Dedicated session kind + tool allowlist; blocks execution/autonomous tools |
| Draft vs product | Drafts in hub `drafts/`; quant products in `history/` + `latest.json` |
| Widget delivery | MCP `get_news_scenario_widget` → persist → SSE `trade_plan.widget` → chat + canvas |
| Date range | User/canvas sets range; quant requires complete range; max 90 calendar days |
| Stale Analysis | Banner + restart session; chat/canvas disabled until new session bound |

---

## UX layout

URL: `/prediction?mode=news-scenarios&session={vibe_session_id}`

```
┌─────────────────────────────────────────────────────────────────────────┐
│  [ Analysis ]  [ News Predictions ]   (latter disabled until artifact) │
├──────────────────────────────┬──────────────────────────────────────────┤
│  News scenario advisor       │  NewsScenarioCanvas                       │
│  (embedded Agent.tsx)        │  · date range picker → PATCH session      │
│  · chat + compact widgets    │  · NewsScenarioPathChart                  │
│  · pipeline-bound MCP tools  │  · outcome chips → PATCH selection      │
│                              │  · recent scenarios reload                │
└──────────────────────────────┴──────────────────────────────────────────┘
```

### Stale snapshot UX

When Analysis re-runs and `artifact.as_of` ≠ session `pipeline_as_of`:

1. Amber banner with **Restart session**
2. Chat input disabled (`pipelineStale`)
3. Canvas interactions disabled
4. Bootstrap skipped until user restarts (clears `?session=`, creates new session)

---

## End-to-end wiring

```mermaid
sequenceDiagram
  participant User
  participant PredictionUI as Prediction.tsx
  participant SessionAPI as trade_routes
  participant Agent as Agent SSE
  participant Loop as AgentLoop
  participant MCP as OpenAlgo MCP
  participant Hub as hub artifacts
  participant Canvas as NewsScenarioCanvas

  User->>PredictionUI: Run Analysis
  User->>PredictionUI: News Predictions
  PredictionUI->>SessionAPI: POST /news-scenarios/session
  SessionAPI->>SessionAPI: resolve_bound_pipeline_doc
  User->>Agent: Chat outcomes / date range
  Agent->>Loop: tool call save_news_scenario_draft
  Loop->>Loop: inject pipeline_as_of, session_id
  Loop->>MCP: save draft
  MCP->>Hub: drafts/{draft_id}.json
  Loop->>Loop: sync active_draft_id on session
  User->>Agent: Show paths
  Agent->>MCP: run_news_event_scenario
  MCP->>Hub: history/{scenario_id}.json
  Agent->>MCP: get_news_scenario_widget
  MCP->>MCP: persist_trade_widget ns_*
  MCP-->>Loop: widget JSON
  Loop-->>Agent: SSE trade_plan.widget
  Agent->>Canvas: onNewsScenarioWidget
  Agent->>Agent: TradePlanWidgetCard compact
  User->>Canvas: outcome chip
  Canvas->>SessionAPI: PATCH selected_outcome_id
```

### Binding chain (must not break)

1. `Prediction.tsx` → `POST /index-prediction/news-scenarios/session` with verbatim `artifact.as_of`
2. Session config is SSOT for `pipeline_as_of`, `date_range`, `active_*`, `selected_outcome_id`
3. `AgentLoop._inject_news_scenario_session_context` fills missing `pipeline_as_of` / `session_id` on pipeline tools
4. Every backend entry → `resolve_bound_pipeline_doc()`
5. Hub context: `[news_scenario_context]` + bound `[index_research_context]` (no `prefetch refresh`)

---

## Session config

| Key | Source |
|-----|--------|
| `session_kind` | `"news_scenario_advisor"` |
| `pipeline_as_of` | Analysis `artifact.as_of` |
| `pipeline_ticker` | `"NIFTY"` |
| `horizon_days` | Analysis horizon control |
| `date_range` | Canvas PATCH / agent draft |
| `active_draft_id` | After `save_news_scenario_draft` (loop sync) |
| `active_scenario_id` | After `run_news_event_scenario` (loop sync) |
| `selected_outcome_id` | Canvas PATCH |

### Session lifecycle

| Event | Behavior |
|-------|----------|
| First open News Predictions | POST session → set `?session=` |
| Return same snapshot | Resume session with matching `pipeline_as_of` (scan or URL) |
| Analysis refresh | Stale banner → restart |
| PATCH session | Requires `session_kind == news_scenario_advisor` (403 otherwise) |

---

## Agent architecture

### Skill

`stack/vibe/skills/news-scenario-advisor/SKILL.md` — references `index-advisor` for factor vocabulary.

### Tool allowlist

`vibetrading/agent/src/session/news_scenario_profile.py` — `filter_registry_for_news_scenario()`.

**Allowed:** pipeline read/simulate MCP tools, `load_skill`, `search_india_symbol`, read-only `get_index_trade_plan`.

**Blocked:** execution, mandates, autonomous agents, debate runners, refresh widgets.

### System prompt

`context.py` when `session_kind == news_scenario_advisor` — quant tools mandatory before stating levels; widget on visualize request.

---

## MCP tools (bound snapshot only)

| Tool | Purpose |
|------|---------|
| `get_pipeline_snapshot` | Spot, baseline, regime, `news_item_count` |
| `query_factor_explanation` | Contributors + macro levels |
| `query_factor_sensitivity` | Sensitivity + event impact curves |
| `query_equation_coefficients` | Ridge equation + model artifact |
| `query_constituent_drivers` | Top constituent signals |
| `get_pipeline_news_items` | Embedded headlines filtered by date range |
| `get_playground_context` | Playground context builder |
| `simulate_pipeline_scenario` | Single-shock simulate (`factor_overrides_json`) |
| `save_news_scenario_draft` | Hub draft persist |
| `run_news_event_scenario` | Multi-outcome batch quant |
| `get_news_scenario_widget` | Build + **persist** `ns_*` widget for SSE relay |

---

## Quant rules

### Date range vs horizon

- `simulate_horizon = min(horizon_days, trading_days_in_range)`
- Trading days approximated as `calendar_days * 5/7` (v1; v2 may use `horizon_dates.py`)
- Path x-axis dates interpolated across user `date_range`
- **`run_news_event_scenario` requires complete `date_range`** → else `missing_date_range`
- **Max 90 calendar days** → `date_range_too_wide`

### Outcome → shocks

1. Agent proposes `primary_factor`, `factor_overrides`, `intensity`, optional `event_preset_id`
2. Backend validates against `MACRO_FACTOR_KEYS` ∪ `NEWS_EVENT_MACRO_KEYS`; unknown keys stripped with **warnings**
3. Intensity scale: low 0.5×, medium 1.0×, high 1.5×
4. Fallback: `event.topic_tags` → `calibrated_shock_pct_for_topic` on `primary_factor`

### Hybrid math

```
expected_return_pct = bottom_up_return_pct + macro_delta_pct (+ event overlay when set)
```

Widget baseline includes `equation_ref: { bottom_up, macro_delta, overlay }`.

### Partial failure

Per-outcome simulate wrapped in try/except. Product may include `status: partial`, `errors[]`, baseline-only paths for failed outcomes. Widget `plan_status: partial` when errors present.

---

## Hub storage

```
reports/hub/NIFTY/news_event_scenarios/
  latest.json
  drafts/{draft_id}.json
  history/{scenario_id}.json
  history/{scenario_id}.md
```

Registered in `integrations/trade_integrations/hub_analytics/manifest.py` as `news_event_scenarios`.

---

## REST API

Base: `/trade/index-prediction/news-scenarios` (auth: `require_local_or_auth`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/session` | Create/resume; body: `pipeline_as_of`, `ticker`, `horizon_days`, optional `session_id` |
| PATCH | `/session/{id}` | `date_range`, `selected_outcome_id`, `active_draft_id`, `active_scenario_id` |
| GET | `/recent` | List recent quant products |
| GET | `/{scenario_id}` | Load quant product |

Draft/run/widget remain MCP-first.

---

## Widget payload

```json
{
  "type": "trade_plan.widget",
  "widget_kind": "news_event_scenario",
  "widget_id": "ns_NIFTY_{12hex}",
  "asset_type": "index",
  "underlying": "NIFTY",
  "spot": 24500,
  "date_range": { "start": "2026-08-01", "end": "2026-08-15" },
  "event": { "source": "headline|custom", "title": "..." },
  "baseline": { "expected_return_pct": 0.8, "path": [], "equation_ref": {} },
  "outcomes": [{ "id": "escalation", "label": "...", "path": [], "contributors": [] }],
  "fan_band": { "low": 23800, "high": 25200, "low_path": [], "high_path": [] },
  "selected_outcome_id": "escalation",
  "scenario_id": "..."
}
```

SSE relay: `trade_plan_widget_frame_from_tool_result` whitelists `get_news_scenario_widget`, accepts `ns_*` widget IDs, loads from `~/.vibe-trading/trade_widgets/`.

---

## Edge cases and expected behavior

| Case | Expected behavior | Implementation |
|------|-------------------|----------------|
| No Analysis artifact | News Predictions tab disabled | ✅ `disabled={!hasArtifact}` |
| Stale `pipeline_as_of` | Banner + restart; disable chat/canvas | ✅ normalized compare + restart |
| Empty `news_impact.items` | Custom events OK; UI notice | ✅ canvas notice |
| Missing `date_range` | Agent asks; run returns `missing_date_range` | ✅ |
| Range > 90 days | Refuse quant / PATCH 400 | ✅ `DateRangeTooWideError` |
| Invalid factor keys | Strip + warn in tool/scenario | ✅ warnings on product + outcome |
| All overrides stripped | Fallback on `primary_factor` + topic tags | ✅ (topic_tags bug fixed) |
| Simulate failure (one outcome) | Partial product + error on outcome | ✅ per-outcome try/except |
| Canvas outcome select | PATCH → next turn in `[news_scenario_context]` | ✅ |
| Concurrent tabs | Last draft write wins; history immutable | ✅ file-based |
| Wrong session PATCH | 403 not news scenario session | ✅ |
| Resume same snapshot | Reuse session without URL | ✅ list_sessions scan |
| Widget → canvas | SSE relay + persist | ✅ ns_* + whitelist |
| Dual widget render | Chat card + canvas | ✅ no early return in Agent |
| Draft `pipeline_as_of` mismatch | Reject run | ✅ StaleSnapshotError |
| Calendar range > horizon | Quant caps; agent should warn | ⚠️ cap only (prompt/skill) |
| Server-side stale tool block | Reject if hub as_of ≠ session | ⚠️ relies on StaleSnapshotError at resolve |
| v2 `headline_ids[]` | Stub unused | ❌ deferred |

---

## Verification

| Check | Command |
|-------|---------|
| Snapshot resolver | `pytest tests/test_pipeline_snapshot.py` |
| Scenario quant + date rules | `pytest tests/test_news_event_scenarios.py` |
| Widget shape | `pytest tests/test_news_scenario_widget.py` |
| Widget SSE relay | `pytest tests/test_news_scenario_widget_relay.py` |
| Tool allowlist | `pytest vibetrading/agent/tests/test_news_scenario_profile.py` |
| Hub context | `pytest tests/test_news_scenario_hub_context.py` |
| Agent prompt | `pytest tests/test_news_scenario_context.py` |
| Frontend build | `cd vibetrading/frontend && npm run build` |
| Manual E2E | Analysis → News Predictions → chat → widget on canvas |

---

## Implementation review

**Review date:** 2026-07-17

A full workflow audit against this spec identified gaps in the widget SSE chain, session guards, and plan §11 edge cases. The following were **fixed in the same review pass**:

| Finding | Severity | Fix |
|---------|----------|-----|
| `get_news_scenario_widget` not in SSE whitelist; `ns_*` IDs rejected; no persist | Critical | Whitelist tool, extend ID regex, `persist_trade_widget` in tool handler |
| PATCH any session by ID | Critical | Require `session_kind == news_scenario_advisor` |
| No `pipeline_as_of` injection on MCP tools | Important | `AgentLoop._inject_news_scenario_session_context` |
| `active_draft_id` / `active_scenario_id` never updated | Important | `sync_news_scenario_session_from_tool_result` after draft/run |
| Chat widget skipped in news mode | Important | Removed early return in `Agent.tsx` |
| No session resume by snapshot | Important | `list_sessions` scan on POST |
| Partial simulate / factor warnings | Important | Per-outcome try/except; warnings on product |
| Canvas history unused | Important | `listRecentNewsScenarios` + reload on canvas |
| `simulate_pipeline_scenario` missing overrides param | Minor | `factor_overrides_json` on MCP tool |
| Outcome chips all highlighted when none selected | Minor | Strict `activeOutcomeId` match |
| Draft vs session as_of mismatch | Minor | Validate on run |

**Remaining (acceptable v1 deferrals):**

- Automated horizon > range agent warning (skill/prompt only)
- v2 multi-headline `headline_ids`
- Live E2E with stack running (manual checklist above)

---

## Extension points (v2)

- **Multi-news clubbing:** `event.headline_ids: string[]` + dampened merged overrides
- **Constituent shocks:** `constituent_overrides` → bottom-up recompute
- **Calibrated probabilities:** replace agent `probability_hint` from outcome ledger
- **Trading calendar:** replace 5/7 approximation with `horizon_dates.trading_days_in_range`

---

## Key files

| Area | Path |
|------|------|
| Snapshot gate | `integrations/.../pipeline_snapshot.py` |
| Scenarios | `integrations/.../news_event_scenarios.py` |
| MCP handlers | `integrations/.../news_scenario_tools.py` |
| Widget builder | `integrations/.../news_scenario_widget.py` |
| Session profile | `vibetrading/agent/src/session/news_scenario_profile.py` |
| Session sync | `vibetrading/agent/src/session/news_scenario_session.py` |
| API routes | `vibetrading/agent/src/api/trade_routes.py` |
| Loop injection | `vibetrading/agent/src/agent/loop.py` |
| SSE relay | `vibetrading/agent/src/api/trade_routes.py` (`trade_plan_widget_frame_from_tool_result`) |
| Skill | `stack/vibe/skills/news-scenario-advisor/SKILL.md` |
| UI | `vibetrading/frontend/src/pages/Prediction.tsx`, `NewsScenarioCanvas.tsx` |
