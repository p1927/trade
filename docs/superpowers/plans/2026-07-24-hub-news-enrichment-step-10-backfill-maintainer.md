# Step 10 — Backfill Maintainer

**Type:** migrate  
**Depends on:** Step 04  
**Module:** `hub_news_pipeline/step_10_backfill_maintainer.py`

## Goal

Re-run steps 02–04 on thin legacy refs. Cap: `limit=50`, `lookback_days=90`. Idempotent if `article_enrichment` present.

## Wire

- Extend `news_maintainer_facts.py` or new maintainer stage calling `run_ref_pipeline(..., through="step_04")`

## Pytest

```bash
python -m pytest tests/hub_news_pipeline/test_step_10_backfill_maintainer.py -q
```
