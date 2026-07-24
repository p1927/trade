# Step 02 — Fetch HTTP

**Type:** implement  
**Depends on:** Step 01  
**Module:** `hub_news_pipeline/step_02_fetch_http.py`

## Goal

Best-effort HTTP article body via `article_body.fetch_article_body_with_html`. Populate `ctx.article_body`, `ctx.fetch_status`, and **`_raw_html_meta_published`** on ref (for step 03).

## Step contract

- Success → `ctx.enrichment_mode="full"`, `fetch_method="http"`
- Fail → `ctx.enrichment_mode="snippet_fallback"`, `fetch_status="failed"` — **do not stop pipeline**
- On any HTTP response with HTML → extract `article:published_time` / JSON-LD `datePublished` → `ref["_raw_html_meta_published"]`

## Tasks

- [x] Skip if `should_continue=False`
- [x] Skip fetch if summary already >= min len
- [x] Snippet fallback on failure — pipeline continues
- [x] Capture HTML meta datetime for step 03
- [x] Tests: mock fetch, failure → snippet_fallback, meta extraction

## Pytest

```bash
python -m pytest tests/hub_news_pipeline/test_step_02_fetch_http.py -q
```
