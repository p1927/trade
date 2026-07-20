# Repository Code Review Log — 2026-07-19

Segmented full-repository review per [plan](../../.cursor/plans/segmented_repo_code_review_522e3342.plan.md).

Issue IDs: `R{segment}-{n}` (e.g. R1-01).

---

## Segment 1: Stack orchestration and ops

**Scope:** `trade`, `start.sh`, `scripts/stack_*`, `stack/`, `exposure/`, `Makefile`, `docker-compose.stack.yml`

### Strengths

- Single lifecycle CLI (`trade`) delegates cleanly to internal scripts.
- PID claim system with reconciliation, port adoption, and dev/daemon mutual exclusion is well-designed.
- Port registry SSOT (`stack/ports.yaml` → `sync_stack_ports.py`) with compose interpolation.
- Hub Docker resilience (Timescale stale PID repair, WAL wait, grace period).
- Exposure pipeline updates `HOST_SERVER` and verifies tunnel reachability.

### Issues

#### Critical

| ID | File | Issue | Fix hint |
|----|------|-------|----------|
| R1-01 | `scripts/stack_lib.sh:658-681` | Preflight skip uses HTTP on Vibe API `/` (404) but status uses `/health` (200) — skip fails on healthy stack, then strict listener check flags own processes | Use `/health` in preflight skip; allow claimed/in-repo PIDs in listener check |
| R1-02 | `integrations/trade_integrations/stack_ports.py:132-178` | `--check-listeners` flags own OpenAlgo/Vibe PIDs as foreign | Integrate claim files or repo-root process allowlist |

#### Important

| ID | File | Issue | Fix hint |
|----|------|-------|----------|
| R1-03 | `scripts/stack_lib.sh:823-825` | Heal leaves degraded Vibe API running (returns 0 on failed readiness) | Return non-zero or restart on probe failure |
| R1-04 | `scripts/stack_lib.sh:1154-1160` | Nautilus registry summary heredoc fed to `stack_pick_python` not Python | Fix to `"$py" - "$registry_file" <<'PY'` pattern |
| R1-05 | `scripts/stack_lib.sh:314-325` | `stack_process_in_trade_repo` matches any `app.py`/`/vite` globally | Require repo root in args or cwd |
| R1-06 | `scripts/stack_ctl.sh:79-81` | `restart --force` bypasses dev-mode guard | Refuse or require `--kill-dev` when dev active |
| R1-07 | `exposure/lib/common.sh:217-224` | `pkill -x cloudflared` kills all machine tunnels | Stop only PIDs from `.exposure.pids` |
| R1-08 | `exposure/lib/common.sh:27-33` | Tunnel port not tied to `stack/ports.yaml` synced ports | Source `OPENALGO_HOST` from `.stack.ports.env` |
| R1-09 | `trade:290` | `sync-ports` hard-requires `.venv/bin/python` | Fallback to `python3` |
| R1-10 | `start.sh` | Legacy `.stack.pids` coexists with claim system | Route through claims only |

#### Minor

| ID | File | Issue |
|----|------|-------|
| R1-11 | `scripts/stack_lib.sh:676` vs `1108` | Inconsistent Vibe health URL (`/` vs `/health`) |
| R1-12 | `exposure/lib/common.sh:119-124` | `record_pid` truncates PID file each call |
| R1-13 | `docker-compose.stack.yml:33` | Hardcoded DB password `tradehub` (dev-only OK) |
| R1-14 | `Makefile:33` | `make reload` bypasses `trade reload` |
| R1-15 | `scripts/stack_dev.sh` | Does not stop Nautilus watch on dev entry |

### Test run evidence

- **No dedicated stack lifecycle tests** in `tests/` (0 files matching `test*stack*`).
- `pytest tests/test_setup_vibe.py -q` — 2 passed (agent.json only).
- `python3 scripts/sync_stack_ports.py --check` — OK.
- `python3 scripts/sync_stack_ports.py --check-listeners` — flags own stack PIDs when running.
- `bash -n` on stack/exposure scripts — pass.

### Assessment

**With fixes** — architecture is strong; Critical R1-01/R1-02 block reliable `trade up`/`doctor` on healthy stack.

### Cross-segment dependencies

- Nautilus watch status bug (R1-04) affects autonomous hub health display.
- Broken API heal (R1-03) affects scheduler/autonomous ticks.
- Exposure port mismatch (R1-08) breaks webhooks when ports remapped.

