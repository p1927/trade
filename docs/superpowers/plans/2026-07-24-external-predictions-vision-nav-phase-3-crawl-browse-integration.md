# Phase 3: Crawl + Browse Integration

**Type:** implement | **Depends on:** Phase 2 | **Out of scope:** path replay changes (Phase 4)

**Goal:** Wire vision navigation into the refresh pipeline and browse agent so blocked pages recover before extract; browse steps use vision to pick clicks when link extraction fails.

## Files

| Action | Path |
|--------|------|
| Modify | `integrations/trade_integrations/dataflows/crawl4ai_client.py` |
| Modify | `integrations/trade_integrations/dataflows/index_research/external_predictions/crawl4ai_fetcher.py` |
| Modify | `integrations/trade_integrations/dataflows/index_research/external_predictions/browse_agent.py` |
| Modify | `integrations/trade_integrations/dataflows/index_research/external_predictions/refresh.py` |
| Test | `tests/test_external_predictions_browse.py`, `tests/test_external_predictions_crawl_resilience.py` |

## Flow changes

### A. Single-URL crawl path (`crawl_urls_parallel`)

```
arun(url) → CrawlPageResult
  → if vision_nav enabled and detect_blocked_page(...).blocked:
        vision_navigate_url(same cdp session policy)
  → return final row (updated markdown + screenshot)
```

Mechanical `_popup_dismiss_js` becomes **tier-0** (env `CRAWL4AI_REMOVE_CONSENT_POPUPS=1` default): runs first; vision only if still blocked.

### B. Browse agent (`run_exploratory_browse`)

After each crawl step:

```
if detect_blocked_page: 
    vision_navigate current URL (counts as sub-step, not extra browse step)
elif not next_url from _pick_next_url:
    plan_vision_navigation(goal="open_forecast_article")
    execute → set next_url from resulting URL or vision-returned link
elif thin markdown:
    keep existing _vision_pick_listing_link (merge into vision_navigator API)
```

Pipeline logs: `source_log` events `"vision_nav: dismiss cookie"`, `"vision_nav: click Maybe Later"`.

### C. Refresh worker

`refresh.py` — when `capture_screenshot=True`, pass `vision_nav=True` into crawl group; surface `navigation_steps` from vision actions in `NavigationTrace`.

## Env flags

| Variable | Default | Meaning |
|----------|---------|---------|
| `EXTERNAL_PREDICTIONS_VISION_NAV` | `1` if MiniMax configured | Master switch |
| `EXTERNAL_PREDICTIONS_VISION_NAV_MAX_ROUNDS` | `3` | Per URL |
| `CRAWL4AI_REMOVE_CONSENT_POPUPS` | `1` | Tier-0 mechanical (keep) |

## Tasks

### Task 1: Integrate detector + vision into `crawl_urls_parallel`

- [ ] Call `detect_blocked_page` on every successful crawl when screenshot present
- [ ] On blocked → `vision_navigate_url`; merge metadata `vision_nav_steps`

### Task 2: Browse agent vision recovery

- [ ] Blocked page recovery before `break` on failed markdown
- [ ] Replace ad-hoc `_vision_pick_listing_link` with `plan_vision_navigation(goal="pick_listing_link")` returning optional `next_url` in JSON
- [ ] Respect max 2 vision calls per browse step

### Task 3: Pipeline + SSE logging

- [ ] `pipeline.info("vision_nav", ...)` in refresh path
- [ ] Attach `vision_nav_trace` to crawl metadata for UI step trace (optional v1: log only)

### Task 4: Live verification (mandatory)

```bash
# CDP up, .env CRAWL4AI_CDP_URL set
python3 scripts/verify_crawl4ai.py --url https://economictimes.indiatimes.com/markets/stocks/news
```

**Accept:** markdown ≥ 100 KB, forecast keyword lines ≥ 20, manual screenshot review — no cookie / notification modal.

```bash
pytest tests/test_external_predictions_browse.py tests/test_external_predictions_crawl_resilience.py tests/test_external_predictions_vision.py -q --timeout=120
```

**Phase gate:** pytest exit 0 + live ET CDP evidence in session notes.
