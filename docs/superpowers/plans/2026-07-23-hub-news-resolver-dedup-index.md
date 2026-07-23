# Hub News Resolver & Dedup — Master Plan Index

> **For agentic workers:** Execute phases **sequentially** via `superpowers:subagent-driven-development`. One implementer subagent per task; never parallel implementers on the same phase.

**Goal:** Events-only SSOT with fast T1 index lookup, tiered staging resolver (T0–T4), two-signal club merge, LLM-Wiki mandatory gate, and post-upsert safety scan — all enabled by default.

**Builds on:** [2026-07-17-hub-distilled-news-entity-design.md](../specs/2026-07-17-hub-distilled-news-entity-design.md) (Tasks 1–10 base entity pipeline)

**Shipped in:** `7321e7f` | **Source evaluation plan:** `.cursor/plans/hub_news_dedup_evaluation_34f21137.plan.md`

## Phase map

| Phase | Scope | Key modules | Status |
|-------|--------|-------------|--------|
| −1 | LLM-Wiki probe + ingest gate | `hub_wiki/probe.py`, `_ingest.py`, `hub_status`, ingest UI pause | **Done** |
| 0 | Retire `records.parquet` primary path | `news_migrations.ensure_hub_news_migrations`, `news_migrations.py` | **Done** |
| 1 | `event_index.parquet` materializer | `news_event_index.py`, hooks in `news_events_store.py` | **Done** |
| 1b–2 | Unified resolver T0–T4 | `news_resolver.py`, staging TTL, `attach_refs_to_event` | **Done** |
| 2b | Two-signal club merge | `news_event_clubbing.py`, compaction + post-upsert wiring | **Done** |
| 3 | T4 gray-zone MiniMax agent | `news_resolver_agent.py` | **Done** |
| 4 | Post-upsert 7d safety scan | `news_post_upsert_safety.py`, `_apply_post_upsert_safety()` | **Done** |

## Global constraints (all phases)

- SSOT: `events.parquet` + derived `event_index.parquet` (not `records.parquet`).
- Resolver flow: T0 relevance → T2 wiki → T1 index match → T3 claims → T4 agent (gray) → rule fallback → create.
- Club merge: embedding/rule similarity **and** (shared `parent_event_id` **or** wiki link).
- OpenAlgo / bridge unchanged — hub storage + entity worker only.
- Defaults on (see `.env.example`): `HUB_NEWS_REQUIRE_LLM_WIKI=1`, `HUB_NEWS_RESOLVER_AGENT_ENABLED=1`, `HUB_NEWS_POST_UPSERT_SAFETY_SCAN=1`.

## Design invariants (BY DESIGN — do not “fix” without spec change)

| Invariant | Rationale |
|-----------|-----------|
| Parent-scoped index includes orphan rows (empty `parent_event_id`) | Macro threading: new parent-scoped refs must match events not yet parent-tagged |
| Star topology in club merge (anchor-only, no transitive B→C via A) | Prevents false transitive merges; locked by `test_build_duplicate_group_uses_star_topology_not_transitive` |
| Post-upsert safety is single-pass (no recursion) | Safety net on write path; scheduled compaction handles broader dedup |

## Convergence fixes (2026-07-23 review)

| ID | Fix | Module |
|----|-----|--------|
| IDX | `patch_event_meta` → `upsert_index_from_event` sync | `news_events_store.py` |
| SR | `had_errors` rolls up staging `errors > 0` | `news_entity_worker._part_had_errors` |
| WIKI | `wiki_link_confirms_pair` logs failures (merge still fail-closed) | `news_event_clubbing.py` |

## Verification protocol

```bash
python -m pytest tests/test_news_event_index.py tests/test_news_resolver.py \
  tests/test_news_entity_compaction.py tests/test_hub_wiki_probe.py \
  tests/test_news_migrations.py tests/test_news_events_store.py \
  tests/test_news_event_matching.py tests/test_news_staging_store.py \
  tests/test_hub_news_ingest.py -q --timeout=120
```

**Gate:** 71 passed (verified 2026-07-23, Option H finish batch).

## Option H finish batch (2026-07-23)

| Item | Status | Module |
|------|--------|--------|
| Soft-create hidden from Prediction until 2nd ref | **Done** | `news_prediction_visibility.py`, `verified_news_store.py`, `news_impact_engine.py` |
| `trade status` LLM-Wiki line | **Done** | `scripts/stack_lib.sh` → `stack_print_llm_wiki_status` |
| Entity cron logs every wiki-down run | **Done** | `news_entity_worker.py`, `index_jobs.py` |
| `news_collect` live-fetch wiki gate | **Done** | `news_collect.py`, `news_impact_engine.py`, `causal_attribution.py` |
| Prediction tab ingest gating + UI | **Done** | `trade_routes.py`, `NewsImpactPanel.tsx` |
| T0 relevance at staging enqueue | **Done** | `news_hub_bridge/_ingest.py` |

## Out of scope (tracked elsewhere)

| Item | Plan |
|------|------|
| News Impact timeline + expandable references UI | [2026-07-17-prediction-phase2-hub-news.md](./2026-07-17-prediction-phase2-hub-news.md) |
| `hub_empty` API status + constituent freshness hints | Same |
| Debounced market-hours-only staging worker | Same |
| T4 MCP subprocess loop (vs HTTP MiniMax) | Optional — HTTP path shipped |
| `club_status` lifecycle enum fields | BY DESIGN — merge via remove + ledger |

## SDD record

See `.superpowers/sdd/progress.md` — Hub News Resolver section.
