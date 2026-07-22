# Phase D — Trading and Data Endpoints

**Goal:** `margin`, intraday `history`, `depth`, `intervals` work for sim broker.

**Files:**
- [`openalgo/broker/stock_simulator/api/margin_api.py`](../../../openalgo/broker/stock_simulator/api/margin_api.py)
- [`openalgo/broker/stock_simulator/api/data.py`](../../../openalgo/broker/stock_simulator/api/data.py)

**Exit:** `/api/v1/margin` returns 200; `/api/v1/history` with `1m` returns bars; `/api/v1/depth` synthetic book.
