# Phase 2 — Tier 0 Event Emitter

**Type:** implement | **Tier:** 0 | **Depends on:** —

## Deliverables

- `integrations/trade_integrations/observability/` package:
  - `paths.py` — `log/observability/` under repo root
  - `schema.py` — `ObservabilityEvent`, modules enum
  - `context.py` — contextvars for trace_id, agent_id, session_id, job_id, ticker
  - `emitter.py` — `emit()`, JSONL append + flush, hooks issue registry on error
  - `store.py` — tail/read helpers for API
  - `logging_config.py` — `configure_trade_logging()` with `TRADE_LOG_LEVEL`
  - `bridge_pipeline.py` — PipelineLogger → emit
  - `rollup.py` — job status rollup + silent-success detection
- `.env.example` — `TRADE_OBSERVABILITY_ENABLED`, `TRADE_LOG_LEVEL`, path overrides

## Event schema

```json
{
  "ts": "ISO8601",
  "module": "watch|llm|ingest|schedule|pipeline|hub|system",
  "event": "string",
  "level": "info|warn|error",
  "trace_id": "",
  "agent_id": "",
  "session_id": "",
  "job_id": "",
  "ticker": "",
  "duration_ms": null,
  "detail": {}
}
```

## Verification

```bash
python -c "from trade_integrations.observability.emitter import emit; emit('system','test')"
test -f log/observability/events.jsonl
python -m pytest tests/test_observability.py -q
```
