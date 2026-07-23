# Repo Review — Pending Product / Design Decisions

Deferred from [2026-07-19-repo-review-log.md](2026-07-19-repo-review-log.md).  
Straightforward fixes are tracked in the fix plan; **do not implement here without your call.**

---

## Multi-agent execution

| ID | Problem | Options | Recommendation |
|----|---------|---------|----------------|
| R2-02 | Empty-leg EXIT calls account-wide `close_all` | A) Require non-empty legs always B) Scope close by strategy tag C) One agent per OpenAlgo account | B for paper multi-agent |
| R2-04 | P&L/stops use whole OpenAlgo book | A) Filter all metrics by agent underlying/strategy B) Separate paper accounts per agent | A first |
| R2-05 | Intent queue races (no locking) | A) `fcntl` flock on queue dir B) Redis claim C) Single processor only | B if Redis already required for Nautilus |
| R7-05 | Global autonomous_agents session pointer | A) Per-agent session files B) Drop legacy autonomous_agents for bridge agents | B |

**Blocks:** Multi-agent live autonomy, concurrent agents on one broker login.

---

## Execution authority

| ID | Problem | Options | Recommendation |
|----|---------|---------|----------------|
| R12-01 | Raw `place_basket_order` MCP unguarded | A) Remove tool B) Hard-fail when bridge agent active C) Prompt-only (status quo) | B |
| R12-02 | `/trade/execute-basket` direct REST | A) Block for autonomous session kinds B) Route through bridge C) Keep for interactive only | C + B for `session_kind=autonomous_agent` |
| R12-05 | Dual ENTER (`autonomous_agents_direct` vs bridge) | A) Deprecate direct path for IN B) Document only | A for India autonomous |

**Blocks:** Fail-closed autonomous execution policy.

---

## US / market expansion

| ID | Problem | Options | Recommendation |
|----|---------|---------|----------------|
| R4-01 | US tickers get India NSE execution artifacts in stock research | A) Block US in stock eligibility B) US-specific execution payload C) Separate US hub kind | A until US path exists |
| R4-03–R4-05 | India lacks earnings_signal; stock markdown/payoff gaps | A) Add India sources B) Defer stock advisor parity | Product priority call |
| FINNIFTY/MIDCPNIFTY | Debate yfinance symbol unknown | A) Research correct symbols B) Keep ^NSEI proxy C) Skip debate for non-NIFTY indices | Research needed |

**Blocks:** US stock advisor, multi-index debate quality.

---

## News semantics

| ID | Problem | Options | Recommendation |
|----|---------|---------|----------------|
| R8-02 | Code bypasses `news_hub_bridge` facade | A) Enforce import lint B) Gradual reroute | B |
| R8-03 | Staging pending in `query_verified_news` | A) Exclude pending from verified query B) Rename API C) Keep for UI preview | Split APIs: `query_verified` vs `query_with_staging` |

**Blocks:** Strict verified-news contract for prediction.

---

## Ingest / data contracts

| ID | Problem | Options | Recommendation |
|----|---------|---------|----------------|
| R9-02 | GitHub valuation overwrite without merge | A) Always merge with local B) GitHub as gap-fill only in full curated run | Match github macro pattern |
| R9-03 | Cache skip by file existence only | A) sha256 in manifest B) mtime TTL | A |
| R9-05 | `save_history_dataset` full-replace | A) Require caller merge B) Built-in merge in store | B |
| R9-07 | Ingest on every NSE browser read | A) Explicit refresh flag B) Background job only | A |
| R5-07 | Constituent sync overwrites without priority | A) Use `merge_with_priority` | A |
| R1-10 | Legacy `.stack.pids` vs claims | A) Remove start.sh pid file B) Migrate | A |
| R1-15 | Dev mode leaves Nautilus watch running | A) Stop watch on dev entry B) Document coexistence | Your preference |

**Blocks:** Safe re-ingest automation, CI data refresh.

---

## Risk / persistence

