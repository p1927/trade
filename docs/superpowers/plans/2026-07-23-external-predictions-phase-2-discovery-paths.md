# Phase 2: Discovery + Path Routing

**Goal:** SearXNG + web search parallel discovery; fast-path replay + exploratory fallback; auto-save paths; parse retry; `horizon_match` flag.

**Type:** implement | **Depends on:** Phase 1 | **Status:** **Done**

### Task 1: Wire `fetcher.py` SearXNG into refresh
### Task 2: `NavigationTrace` model + path store per `(source_id, horizon_days)`
### Task 3: Fast-path replay skeleton + fallback
### Task 4: Parse-check retry loop; soft horizon mismatch

```bash
pytest tests/test_external_predictions.py tests/test_external_predictions_paths.py -q --timeout=120
```