---

## Segment 2: Nautilus ↔ OpenAlgo bridge

**Scope:** `integrations/nautilus_openalgo_bridge/` (36 modules). Merged master-todo M005–M022.

### Strengths

- Clean execution authority: `execute.py` sole OpenAlgo path; actor split (WatchActor → BridgeSignalActor → intent queue).
- M005/M006 verified fixed (config parse, underlying filter on legs).
- Per-agent RiskActor; Nautilus-venv-safe hub JSON fallbacks.

### Issues

#### Critical

| ID | File | Issue |
|----|------|-------|
| R2-01 | `signal_actions.py:31-42` | Sync `execute_intent` on Nautilus actor thread (M013) |
| R2-02 | `execute.py:86-95` | Empty-leg EXIT uses account-wide `close_all` — multi-agent risk |
| R2-03 | `intent_queue.py:72-74` | Halted intents block queue head forever (M008) |
| R2-04 | `reconcile.py`, `watch_actor.py`, `risk_actor.py` | Book-level P&L/stops not agent-scoped (M007 partial) |
| R2-05 | `intent_queue.py` + poll paths | No file locking — double-execute race |

#### Important

| ID | File | Issue |
|----|------|-------|
| R2-06 | `preflight.py:53-59` | Paper EXIT bypasses exit-window (M018) |
| R2-07 | `reconcile.py:108-113` | EXIT claims handoff cleared without verify (M019) |
| R2-08 | `vibe_trigger.py:233-266` | US exit missing running/plan guards (M021) |
| R2-09 | `execute.py:113-119` | Pre-exit unrealized P&L in ledger (M009) |
| R2-10 | `risk_actor.py:81-82` | INR limit on USD P&L (M014) |
| R2-11 | `watch_actor.py:221` | Spot baselines frozen at first tick (M015) |
| R2-12 | `watch_actor.py:96-97` | Flatten timer hour borrow bug (M020) |
| R2-13 | `handoff.py:165-178` | `sync_watch_spec_to_handoff` needs store import (M011) |
| R2-14 | `watch_eval.py:77-98` | OI/volume rules lack baselines (M010) |
| R2-15 | `risk_state.py` | Halt state in-memory only (M012 pending) |

#### Minor

| ID | File | Issue |
|----|------|-------|
| R2-16 | `handoff.py:227-231` | Misleading `enqueue_intent` docstring (M022) |
| R2-17 | `tests/test_nautilus_channel_feed.py` | Stale monkeypatch — 1 failing test |

### Test run evidence

- `pytest tests/test_nautilus_* -q` — **65 passed, 2 skipped, 1 failed** (`test_nautilus_channel_feed`).

### Assessment

**With fixes** — sound for single-agent paper; not ready for multi-agent/live until R2-01–R2-05 addressed.

### Cross-segment dependencies

- `execution/bridge_intent.py` (M031), `autonomous_agents/store.py` (M011/M024), Vibe trigger, outcome ledger.

---

## Segment 3: Integration core and TradingAgents wiring

**Scope:** `register.py`, `env.py`, `stack_ports.py`, `context/`, `bridge/`, `research/`, `tools/`, `agents/`, `clients/`

### Strengths

- Guarded patch entrypoint (`TRADE_INTEGRATIONS_SKIP_APPLY`, `_APPLIED`).
- Hub SSOT via `context/hub.py`; research registry contracts; debate synthesis tested.

### Issues

#### Critical

| ID | File | Issue |
|----|------|-------|
| R3-01 | `bridge/agent_debate.py:14-22` | BANKNIFTY mapped to `^NSEI` instead of `^NSEBANK` — wrong debate data |

#### Important

| ID | File | Issue |
|----|------|-------|
| R3-02 | `register.py:176-203` | Every propagate prefetches 4 pipelines; success not verified |
| R3-03 | `research/registry.py` vs tools | Debate required for execute but optional for TradingAgents tools |
| R3-04 | `research/orchestrator.py:327-332` | `get_research_status()` side-effectful (can run pipelines) |
| R3-05 | `register.py` | Global irreversible monkey-patches — test pollution risk |
| R3-06 | `research/debate_synthesis.py` | Fragile price parsing; index view ignores debate direction |
| R3-07 | `register.py:29-85` | Mutates shared `DEFAULT_CONFIG` in place |

