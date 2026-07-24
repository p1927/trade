# Step 02b — Fetch Crawl4AI

**Type:** implement  
**Depends on:** Step 02  
**Module:** `hub_news_pipeline/step_02b_fetch_crawl4ai.py`

## Goal

When step 02 failed or body short AND `HUB_NEWS_CRAWL4AI_ENABLED=1`, fetch via `crawl4ai_client.crawl_urls_parallel_sync` only (hub wrapper, not external_predictions).

**Default:** `HUB_NEWS_CRAWL4AI_ENABLED=0` (off until ops enable). Step always registered in `DEFAULT_STEP_ORDER`; no-op when disabled.

## Step contract

- No-op if step 02 succeeded with adequate body
- No-op if env disabled
- Success → upgrade to `enrichment_mode=full`, `fetch_method=crawl4ai`
- Fail → remain `snippet_fallback`

## Files

| File | Action |
|------|--------|
| `hub_news_pipeline/step_02b_fetch_crawl4ai.py` | Create |
| `tests/hub_news_pipeline/test_step_02b_fetch_crawl4ai.py` | Create — mock crawl client |

## Pytest

```bash
python -m pytest tests/hub_news_pipeline/test_step_02b_fetch_crawl4ai.py -q
```
