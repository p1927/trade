# Hub UI — Causes & Future Timeline Cards

**Type:** implement  
**Depends on:** Steps 05–07  
**Out of scope for pipeline modules**

## Goal

Hub news cards show `cause_indicators`, `future_events`, `enrichment_mode`, `article_opinions` (collapsed, labeled "not used for prediction").

## Files

- `vibetrading/frontend/src/pages/Hub.tsx`
- `hub_status.py` — expose enrichment fields in API

## Defer until

Pipeline integration tests pass.