#### Minor

| ID | File | Issue |
|----|------|-------|
| R3-08 | `register.py:243-244` | yfinance hub ingest errors swallowed |
| R3-09 | `clients/tapetide.py` | Always enabled; not thread-safe caches |
| R3-10 | `env.py:78-117` | `ensure_vibe_stack_heal()` can block 180s |

### Test run evidence

- Debate synthesis, hub context, orchestrator/registry well tested; **no tests** for index ticker mapping, `env.py`, `stack_ports.py`.

### Assessment

**With fixes** — fix R3-01 before multi-index debate; architecture sound.

---

## Segment 4: Company / options / stock research

**Scope:** `company_research/`, `options_research/`, `stock_research/`, `broker_charges/`, `symbol_registry/`

### Strengths

- Options pipeline matches north-star (ranked strategies, charges, payoff, implementation steps).
- Staged aggregator + hub round-trip; India options fail-closed for US.

### Issues

#### Critical

| ID | File | Issue |
|----|------|-------|
| R4-01 | `stock_research/aggregator.py` + eligibility | US tickers get India NSE execution artifacts |
| R4-02 | `stock_research/aggregator.py:60-68` | Missing spot → empty ranked/recommended/charges block |

#### Important

| ID | File | Issue |
|----|------|-------|
| R4-03 | `company_research` | India never populates `earnings_signal`/`corp_events` — weak IN event scoring |
| R4-04 | `stock_research` | `payoff_over_time` never populated |
| R4-05 | `stock_research/format.py` | Markdown thinner than options (no charges/scenarios) |
| R4-06 | `stock_research/aggregator.py:262-264` | BSE hardcoded to NSE exchange |
| R4-07 | `options_research/aggregator.py` | Silent exception swallowing on spot/ledger |
| R4-08 | `company_research/aggregator.py:196` | Batch omits macro vs single-ticker default |

### Test run evidence

- Segment-focused: **43 passed** (`test_options_*`, `test_company_*`, `test_stock_*`, `test_broker_charges*`, `test_symbol_registry*`).

### Assessment

**With fixes** — options B+; stock C1/C2 block US safety and incomplete artifacts.

---

## Segment 5: Index research core (non-prediction)

**Scope:** `index_research/` excluding `prediction_algorithms/`

### Strengths

- Layered cold-tier → panel → enrichment → aggregator; news SSOT discipline; macro forecast parity tested.

### Issues

#### Critical

| ID | File | Issue |
|----|------|-------|
| R5-01 | `panel_enrichment.py:19-32` | PE proxy uses terminal close — look-ahead bias |
| R5-02 | `news_impact_engine.py` + `news_shock_calibration.py` | Calibrated shock unit mismatch (return % vs factor %) |

#### Important

| ID | File | Issue |
|----|------|-------|
| R5-03 | `aggregator.py:263-264` | `momentum_force` always True — breaks cached runs |
| R5-04 | `aggregator.py:437-440` | Partial prediction object when spot missing |
| R5-05 | `factor_catalog.py` vs `factor_matrix.py` | Catalog/matrix drift — CI failure |
| R5-06 | `panel_enrichment.py:198-208` | Global VIX aliased to `india_vix` |
| R5-07 | `history_ingest.py` | Constituent sync overwrites without priority merge |
| R5-08 | — | No tests for history ingest, panel enrichment, shock calibration |

### Test run evidence

- **40 passed, 2 failed** (targeted subset: aggregator, attribution, macro parity, news impact).
- **Full `test_index_*` run (110 tests, ~56 min): 105 passed, 5 failed:**
  - `test_index_pipeline_log::test_factor_catalog_covers_matrix_keys` — catalog missing `us_10y_velocity_3d` from matrix
  - `test_index_factor_backfill::test_backfill_writes_technical_and_calendar_factors` — mock missing `start=` kwarg
  - `test_index_day_attribution::test_build_nifty_price_series_returns_rows` — needs local history data
  - `test_index_day_attribution::test_explain_nifty_day_for_known_date` — same
  - `test_index_self_learning::test_self_learning_loop_reconcile_metrics_trigger_retrain` — reconcile/retrain path

### Assessment

**With fixes** — B−; fix C1/C2 before trusting backtests/calibration.

---

