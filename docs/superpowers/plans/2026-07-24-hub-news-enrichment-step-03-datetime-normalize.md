# Step 03 — Datetime Normalize

**Type:** implement  
**Depends on:** Step 02  
**Module:** `hub_news_pipeline/step_03_datetime_normalize.py`

## Goal

Uniform IST `published_at`, `publish_day`. Prefer article meta from HTML when available. Never `min()` RSS vs meta.

## Output on context

- `ctx.published_at` ISO with TZ
- `ctx.publish_day` IST YYYY-MM-DD
- `ctx.date_conflict: bool`
- `ctx.timezone_source`

## Rules

1. JSON-LD / meta `datePublished` wins when present
2. Else RSS date at 09:15 IST default
3. |rss_day − meta_day| > 1 → `date_conflict=True`, keep meta

## Files

| File | Action |
|------|--------|
| `hub_news_pipeline/step_03_datetime_normalize.py` | Create |
| `article_body.py` | Optional — extract meta datetime helper |
| `tests/hub_news_pipeline/test_step_03_datetime_normalize.py` | Create |

## Pytest

```bash
python -m pytest tests/hub_news_pipeline/test_step_03_datetime_normalize.py -q
```
