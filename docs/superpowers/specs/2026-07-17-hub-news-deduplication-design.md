# Hub News Deduplication — Design Spec

**Date:** 2026-07-17  
**Status:** **Superseded** by `2026-07-17-hub-distilled-news-entity-design.md`  
**Scope:** Daily compaction of verified news SSOT; moderate semantic dedup with summary similarity

> This document is retained for reference. The approved approach is **Distilled News Events** with staging queue + async distillation — see the superseding spec and implementation plan `docs/superpowers/plans/2026-07-17-hub-distilled-news-entity.md`.

---

## Problem

News enters the hub through multiple ingest paths (aggregator, SearXNG, monitor, backfill). Dedup runs **only at ingest time** via `merge_raw_headlines()` in `news_dedup.py`. Stories that share the same narrative but differ in URL, title, or source accumulate in `reports/hub/_data/news_verified/records.parquet`, bloating the hub and polluting prediction attribution / scenario tools with redundant headlines.

Timeline artifacts (index factors, prediction ledger, company research history) use date-keyed rows and are not affected by news semantic dedup, but may have mechanical duplicate dates from retries.

---

## Goals

1. **Daily compaction** of verified news so the hub does not grow unbounded with near-duplicate stories.
2. **Moderate dedup (level B):** merge same-day stories about the same event — same topic, factor, market direction, and **similar summary/prediction** — even when headlines differ across outlets.
3. **Reuse existing factor taxonomy** (`news_tags.py` topics/themes/factors) — no parallel classifier vocabulary.
4. **Summary-similarity gate:** two stories merge only when their `content_summary` (or title fallback) is sufficiently similar.
5. **Preserve attribution integrity:** merged rows keep best verification, impact predictions, and union of sources; compaction is logged and reversible via ledger of removed IDs.

## Non-goals

- Aggressive cross-topic merging (e.g. oil + FII when narratives are distinct).
- Re-verification or LLM calls on every daily run (classifiers are keyword-based + summary similarity; LLM enrichment remains ingest-time only).
- Semantic dedup of OHLCV / factor time series (separate mechanical date-key compaction only).

---

## Architecture

```
18:35 IST daily cron (after factor snapshot + company archive)
  │
  ▼
hub_news_compactor.compact_verified_news(ticker, lookback_days=90)
  │
  ├─ load records.parquet for ticker within window
  ├─ hub row → raw headline dict (title, content_summary, tags_json, …)
  ├─ cluster_stories()          ← new: semantic + summary similarity
  ├─ pick_canonical_per_cluster() ← best row per cluster
  ├─ merge_cluster_metadata()   ← union sources, tags, preserve impact
  ├─ rewrite records.parquet
  ├─ append compaction report → _data/news_verified/compaction_log.jsonl
  └─ optional: compact_timeline_date_keys() on factor/prediction parquet
```

**Insertion point:** new module `integrations/trade_integrations/dataflows/index_research/hub_news_compactor.py`, scheduled via `vibetrading/agent/src/scheduled_research/index_jobs.py` as `hub_news_compaction` job.

**Manual ops:** `scripts/compact_hub_news.py --ticker NIFTY --days 90 --dry-run`

---

## Dedup algorithm (moderate + summary similarity)

### Stage 1 — Hard keys (unchanged)

Reuse existing logic from `news_dedup.py`:

| Key | Rule |
|-----|------|
| `canonical_story_id` | Normalized URL, else normalized title |
| `title_norm` | Exact normalized title match |
| `semantic_cluster_key` | `sem:{publish_day}:{topic}:{direction}:{primary_factor}` |

### Stage 2 — Summary-similarity merge (new)

After Stage 1 clusters are formed, within each **candidate bucket** compare pairs:

**Bucket key:** `(publish_day, ticker, primary_factor, market_direction)`

Only rows sharing the same bucket are eligible for summary merge. This enforces moderate (B) scope: same day, same dominant factor, same directional read.

**Similarity function:**

```python
def summary_similarity(a: str, b: str) -> float:
    # Normalize: de-clickbait, lowercase, collapse whitespace
    # Compare via difflib.SequenceMatcher.ratio()
    # Fallback: Jaccard on word tokens if either text < 40 chars
```

**Merge threshold:** `ratio >= 0.72` (configurable via `HUB_NEWS_DEDUP_SUMMARY_THRESHOLD`).

**Additional merge signals (any one sufficient with ratio >= 0.65):**

- `structured_summary_json.implied_factors` overlap ≥ 1 AND same `market_direction`
- `predicted_impact_json.direction` matches AND same primary factor
- Normalized title Jaccard ≥ 0.55 AND same semantic cluster key prefix (day + topic)

**Never merge when:**

- Different `publish_day`
- Conflicting market direction (bullish vs bearish) unless both tagged `mixed`/`neutral`
- Summary similarity < 0.55 regardless of other signals

### Stage 3 — Canonical row selection

When merging cluster `{A, B, C, …}` into one row:

