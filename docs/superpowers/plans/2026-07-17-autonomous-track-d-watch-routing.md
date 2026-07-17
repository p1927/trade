# Autonomous Track D — Watch Routing Fixes

**Status:** Complete

**Goal:** Route watch ticks by execution profile; fix empty Nautilus binding; expose `watch_path` in runtime UI.

## Shipped

- `profile.py`: `uses_nautilus_watch`, US `watch_backend=nautilus_alpaca`
- `watch.py`: profile-aware routing; US uses `run_once_alpaca`; no OpenAlgo poll for US
- `_detached_nautilus_watching`: registry membership only
- `runtime_status.py` + `AutonomousAgentCard.tsx`: `watch_path`, registry flags
- `tests/test_autonomous_watch.py`: US + empty-binding cases

## Verify

```bash
pytest tests/test_autonomous_watch.py tests/test_execution_profile.py -v
```
