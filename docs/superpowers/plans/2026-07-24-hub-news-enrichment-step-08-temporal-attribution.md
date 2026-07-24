# Step 08 — Temporal Attribution

**Type:** implement  
**Depends on:** Steps 05–07  
**Module:** `hub_news_pipeline/step_08_temporal_attribution.py`

## Goal

Prediction consumers use **causes + future_events** with correct dates. Never `article_opinions`.

**Design:** All prediction/analysis read paths (`headlines_for_prediction_date`, `headlines_for_day`, `resolve_news_impact`, `build_snapshot_from_hub`, `list_*_headlines`) call `prepare_items_for_prediction_attribution`. Inventory browse (`query_verified_news`, `query_with_staging`, `tag_inventory`) stays unfiltered.

## Tasks

- [x] `get_market_context_as_of(publish_day)` in `news_market_context.py` (panel row fallback)
- [x] Update `headlines_for_prediction_date` / `filter_prediction_attribution_items`
- [x] Future events: include in attribution when `expected_date` near prediction horizon
- [x] Facts always rendered with `as_of`
- [x] Single-ref high `prediction_value_score` visibility option

## Files

| File | Action |
|------|--------|
| `hub_news_pipeline/step_08_temporal_attribution.py` | Create — filter helpers |
| `news_impact_engine.py` | Wire filters |
| `news_prediction_visibility.py` | Optional enriched single-ref rule |
| `tests/hub_news_pipeline/test_step_08_temporal_attribution.py` | Create |

## Pytest

```bash
python -m pytest tests/hub_news_pipeline/test_step_08_temporal_attribution.py -q
```
