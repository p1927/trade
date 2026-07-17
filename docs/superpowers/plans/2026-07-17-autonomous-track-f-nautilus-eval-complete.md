# Autonomous Track F — Nautilus Watch Eval Completeness

**Status:** Complete

**Goal:** Implement stubbed metrics and thesis break on Nautilus-primary path.

## Shipped

- `watch_eval.py`: `oi_change_pct`, `volume_spike_pct`
- `thesis_eval.py`: `evaluate_thesis_for_agent` → `THESIS_BROKEN` / `EXIT_NOW`
- `poll_loop.py`: thesis eval + `_dispatch_alerts` for THESIS
- `bridge_signal_actor.py`: handles `THESIS_BROKEN`
- `watch_actor.py`: thesis timer, OI/volume baselines
- `tests/test_nautilus_watch_eval.py`

## Verify

```bash
pytest tests/test_nautilus_watch_eval.py -v
```
