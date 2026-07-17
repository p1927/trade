# Autonomous Track G — Quant Repository Monitor

**Status:** Complete

**Goal:** Diff `quant_review` snapshots on schedule; push material alerts to Vibe.

## Shipped

- `monitor/quant_monitor.py`: `diff_quant_review`, `run_quant_monitor_tick`
- `hub.py`: `save_quant_review_history`
- `vibe_trigger.py`: `dispatch_quant_alert_sync`
- `autonomous_agent_jobs.py`: `JOB_TYPE_QUANT` scheduler job
- Agent JSON `quant_state` persistence
- `tests/test_quant_monitor.py`

## Verify

```bash
pytest tests/test_quant_monitor.py -v
```