## Segment 6: Prediction algorithms

**Scope:** `prediction_algorithms/` (tracks, combiners, evaluator, promotion, api)

### Strengths

- Walk-forward causal guards (`before_date`); conservative promotion gates; scoreboard cache invalidation.

### Issues

#### Important

| ID | File | Issue |
|----|------|-------|
| R6-01 | `vibetrading/.../trade_routes.py:1330` | Forecast-lab API skips scoreboard runtime kwargs — live ≠ backtest |
| R6-02 | `walk_forward.py:221-229` | Early-window combiner weights degenerate |
| R6-03 | `promotion.py` | Weight-stability naming overstated; baseline metric mixing |
| R6-04 | `event_overlay` + calibration | Potential calibration leakage (cross-segment) |

#### Minor

| ID | File | Issue |
|----|------|-------|
| R6-05 | `api.py:44` | Unnecessary hub I/O in tracks_only mode |
| R6-06 | `registry.py:47-50` | Unknown track IDs silently skipped |

### Test run evidence

- **40 passed** (`test_prediction_*`, `test_track_*`, `test_scoreboard_*`).

### Assessment

**With fixes** — good foundation; wire API combine path to `resolve_combiner_runtime_kwargs`.

---

## Segment 7: Autonomous agents, execution, monitor, auto_paper

**Scope:** `autonomous_agents/`, `execution/`, `monitor/`, `auto_paper/`, `trade_widgets/`. Merged M023–M033.

### Strengths

- Propose/commit consent model, O_EXCL commit lock, orchestrator lifecycle, execution profile routing.

### Issues

#### Critical

| ID | File | Issue |
|----|------|-------|
| R7-01 | `proposals.py:598-625` | Commit not atomic — orphan agent on crash |
| R7-02 | `store.py:219-239` | Commit lock no stale recovery |

#### Important

| ID | File | Issue |
|----|------|-------|
| R7-03 | M023–M033 | Session private API, timestamp rollback, revision debounce, prompt placeholders, bridge intent bugs, scheduler/prompt mismatch |
| R7-04 | `infra_startup.py` | Early generic watch_spec before strategy rules |
| R7-05 | `auto_paper/session_store.py` | Multi-agent session pointer collision |

### Test run evidence

- **34 passed** (autonomous/orchestrator/commit suite).

### Assessment

**With fixes** — structurally sound v1 paper loop; R7-01/R7-02 + M031/M032 block production hardening.

---

## Segment 8: Hub storage, capture, analytics, news SSOT

**Scope:** `hub_storage/`, `hub_capture/`, `hub_analytics/`, `news_hub_bridge/`, `news_aggregator/`

### Strengths

- `events.parquet` SSOT; bridge façade; staging pipeline; DuckDB read analytics with keyword guard.

### Issues

#### Important

| ID | File | Issue |
|----|------|-------|
| R8-01 | `news_hub_bridge/_ingest.py:234-243` | RSS ingest skips ticker normalization |
| R8-02 | Multiple index_research imports | Bypass `news_hub_bridge` facade |
| R8-03 | `query_verified_news` | Staging pending rows in "verified" query |
| R8-04 | `news_aggregator` | Bridge ingest failures at debug level |
| R8-05 | `hub_analytics/duckdb_views.py` | `read_parquet()` not blocked in SQL guard |
| R8-06 | `parquet_io.py:11-23` | Silent corrupt parquet → empty frame |
| R8-07 | `news_events_store.py` | Read-modify-write without file lock |
| R8-08 | `tests/test_news_hub_bridge.py` | Pipeline pause test fails (rule-fallback default drift) |

### Test run evidence

- **33 passed, 1 failed** (pause test).

### Assessment

**With fixes** — B+ architecture; fix RSS ticker + test drift + facade bypasses.

---

## Segment 9: Data ingest and external sources

**Scope:** `github_datasets/`, `external_financial_datasets/`, `nifty100_financial_intel/`, `nse_browser/`, shared `dataflows/*.py`

### Strengths

- Tiered raw→hub→cold; `throttled_http`; NSE browser layered fallbacks; parser hardening for FII/DII.

### Issues

#### Critical

