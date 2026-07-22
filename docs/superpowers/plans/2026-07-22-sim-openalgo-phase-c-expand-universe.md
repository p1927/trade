# Phase C — Expand Universe (BANKNIFTY, SENSEX)

**Goal:** Same symtoken + quote parity for all HF underlyings.

**Implementation:** `SIM_MC_UNDERLYINGS=NIFTY,BANKNIFTY,SENSEX` in [`master_contract.py`](../../../integrations/trade_integrations/stock_simulator/master_contract.py). SENSEX options on `BFO`.

**Exit:** Master contract includes all three indices + options; tests in `test_build_symtoken_rows_all_hf_underlyings`.
