# Hub Maintainer × Hub Source — Master Plan Index

> **For agentic workers:** Execute phases **sequentially** via `superpowers:subagent-driven-development`. One implementer subagent per task; never parallel implementers on the same phase.

**Goal:** Bring the entity **maintainer** fully in line with current Hub Source design (events SSOT, resolver/club merge, wiki gate, bridge contract, injection consumers) while preserving every existing capability. Maintainer’s primary job: **dedupe, merge, enrich, and prune** hub news so `events.parquet` stays factual, non-bloated, and consistent for all readers.

**Builds on:**

- [2026-07-17-hub-distilled-news-entity-design.md](../specs/2026-07-17-hub-distilled-news-entity-design.md) (entity model)
- [2026-07-23-hub-news-resolver-dedup-index.md](./2026-07-23-hub-news-resolver-dedup-index.md) (resolver T0–T4, index, club merge, safety scan)
- Evaluation: `.cursor/plans/hub_maintainer_evaluation_9cfd676f.plan.md`

## Maintainer mandate (product)

The maintainer is **not** ingest. It is the scheduled **hygiene + consolidation** pass over Hub Source:

| Responsibility | Mechanism (existing + planned) |
|----------------|--------------------------------|
| Merge duplicate stories | `compact_distilled_events` (two-signal + wiki club merge) |
| Roll up topic bloat | `rollup_parent_topic_events` (parent digest) |
| Remove stale / rejected / expired | `cleanup_hub_news`, staging TTL purge, discard ledger purge |
| Repair bad distillations | `repair_leaked_distilled_summaries`, redistill in `backfill_distilled_event_metadata` |
| Backfill entity metadata | `event_id`, `consensus`, `references`, index sync |
| Fetch / reconcile facts | LLM adjudication pipeline (`news_llm_story_pipeline`) on refs missing claims — **extend to maintainer pass** |
| Post-write safety | `run_post_upsert_safety_scan` — **extend to maintenance sweep** |
| Consumer refresh | `refresh_news_impact(refresh_ingest=False)` via bridge — **extend index doc sync** |

**Mode split (unchanged):**

| Mode | Cron / trigger | Scope |
|------|----------------|-------|
| `drain` | continuous + manual drain | Staging batch, light compact (7d), impact refresh |
| `maintenance` | `entity_maintenance_cron` + “Run maintainer now” | Full pipeline below |

**Maintenance stage order (target — no stage removed):**

```
ensure_hub_news_migrations
→ staging drain (optional batch in maint mode)
→ staging TTL purge
→ repair_leaked_distilled_summaries
→ backfill_distilled_event_metadata
→ fact_adjudication_backfill          [NEW]
→ compact_distilled_events (365d, multi-pass)
→ post_upsert_safety_sweep            [NEW — 7d window]
→ cleanup_hub_news
→ rollup_parent_topic_events
→ rebuild_event_index (if rows changed) [NEW — conditional]
→ refresh_news_impact (bridge, no ingest)
→ sync_news_impact_to_index_doc       [NEW — when latest.json present]
```

## Phase map

| Phase | Plan file | Type | Depends on | Status |
|-------|-----------|------|------------|--------|
| 0 | [2026-07-23-hub-maintainer-phase-0-audit.md](./2026-07-23-hub-maintainer-phase-0-audit.md) | Plan gate | — | **Done** |
| 1 | [2026-07-23-hub-maintainer-phase-1-ssot-alignment.md](./2026-07-23-hub-maintainer-phase-1-ssot-alignment.md) | Implement | Phase 0 | **Done** |
| 2 | [2026-07-23-hub-maintainer-phase-2-enrichment-dedup.md](./2026-07-23-hub-maintainer-phase-2-enrichment-dedup.md) | Implement | Phase 1 | **Done** |
| 3 | [2026-07-23-hub-maintainer-phase-3-bridge-docs.md](./2026-07-23-hub-maintainer-phase-3-bridge-docs.md) | Migrate + cleanup | Phase 2 | **Done** |
| 4 | [2026-07-23-hub-maintainer-phase-4-hub-ui.md](./2026-07-23-hub-maintainer-phase-4-hub-ui.md) | Implement | Phase 1 | **Done** |
| 5 | [2026-07-23-hub-maintainer-phase-5-injection-sync.md](./2026-07-23-hub-maintainer-phase-5-injection-sync.md) | Implement | Phase 2, 3 | **Done** |

Phases 4 may start after Phase 1 (UI does not require Phase 2 enrichment code). Phase 5 requires Phase 2 + bridge doc parity (Phase 3).

