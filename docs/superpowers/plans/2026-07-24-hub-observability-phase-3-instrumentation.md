# Phase 3 — Instrumentation (Tier 0 complete)

**Type:** implement | **Tier:** 0 | **Status:** Done for autonomous + prediction

## Autonomous agents — logged

| Event | Module | Source |
|-------|--------|--------|
| `vibe_dispatch_skipped/sent/failed` | watch | `vibe_trigger.py` |
| `autonomous_watch_tick` | watch | `watch.py` |
| `autonomous_decision` | watch | `decisions.py` |
| `autonomous_scheduled_job_start` | watch | `autonomous_agent_jobs.py` |
| `job_dispatch_done` + rollup | schedule | `executor.py` |

## Prediction — logged

| Event | Module | Source |
|-------|--------|--------|
| Pipeline stages (all) | pipeline | `index_prediction_run_jobs`, `external_predictions_run_jobs` via `bridge_pipeline` |
| `index_prediction_job_done` | pipeline | `index_prediction_run_jobs.py` |
| `external_predictions_job_done` | pipeline | `external_predictions_run_jobs.py` |
| `hub_news_ingest_complete` + per-source failures | ingest | `hub_news_ingest.py` |
| `news_entity_worker_complete` + had_errors issue | ingest | `news_entity_worker.py` |

## LLM — logged

| Event | Module | Source |
|-------|--------|--------|
| `llm_call_complete/failed` | llm | `providers/chat.py`, `minimax_agent.py` |
| `react_iteration_threshold` | llm | `agent/loop.py` (iter ≥80% / max) |

## Agent API

- `GET /trade/observability/issues`
- `GET /trade/observability/summary`
- `POST /trade/observability/issues/{id}/resolve`

## Deferred (Tier 1+)

- Langfuse hierarchical traces
- Grafana/Loki dashboards
- Hub UI open-issues badge (Phase 5)
