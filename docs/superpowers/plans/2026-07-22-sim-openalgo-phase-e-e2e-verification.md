# Phase E — E2E Verification

**Goal:** Confirm sim + OpenAlgo loop for NIFTY advisor/watch/paper paths.

**Checks:**
- `pytest tests/test_stock_simulator_master_contract.py tests/test_stock_simulator_hf_replay.py tests/test_stock_simulator_phase1.py -q`
- Master contract download via OpenAlgo UI or `master_contract_download()`
- `POST /api/v1/expiry`, `optionsymbol`, `quotes` for NIFTY
- Update [`2026-07-22-stock-simulator.md`](2026-07-22-stock-simulator.md) status table
