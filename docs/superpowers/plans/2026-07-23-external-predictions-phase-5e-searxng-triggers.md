# Phase 5E — SearXNG Fallback Trigger Closure

**Type:** implement  
**Depends on:** Phase 5A (bot-block fallback shipped)  
**Supersedes:** none  
**Out of scope:** New data sources, UI redesign

## Goal

Close **R4** and **R5** from Phase 5 convergence — SearXNG text fallback must run whenever Crawl4AI cannot yield an extractable forecast, not only when every row is Akamai-blocked.

## Carried-forward findings (no deferral)

| ID | Was | Root cause |
|----|-----|------------|
| **R4** | HYPOTHESIS | `should_try_searxng` only when `pick_best_crawl_result` is `None` **and** bot-block heuristics match; successful crawls with weak/no NIFTY signal skip fallback |
| **R5** | HYPOTHESIS | `crawl_rows_all_bot_blocked` requires 100% of failure rows to match bot regex; timeout + Akamai mix skips fallback |

## Design

### Trigger matrix (after `pick_best is None`)

Run `extract_via_searxng_fallback` when **any** of:

1. `crawl_rows_all_bot_blocked(rows)` — existing
2. `is_bot_block_error(message)` on first error — existing
3. **New:** `crawl_rows_have_usable_text(rows)` — at least one row has `success` + markdown ≥ N chars but was rejected by `pick_best` (no NIFTY forecast signal / score below threshold)
4. **New:** `crawl_rows_any_bot_blocked(rows)` — **any** failure row is bot-block (relaxes R5; still requires `pick_best is None`)

Do **not** run fallback when `rows` is empty and URLs were never crawled (keep existing "no landing URLs" path).

### Provenance

- Fallback records keep `navigation_mode: searxng_fallback`, `fetch_method: searxng_text` (R1 guard already shipped).
- Log trigger reason: `searxng_trigger: bot_all | bot_any | crawl_no_forecast`.

### Files

| File | Change |
|------|--------|
| `crawl_resilience.py` | `crawl_rows_have_usable_text`, `crawl_rows_any_bot_blocked`, `should_run_searxng_fallback(rows, message)` |
| `refresh.py` | Replace inline `should_try_searxng` with helper; pipeline log trigger reason |
| `tests/test_external_predictions_crawl_resilience.py` | Matrix tests for all trigger paths + negative case (empty rows) |

## Tasks

- [x] **5E-1** — Add helpers + unit tests (no behavior change until wired)
- [x] **5E-2** — Wire `refresh._record_from_crawl_group`; log `searxng_trigger`
- [x] **5E-3** — Integration test: mock crawl success + `pick_best` None → fallback invoked
- [ ] **5E-4** — Live smoke: moneycontrol + one broker source; refresh rollup shows fewer `error`, more `ok` or honest `not_found`

## Phase gate

```bash
python -m pytest tests/test_external_predictions_crawl_resilience.py tests/test_external_predictions.py -q --timeout=120
```

Pass 2→3 convergence on diff; zero open CONFIRMED.

## Success criteria

- Crawl succeeds but no forecast signal → SearXNG attempted before final `not_found`.
- Mixed bot + timeout failures → SearXNG attempted when any row is bot-blocked.
- No regression on R1 provenance or R3 URL ordering.