| ID | File | Issue |
|----|------|-------|
| R9-01 | HF/nifty100 fetches | Raw `requests` bypasses throttling |
| R9-02 | `curated_ingest.py:284-286` | GitHub valuation overwrites without merge |
| R9-03 | `github_datasets/fetch.py` | Cache skip by existence only — stale data |
| R9-04 | `nse_browser/http_bridge.py:58-62` | Binary corruption via UTF-8 round-trip |
| R9-05 | `history_store.py:105-118` | Full-replace can lose data on partial frames |

#### Important

| ID | File | Issue |
|----|------|-------|
| R9-06 | `throttled_http.py` | Global pacing — not parallel-safe |
| R9-07 | `nse_browser/orchestrator.py:113` | Ingest on every read query |
| R9-08 | — | No tests for github/external/nifty100/throttled_http |

### Test run evidence

- `tests/test_nse_browser_parsers.py` — **10 passed**; ingest paths largely untested.

### Assessment

**With fixes** — B−; unify HTTP layer and merge-aware cold-tier writes.

---

## Segment 10: Test suite quality audit

**Scope:** `tests/` (187 files), `tests/conftest.py`, `tradingagents/tests/` (pytest-included)

### Strengths

- Hub isolation via `TRADE_STACK_HUB_DIR` autouse fixture.
- Strong coverage for autonomous loop, nautilus bridge core, debate synthesis, news SSOT.
- `tradingagents/tests`: **559 passed, 2 skipped** (~94s).

### Issues

#### Important

| ID | Issue |
|----|-------|
| R10-01 | **No stack lifecycle tests** (segment 1) |
| R10-02 | **Zero coverage:** `hub_analytics` (partial), `auto_paper` engine, `github_datasets`, `external_financial`, `history_ingest`, `panel_enrichment`, `throttled_http` |
| R10-03 | **7 confirmed failing tests** (this session): `test_nautilus_channel_feed`, `test_openalgo_adapter::test_unmapped_index_raises`, `test_company_research_fundamentals_filings_macro::test_dedupes_filings`, `test_company_research_fundamentals_filings_macro::test_yfinance_vix_and_nifty` (network/nselib), `test_index_pipeline_log::test_factor_catalog_covers_matrix_keys`, `test_index_factor_backfill`, `test_news_hub_bridge::test_ingest_reports_pipeline_paused_without_minimax` |
| R10-04 | Full `tests/` suite **slow/hangs** on integration tests (~787 tests, >25min partial run) — needs per-test timeouts |
| R10-05 | `trade_integrations` auto-applied on import — tests must use skip flag or accept global patches |

### Test run evidence

- **Collected:** 1346 tests (`tests/` + `tradingagents/tests`).
- **tradingagents/tests:** 559 passed, 2 skipped.
- **tests/ (partial):** failures observed through ~45% run; first failure on `-x`: `test_company_research_fundamentals_filings_macro::test_dedupes_filings`.

### Assessment

**With fixes** — broad coverage on product paths; data-plane and ops layers undertested; 6+ known drift failures.

---

## Segment 11: Vibetrading trade surfaces

**Scope:** `vibetrading/agent/src/api/trade_routes.py`, autonomous UI, MCP wiring

### Strengths

- Widget SSE relay pipeline; execution safety (`OPENALGO_PAPER_MODE`); session-kind separation; charges/P&L wired for options.

### Issues

#### Critical

| ID | File | Issue |
|----|------|-------|
| R11-01 | `frontend/src/hooks/useSSE.ts:82-95` | Autonomous SSE events not subscribed — handlers never fire |
| R11-02 | `autonomous_routes.py` | No auth on commit/pause/stop/clear-all |

#### Important

| ID | File | Issue |
|----|------|-------|
| R11-03 | `TradePlanWidgetCard.tsx` | Execute visible before plan approval in autonomous mode |
| R11-04 | `TradePlanWidgetCard.tsx` | Charges stale after drag-adjusted strikes |
| R11-05 | — | No tests for `/trade/execute-basket` |
| R11-06 | `trade_routes.py` vs `trade_widgets/store.py` | Duplicate widget loaders |

### Test run evidence

- **39 passed** (relay, orchestrator, widget guard, plan context).

### Assessment

**With fixes** — good dev loopback; R11-01/R11-02 block remote/autonomous UX reliability.

---

## Segment 12: OpenAlgo + TradingAgents touchpoints

**Scope:** OpenAlgo MCP, `register.py` patches, `ed-alpha/` optional

### Strengths

