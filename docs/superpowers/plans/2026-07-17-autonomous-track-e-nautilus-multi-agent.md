# Autonomous Track E — Multi-Agent Nautilus Registry

**Status:** Complete

**Goal:** One Nautilus node watches many agents via `log/nautilus-watch.agents.json`.

## Shipped

- `nautilus_watch.py`: registry CRUD, multi-agent `ensure_nautilus_watch_for_agent`
- `node.py`: multi-actor config per registry agent; OpenAlgo + Alpaca data clients
- `run_watch_node.py`: `--registry` flag
- `stack_lib.sh`: registry display in `trade status`; launch with `--registry`
- `tests/test_nautilus_watch_registry.py`

## Verify

```bash
pytest tests/test_nautilus_watch_registry.py -v
trade start nautilus-watch --registry
```
