# Unified Watch Registry Migration

**Date:** 2026-07-22  
**Status:** Implemented

## Goal

Remove global env watchlists (`NAUTILUS_WATCH_SYMBOLS`, default monitor/auto-paper watchlists). Poll and alert only symbols declared by autonomous agents (`aa_*`) or `/agent` sessions (`ws_{session_id}`) via the unified watch registry under `reports/hub/_data/watches/`.

## Phases (shipped)

| Phase | Deliverable |
|-------|-------------|
| 0 | `integrations/trade_integrations/watch_registry/` — store, scope, api |
| 1 | `node.py` registry-only symbol collection; removed `BridgeConfig.watch_symbols` |
| 2 | REST `/watches` routes + registry-backed `mcp_set_watch_spec` |
| 3 | Session alert dispatch in `vibe_trigger.py`; `record_owner_alert_fired` + one-shot |
| 4 | `WatchersPanel` in ContextDrawer + autonomous plan banner |
| 5 | Monitor/auto-paper/options jobs derive symbols from agents + registry |

## Verification

```bash
python -m pytest tests/test_watch_registry.py tests/test_nautilus_watch_registry.py \
  tests/test_nautilus_handoff.py tests/test_autonomous_watch.py \
  tests/test_nautilus_preflight.py tests/test_nautilus_vibe_trigger.py -q --timeout=120
```

## Operational note

Remove `NAUTILUS_WATCH_SYMBOLS` from local `.env` — it is no longer read. Watches appear only after agent/session creates them (MCP `set_agent_watch_spec`, REST `/watches`, or UI delete via Watchers panel).
