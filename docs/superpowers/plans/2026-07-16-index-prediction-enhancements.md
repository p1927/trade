# Index Prediction Enhancements Implementation Plan

> **Goal:** Add PCR, technical momentum, constituent momentum rollup, calendar regressors, walk-forward MAE, and direction classifier to the existing hybrid NIFTY predictor — without importing external ML repos.

**Architecture:** New pure-Python feature modules feed `factor_matrix.py` and `macro_global.py`. Attribution blends sentiment + momentum. `predictor.py` uses walk-forward OOS MAE and optional LogisticRegression direction head. Each step has dedicated unit tests.

---

## Tasks

- [x] Task 1: `technical_features.py` + `calendar_features.py` + tests
- [x] Task 2: PCR in `macro_global.py` + factor keys + backfill + tests
- [x] Task 3: `constituent_momentum.py` + attribution blend + tests
- [x] Task 4: Walk-forward MAE + direction classifier in `predictor.py` + tests
- [x] Task 5: Wire `snapshot.py`, `history_loader.py`, `aggregator.py`
- [x] Task 6: Full pytest run (76/77 index tests pass; 1 pre-existing batch_constituents failure)
