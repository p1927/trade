# Phase 6 — Agent Issue Registry

**Type:** implement | **Tier:** 0 | **Depends on:** Phase 2

## Deliverables

- `observability/issues.py` — fingerprint, dedupe, open/resolve, repeat-skip windows
- `log/observability/issues.jsonl` — agent SSOT
- API routes:
  - `GET /trade/observability/issues?status=open&module=watch`
  - `GET /trade/observability/summary`
  - `POST /trade/observability/issues/{issue_id}/resolve`
- Issue triggers wired from `emit(level=error)` and `emit_job_rollup(had_errors=True)`

## Verification

```bash
python -m pytest tests/test_observability.py -q
curl -s http://127.0.0.1:8899/trade/observability/issues?status=open
```
