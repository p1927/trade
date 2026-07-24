# Hub News Enrichment Pipeline — Index Plan

**Date:** 2026-07-24  
**Goal:** Modular, step-named pipeline that enriches hub news sources with **cause indicators** and **future event timelines** (not article predictions). Each step is a separate module with its own sub-plan, tests, and **mandatory convergence gate** before the next step.

## Product focus (locked)

| Extract | Ignore for prediction inputs |
|---------|-------------------------------|
| **Cause indicators** — factors/mechanisms that could move NIFTY (FII flows, RBI, oil, earnings, geopolitics) | Article **predictions** (price targets, "NIFTY will hit X") — store as `article_opinions` for audit only |
| **Future event timeline** — dated/upcoming events the article discusses (budget, Fed, expiry, results) | Treating old article forecasts as current signals |
| **Facts with `as_of` + IST dates** | Merging with external predictions SSOT |

Quality over speed. Crawl failure → snippet fallback → LLM still runs. **All steps below are in scope** — ship one step at a time with tests + convergence; nothing deferred.

## Module layout (step-named files)

```
integrations/trade_integrations/dataflows/index_research/hub_news_pipeline/
  pipeline_context.py
  pipeline_runner.py
  step_01_relevance_gate.py
  step_02_fetch_http.py          # includes HTML meta capture for step 03
  step_02b_fetch_crawl4ai.py
  step_03_datetime_normalize.py
  step_04_ref_enrich_llm.py
  step_05_claims_bridge.py
  step_06_adjudicate_bridge.py
  step_07_event_distill_bridge.py
  step_08_temporal_attribution.py
  step_09_hindsight_causes.py
  step_10_backfill_maintainer.py
```

## Default pipeline order (locked — all steps registered)

```python
DEFAULT_STEP_ORDER = [
    "step_01_relevance_gate",
    "step_02_fetch_http",
    "step_02b_fetch_crawl4ai",   # no-op when disabled or step_02 full
    "step_03_datetime_normalize",
    "step_04_ref_enrich_llm",
    "step_05_claims_bridge",
    "step_06_adjudicate_bridge", # no-op when HUB_NEWS_LLM_ADJUDICATION_ENABLED=0
    "step_07_event_distill_bridge",
]

RESOLVER_THROUGH = "step_07_event_distill_bridge"
MAINTAINER_THROUGH = "step_04_ref_enrich_llm"  # step 10 backfill cap
```

Steps **02/02b** failures do **not** abort — set `enrichment_mode=snippet_fallback` and continue to **04**.

## Phase map

| Step | Sub-plan | Type | Status | Depends | Delivers |
|------|----------|------|--------|---------|----------|
| 01 | [step-01-relevance-gate.md](2026-07-24-hub-news-enrichment-step-01-relevance-gate.md) | implement | **Done** | — | Drop non-market refs; LLM ambiguous |
| 02 | [step-02-fetch-http.md](2026-07-24-hub-news-enrichment-step-02-fetch-http.md) | implement | **Done** | 01 | HTTP body + HTML meta → `_raw_html_meta_published` |
| 02b | [step-02b-fetch-crawl4ai.md](2026-07-24-hub-news-enrichment-step-02b-fetch-crawl4ai.md) | implement | **Done** | 02 | Crawl4AI tier when HTTP thin/failed |
| 03 | [step-03-datetime-normalize.md](2026-07-24-hub-news-enrichment-step-03-datetime-normalize.md) | implement | **Done** | 02 | IST `published_at`; meta preferred over RSS |
| 04 | [step-04-ref-enrich-causes.md](2026-07-24-hub-news-enrichment-step-04-ref-enrich-causes.md) | implement | **Done** | 03 | Causes + future timeline LLM |
| 05 | [step-05-07-bridge-distill.md](2026-07-24-hub-news-enrichment-step-05-07-bridge-distill.md) | refactor | **Done (05)** | 04 | Claims bridge + claim extraction |
| 06 | [step-05-07-bridge-distill.md](2026-07-24-hub-news-enrichment-step-05-07-bridge-distill.md) | refactor | **Done** | 05 | Per-ref adjudication; cause-aware |
| 07 | [step-05-07-bridge-distill.md](2026-07-24-hub-news-enrichment-step-05-07-bridge-distill.md) | refactor | **Done** | 06 | Distill hints + `structured_enrichment` on ref |
| 08 | [step-08-temporal-attribution.md](2026-07-24-hub-news-enrichment-step-08-temporal-attribution.md) | implement | **Done** | 07 | Prediction read-path filters |
| 09 | [step-09-hindsight-causes.md](2026-07-24-hub-news-enrichment-step-09-hindsight-causes.md) | implement | **Done** | 08 | Did cited causes align with move? |
| 10 | [step-10-backfill-maintainer.md](2026-07-24-hub-news-enrichment-step-10-backfill-maintainer.md) | migrate | **Done** | 04 | Capped maintainer backfill |
| UI | [step-ui-hub-cards.md](2026-07-24-hub-news-enrichment-step-ui-hub-cards.md) | implement | **Done** | 07 | Hub cards for causes/timeline |

## Execution order (locked — sequential, no skips)

1. **01 → 02 → 02b → 03 → 04** — unit test each; convergence before next
2. **05 → 06 → 07** — bridge into story pipeline; `test_pipeline_runner_chain.py`
3. **08** — temporal attribution consumers
4. **09 → 10** — maintenance path
5. **UI** — when pipeline stable through 07

## Integration gates

| Consumer | Hook |
|----------|------|
| `news_resolver.py` | `run_ref_pipeline(ref, through=RESOLVER_THROUGH)` when `HUB_NEWS_PIPELINE_ENABLED=1` |
| `news_entity_worker.py` | `distill_event` reads `structured_enrichment` / `pipeline_distill_hints` |
| `news_maintainer_facts.py` | Step 10 calls `run_ref_pipeline(..., through=MAINTAINER_THROUGH)` |
| **Prediction reads** | `prepare_items_for_prediction_attribution` — `resolve_news_impact`, `build_snapshot_from_hub`, `headlines_for_*`, `list_*_headlines` |
| **Inventory browse** | `query_verified_news`, `query_with_staging`, `tag_inventory` — no Step 08 filter (by design) |

## Global constraints

- Hub news only — no merge with `external_predictions/`
- Crawl4AI only via `crawl4ai_client.py` (hub wrapper)
- Article predictions → `article_opinions[]` with `use_for_prediction: false`
- Every step: unit tests + `StepResult` trace in context
- **Convergence:** 2× Pass 2→3 per step/phase before next (see `fix-review-before-stack`)

## Verification protocol

Per step: `pytest tests/hub_news_pipeline/test_step_XX*.py -q` exit 0 + convergence cited.

After step 07: `pytest tests/hub_news_pipeline/test_pipeline_runner_chain.py tests/test_news_resolver.py -q --timeout=120`

Program gate (all steps): full hub_news_pipeline suite + resolver tests.

## Success criteria

- Step 04 output includes `cause_indicators[]` and `future_events[]` on finance refs (full + snippet modes)
- Step 02 captures article meta datetime; step 03 prefers meta over RSS
- Step 02b upgrades thin HTTP refs when Crawl4AI enabled
- Prediction attribution uses causes; never `article_opinions`
- Each step identifiable in logs by `step_id`
