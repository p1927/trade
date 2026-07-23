# Hub Maintainer Phase 5 — Injection & Index Sync

**Type:** Implement  
**Depends on:** Phase 2 (maintainer output stable), Phase 3 (bridge contract)  
**Supersedes:** —  
**Out of scope:** Adding `[news_impact_context]` to general `/agent` chat (BY DESIGN per evaluation)

## Goal

After maintenance, ensure **injection consumers** (index research artifact, prediction panel, hub prefetch) see refreshed news impact without manual “Run analysis”. Document the coupling explicitly.

## File map

| File | Change |
|------|--------|
| `news_entity_worker.py` | Post-maintainer sync hook |
| `news_hub_bridge/__init__.py` | Orchestrate sync |
| `news_impact_engine.py` | Ensure `sync_news_impact_to_index_doc` idempotent |
| `hub_bridge.py` | Optional staleness hint when news_impact updated_at > index as_of |
| `tests/test_hub_bridge_index_prefetch.py` | Regression |
| `docs/news-hub-bridge.md` | Injection sync section (Phase 3 follow-up) |

## Task 1: sync_news_impact_to_index_doc after maintenance

- [ ] **Step 1:** Write failing test: after maintainer mock merges events, `sync_news_impact_to_index_doc` updates embedded `news_impact` in existing `latest.json` without changing `as_of` / prediction fields.
- [ ] **Step 2:** At end of `run_hub_news_entity_job` when `run_maintenance` and not paused:
  1. `refresh_news_impact(ticker, refresh_ingest=False)` (existing)
  2. Load index doc via `load_index_research_json(ticker)`; if present → `sync_news_impact_to_index_doc(doc)` and save via existing hub save helper (no full pipeline re-run).
- [ ] **Step 3:** Add manifest stage `index_news_sync`: `{ synced: bool, reason?: string }`.
- [ ] **Step 4:** Skip sync when no index doc (BY DESIGN — prediction not run yet).

## Task 2: News-scenario session frozen baseline (E-C)

- [ ] **Step 1:** Confirm `format_news_scenario_context` policy “never refresh index research” — maintainer sync must **not** bump `pipeline_as_of` or re-run orchestrator.
- [ ] **Step 2:** Test: news_scenario session config unchanged after maintainer sync (only `news_impact` subtree inside frozen doc if bound — document: scenario sessions bind specific doc snapshot; if user pinned `pipeline_as_of`, sync writes only when doc file matches session ticker and optional `allow_news_impact_patch` flag default false).

**Decision (locked):** Maintainer sync updates `news_impact_latest.json` always; updates embedded `news_impact` in `latest.json` **only when** doc already exists and `INDEX_NEWS_SYNC_ON_MAINTAINER=1` (default on). News-scenario sessions read bound snapshot — no auto-invalidate of active scenario drafts.

- [ ] **Step 3:** Add env `INDEX_NEWS_SYNC_ON_MAINTAINER` default `1` in `.env.example`.

## Task 3: Prefetch staleness hint (optional, small)

- [ ] **Step 1:** When building `[index_research_context]`, if `news_impact.updated_at` > index `as_of`, append one line: `news_impact_stale_vs_index: true; user may refresh index research for full reconcile`.
- [ ] **Step 2:** Test in `tests/test_hub_context_index.py`.

## Task 4: Documentation (E-C closure)

- [ ] **Step 1:** In `docs/news-hub-bridge.md`, section “Maintainer → injection”:
  - Maintainer → impact snapshot → index embed (optional) → prefetch on next agent turn
  - Not immediate chat injection
  - News-scenario MCP tools remain authoritative for scenario mode

## Evaluation closure

| ID | Task |
|----|------|
| E-C | Tasks 1–4 |
| E-L | Task 1 |

## Phase pytest scope

```bash
python -m pytest tests/test_hub_bridge_index_prefetch.py tests/test_hub_context_index.py \
  tests/test_news_scenario_hub_context.py tests/test_hub_context.py -q --timeout=120
```

Plus full program gate from index plan.

## Completion gate

- [ ] Maintainer run updates `news_impact_latest.json`.
- [ ] Index doc embed sync gated and tested.
- [ ] No regression to news-scenario freeze policy.
- [ ] Index evaluation backlog E-C, E-L closed.