- OpenAlgo REST as sole broker session; MCP skips double-patch; bridge preflight/reconcile; clean submodule trees.

### Issues

#### Critical

| ID | File | Issue |
|----|------|-------|
| R12-01 | `openalgo/mcp/mcpserver.py:235-263` | `place_basket_order` unguarded — bypasses bridge/mandate |
| R12-02 | `trade_routes.py:217-258` | `/trade/execute-basket` direct OpenAlgo REST |

#### Important

| ID | File | Issue |
|----|------|-------|
| R12-03 | `openalgo/symbols.py` vs `tradingagents/interface.py` | Duplicate `NoMarketDataError` classes — test/runtime drift |
| R12-04 | `data_feed.py` vs channel design | Watch feed bypasses hub channel (R2-17 test) |
| R12-05 | Dual ENTER paths | Bridge vs `auto_paper_direct` OpenAlgo |
| R12-06 | — | No integration tests for MCP vs bridge routing |

### Test run evidence

- **81 passed, 2 failed, 2 skipped** (`test_nautilus_*` + `test_openalgo_*`).

### Assessment

**With fixes** — architecture correct at REST layer; MCP tool boundary not fail-closed.

---

## Final rollup

### Priority fix backlog (deduped, severity-ordered)

**P0 — Correctness / blocks daily ops**

1. R1-01/R1-02 — preflight fails on healthy stack
2. R3-01 — BANKNIFTY wrong yfinance symbol in debate
3. R5-01/R5-02 — look-ahead PE proxy + shock unit mismatch
4. R10-03 — fix 6 known failing tests (drift)

**P1 — Autonomous loop / execution authority**

5. R2-01–R2-05 — bridge queue/thread/book scoping
6. R7-01/R7-02 — commit atomicity + lock recovery
7. R12-01/R12-02 — unguarded MCP/REST order paths
8. R11-01/R11-02 — autonomous SSE + route auth

**P2 — Product completeness (north-star gaps)**

9. R4-01/R4-02 — stock US safety + incomplete artifacts
10. R11-03/R11-04 — autonomous execute gate + charges on drag
11. R6-01 — forecast-lab API combiner parity
12. R8-01/R8-03 — news RSS ticker + staging-in-verified semantics

**P3 — Data plane / ingest reliability**

13. R9-01–R9-05 — HTTP throttling, merge-aware writes, cache validation
14. R5-05 — factor catalog/matrix sync
15. R8-06/R8-07 — parquet error surfacing + locking

**P4 — Maintainability / test gaps**

16. R10-01/R10-02 — stack tests + data-plane coverage
17. R3-05/R10-05 — monkey-patch test isolation
18. R1-04 — Nautilus status display bug

### Segment assessments summary

| Segment | Assessment |
|---------|------------|
| 1 Stack ops | With fixes |
| 2 Bridge | With fixes |
| 3 Integration core | With fixes |
| 4 Research kinds | With fixes |
| 5 Index core | With fixes |
| 6 Prediction algos | With fixes |
| 7 Autonomous | With fixes |
| 8 Hub/news | With fixes |
| 9 Ingest | With fixes |
| 10 Tests | With fixes |
| 11 Vibe UI | With fixes |
| 12 Submodules | With fixes |

### Cross-cutting themes

1. **Execution authority split** — designed (OpenAlgo wins) but not enforced at MCP/REST boundaries (R12-01, R11-03, R2-02).
2. **Account-wide vs agent-scoped state** — reconcile, risk, EXIT flatten (R2-04, R2-02).
3. **Hub SSOT erosion** — direct store imports bypass bridge (R8-02).
4. **Test drift after refactors** — channel feed, NoMarketDataError, filings mock, news pause gate (R10-03).
5. **Data integrity for predictions** — look-ahead, shock units, catalog drift (R5-01/R5-02/R5-05).
6. **No stack lifecycle tests** — ops regressions undetected (R1-01 discovered by review, not CI).

### Recommended next actions

1. Fix P0 items in focused commits (one issue per commit per master-todo discipline).
2. Add regression tests for R1-01 (preflight `/health`), R3-01 (index ticker map), R11-01 (SSE types).
3. Feed open master-todo M007–M033 into Fixer track — already reconciled in Segments 2 and 7.
4. Optional: add `pytest-timeout` for full suite CI reliability (R10-04).

---