| Field | Winner |
|-------|--------|
| `canonical_story_id` | Earliest `first_seen_at` row's ID (stable external references) |
| `title` | Longest de-clickbait title |
| `content_summary` | Longest summary |
| `structured_summary_json` | From row with most `facts` |
| `sources_json` | Union of all sources (dedupe by vendor+url) |
| `tags_json` | `merge_article_tags()` union |
| `verification_status` | Prefer `verified` > `partial` > `unverified` |
| `predicted_impact_json` | From highest-confidence verified row; tie → longest summary |
| `actual_impact_json` | Non-empty wins; else canonical row |
| `maturity_date`, `horizon_trading_days` | From row with `actual_impact_json` if present, else canonical |

Removed `canonical_story_id` values are appended to `compaction_log.jsonl` with `merged_into` pointer for audit.

---

## Classifier (factor-aligned, no new vocabulary)

Dedup classification reuses **`build_article_tags()`** and **`build_structured_summary()`** from existing modules:

| Classifier output | Source | Used in dedup |
|-------------------|--------|---------------|
| Topic (oil, fii, war, …) | `news_tags._TOPIC_KEYWORDS` | Semantic cluster key |
| Market direction | `_market_direction(themes)` in `news_dedup.py` | Bucket key + conflict guard |
| Primary factor | First entry in `tags.factors` | Semantic cluster + bucket key |
| Investor flow signal | `fii`/`dii` topics + themes `rally`/`selloff`/`inflow`/`outflow` | Already in theme keywords |
| Implied factors | `structured_summary.implied_factors` | Secondary merge signal |

**No LLM on daily run.** Tags are read from persisted `tags_json`; missing tags are backfilled once via existing `repair_hub_tags()` before compaction.

Future optional enhancement: LLM classify only at ingest for rows with empty topics (not in v1).

---

## Daily schedule

| Job | Cron (default) | Notes |
|-----|----------------|-------|
| `hub_news_compaction` | `35 18 * * *` | After factor snapshot (18:00) and company archive (18:30) |

Registered in `register_default_index_jobs()` alongside existing index jobs. Env override: `HUB_NEWS_COMPACTION_CRON`.

**Lookback window:** 90 calendar days (configurable). Rows older than window are untouched — they are stable and already attributed.

**Dry-run mode:** report merge groups without writing parquet (for ops validation).

---

## Timeline data (indexes & stocks)

News dedup does **not** alter timeline semantics. Separate lightweight step in the same job:

| Artifact | Compaction rule |
|----------|-----------------|
| `_data/index_factors/daily/*.parquet` | `drop_duplicates(subset=['date'], keep='last')` per file |
| `_data/index_predictions/ledger.parquet` | `drop_duplicates(subset=['prediction_date', 'ticker'], keep='last')` |
| `{SYMBOL}/company_research/history/{date}.json` | No change (one file per day by design) |
| `_data/news/daily/{date}.parquet` | Optional: run `merge_raw_headlines()` on archive row set, rewrite if count drops > 5% |

This prevents mechanical bloat from retries without touching factor values or prediction logic.

---

## Data flow impact

Consumers of `query_verified_news()` see fewer, richer canonical rows after compaction. No API contract change.

**Downstream safety:**

- `news_impact_engine` ledger rows keyed by old `canonical_story_id` → compaction log maps old→new; optional repair pass updates ledger references (v1: log only; repair in v1.1 if needed).
- Prediction attribution uses `publish_day` + tags — fewer duplicates improves signal, no schema change.

---

## Files to add / modify

| File | Change |
|------|--------|
| `hub_news_compactor.py` | **New** — clustering, similarity, merge, rewrite |
| `news_dedup.py` | Export `summary_similarity()`, `cluster_stories_for_compaction()` or keep in compactor |
| `verified_news_store.py` | Add `load_records_df()`, `replace_records_df()` helpers if missing |
| `index_jobs.py` | Register + dispatch `hub_news_compaction` job |
| `scripts/compact_hub_news.py` | **New** — CLI with `--dry-run`, `--days`, `--ticker` |
| `tests/test_hub_news_compactor.py` | Regression tests for merge thresholds and canonical selection |

---

## Error handling

- **Empty hub:** no-op, return `{merged: 0, removed: 0}`.
- **Write failure:** do not delete original parquet; write to temp file then atomic rename.
- **Partial tag rows:** run `repair_hub_tags()` inline before clustering.
- **Job overlap:** skip if compaction ran within last 20 hours (lock file in `_data/news_verified/.compaction.lock`).

---

## Success metrics

- Duplicate rate: `(rows_before - rows_after) / rows_before` logged per run; target ≥ 10% reduction on active 90d window once deployed.
- Zero regression in `tests/test_news_impact_engine.py` merge tests.
- `query_verified_news(limit=50)` returns no pairs with same semantic cluster key + summary similarity ≥ threshold.

---

## Open questions (resolved)

| Question | Decision |
|----------|----------|
| Dedup aggressiveness | **B — moderate** |
| Summary comparison | **SequenceMatcher on content_summary, threshold 0.72** |
| LLM daily classify | **No — keyword tags + summary similarity only** |
| Timeline data | **Mechanical date-key dedup only, same daily job** |