## Global constraints (all phases)

Copied from entity + resolver plans — **do not drift**:

- **SSOT:** `reports/hub/_data/news_events/events.parquet` + derived `event_index.parquet`. Staging: `_data/news_staging/`. Not `records.parquet` for new writes.
- **Public read/write for app code:** `trade_integrations.dataflows.news_hub_bridge` only. Ops worker may touch stores directly.
- **LLM on hot path:** Never on ingest. Distillation/adjudication/compaction LLM runs in worker/maintainer only.
- **Resolver invariants:** Parent-scoped index includes orphans; club merge star topology; post-upsert safety single-pass (scheduled sweep is separate bounded pass).
- **Wiki gate:** `HUB_NEWS_REQUIRE_LLM_WIKI=1` — maintainer pauses when wiki down (existing); do not bypass.
- **Soft-create visibility:** Single-ref events hidden from prediction attribution until corroborated — maintainer must not delete them; may merge when 2nd ref arrives via compaction.
- **Hub injection boundary:** General `/agent` sessions do not get `[news_impact_context]`; index artifact + news-scenario MCP remain authoritative. Maintainer refreshes snapshots injection reads indirectly.
- **Feature preservation:** Every existing maintainer stage, cron job, Hub button, drain mode, wiki compaction, rollup digest, cleanup, and `had_errors` rollup must remain unless explicitly superseded with parity tests.

## Evaluation backlog (must all land)

| ID | Finding | Phase |
|----|---------|-------|
| E-A | `docs/news-hub-bridge.md` stale (records SSOT) | 3 |
| E-B | Hub UI vs prediction visibility unlabeled | 4 |
| E-C | Injection coupling documented; index sync after maint | 5 |
| E-D | Hub UI missing `gates`, `source_availability`, migration | 4 |
| E-E | Maintainer UI label understates scope | 4 |
| E-F | Light sources default `rss` vs UI `rss,watcher` | 4 |
| E-G | Bridge doc missing wiki gate / entity pipeline | 3 |
| E-H | `runMaintenance` ignores paused / `had_errors` | 4 |
| E-I | `repair` / `backfill` still read `list_verified_records` (legacy) | 1 |
| E-J | No maintainer-wide fact adjudication or safety sweep | 2 |
| E-K | No conditional `event_index` rebuild after bulk merge | 2 |
| E-L | No `sync_news_impact_to_index_doc` after maintainer | 5 |

## Verification protocol

Per `fix-review-before-stack` + `review-evidence-discipline` — every task:

```
Task Pass 0–4 (convergence on task diff)
→ task reviewer (SDD)
→ record in .superpowers/sdd/progress.md
```

**Program pytest gate (run at end of Phases 1, 2, 5):**

```bash
python -m pytest tests/test_news_event_index.py tests/test_news_resolver.py \
  tests/test_news_entity_compaction.py tests/test_hub_wiki_probe.py \
  tests/test_news_migrations.py tests/test_news_events_store.py \
  tests/test_news_event_matching.py tests/test_news_staging_store.py \
  tests/test_hub_news_ingest.py tests/test_news_scenario_hub_context.py \
  tests/test_hub_context.py -q --timeout=120
```

**Bugbot (Phase completion):**

```text
Full Repository Path: /Users/pratyushmishra/Documents/GitHub/Trade
Diff: uncommitted changes
Custom Instructions: Maintainer must not drop stages; SSOT writes to events.parquet;
 silent success when had_errors; visibility filter asymmetry; wrong-frame list_verified vs list_events.
```

## Success criteria (program-level)

1. Maintainer repair/backfill/safety operate on **events SSOT** with parity for all pre-change behaviors.
2. Maintenance run produces structured stage summary surfaced in Hub UI (merged, removed, redistilled, adjudicated, errors).
3. `docs/news-hub-bridge.md` matches resolver/entity reality.
4. Hub page shows gates, source health, visibility badges, maintainer feedback — nothing from evaluation omitted.
5. After maintenance, `news_impact_latest.json` and embedded index `news_impact` reflect merged hub (when index doc exists).
6. Zero regression: drain mode, continuous drain, ingest, discard, resolver T0–T4, club merge, Option H gates unchanged.

## Execution order

**Phase 0** (audit gate) → **Phase 1** (SSOT) → **Phase 2** (enrichment/dedup) → **Phase 3** (docs) → **Phase 4** (UI, can overlap after Phase 1) → **Phase 5** (injection sync).