| ID | Problem | Options | Recommendation |
|----|---------|---------|----------------|
| R2-06 | Paper EXIT bypasses exit-window | A) Enforce always B) Keep analyzer bypass C) Env opt-in | C with default enforce |
| R2-10 | INR limit on USD P&L | A) Currency-aware limits B) Separate US agents | A |
| R2-15 | Halt state in-memory only | A) Redis B) Hub JSON C) Accept for paper | B for paper |
| R8-07 | Parquet RMW without file lock | A) flock B) Single writer process | A |

**Blocks:** Live trading, restart-safe risk gates.

---

## Architecture / performance

| ID | Problem | Options | Recommendation |
|----|---------|---------|----------------|
| R3-02 | Prefetch all 4 pipelines on every propagate | A) Eligibility-gated prefetch B) Cache freshness short-circuit | A |
| R3-03–R3-07 | Debate required vs tools; side-effect status; monkey-patches | Per-item design review | — |
| R12-04 | Watch feed bypasses hub channel | A) Wire `get_multi_quotes` + WATCH policy B) Keep direct REST | A for unified cache |
| R10-04 | Full pytest suite slow/hangs | A) `pytest-timeout` B) Mark integration tests C) Split CI jobs | A + B |

**Blocks:** CI reliability, debate cost control.

---

## Product UX

| ID | Problem | Options | Recommendation |
|----|---------|---------|----------------|
| R11-04 | Charges stale after strike drag | A) Re-fetch `/trade/charges` on leg change B) Client-side estimate | A |
| M033 | Scheduler enables research but prompt says disabled | A) Align prompt B) Disable scheduler by default | A |
| R7-04 | Generic watch_spec before strategy rules | A) Defer handoff until bootstrap completes B) Accept generic early watch | A |
| R4-04 | Stock `payoff_over_time` empty | A) Port from options ranker B) Defer | North-star stock phase |

**Blocks:** Stock advisor parity, autonomous UX polish.

---

## Decided — 2026-07-20 (quality-first + no-bloat ingest)

### Ingest policy (user directive)

| Rule | Decision |
|------|----------|
| Expensive batch ingest | **Only on explicit user/script request** (`explicit=True` or `TRADE_ALLOW_BATCH_INGEST=1`) |
| Real-time data | **Always merge** with existing cold tier on dedupe keys — no full-replace bloat |
| Read/query paths | **No ingest-on-read** side effects |

### Implementation status

See plan [`pending_decisions_implementation_2f127018.plan.md`](../../.cursor/plans/pending_decisions_implementation_2f127018.plan.md).

Key decisions recorded: fail-closed execution (R12-*), multi-agent scoping (R2-*), verified-only `query_verified_news` (R8-03), defer handoff until plan approval (R7-04), US autonomous gate + FINNIFTY debate block, mandatory rich research (no `tools_only`), watch hot path direct multiquote + async tick capture.

**Phases 0–5 complete 2026-07-20** — see [`.cursor/plans/pending_decisions_implementation_2f127018.plan.md`](../../.cursor/plans/pending_decisions_implementation_2f127018.plan.md).

**Phases 6–10 complete 2026-07-20** — R11-04 charges refetch, M033 scheduler/prompt alignment, R1-15 idempotent Nautilus in dev, R1-10 `.stack.pids` removed, R9-07 NSE ingest gated + `nse-macro-refresh` job, R8-02 news facade lint, R4-04 stock `payoff_over_time`, FINNIFTY/MIDCPNIFTY yfinance map, parallel bootstrap prefetch, `execution_market` on hub docs, nightly CI extended.

**Phases 11–17 complete 2026-07-20** — orchestrator closure, hub health + widget dedup, EXIT ledger + poll_loop v1 doc, prediction P0/5–7 + PCR gate, auto combiner + hub news freshness, SB equity loader + US path + NIFTYIT debate, parquet flock + data contract hygiene.

**Deferred:** LightGBM full track (Phase G heavy) — backlog unless quick wrapper exists.

---

## How to use this doc

1. ~~Reply with decisions per theme~~ — **Done 2026-07-20**
2. Move decided items into a new implementation plan or master-todo.
3. Keep undecided rows here until resolved.
