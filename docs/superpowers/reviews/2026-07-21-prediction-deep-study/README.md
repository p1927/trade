# Prediction Deep Study — README

**Completed:** 2026-07-21  
**Plan:** Prediction Tab Analysis Deep Study (Phases 0–7)

## What this is

File-by-file code review of the **Prediction tab Run analysis** pipeline: how forecasts are built, how to verify them, and documented inconsistencies backed by internal code evidence and external quant-finance references.

## Start here

1. **[00-verification-playbook.md](00-verification-playbook.md)** — commands to run after every analysis
2. **[00-issue-register.md](00-issue-register.md)** — consolidated findings (C1–C10 + N-series)
3. **Phase docs** — deep dive per layer

## One-sentence model

```
expected_return ≈ bottom_up(constituents) + macro_delta(Ridge + gates + overlay)
  → reconcile(scenarios) → finalize → optional debate merge → optional forecast lab combiner
```

## Top 5 issues to fix (study recommendation)

| Priority | ID | Issue |
|----------|-----|-------|
| 1 | N-04 | Debate merge after reconcile without re-anchor |
| 2 | N-05 | Simulate baseline ≠ displayed headline |
| 3 | N-02 | Regime gate bypass when gated output ≈ 0 |
| 4 | N-01 | UI macro key audit covers 24/52 factors |
| 5 | N-06 | Ledger scenario metadata field mismatch |

## External references

- [IBKR — Ridge & polynomial regression](https://www.interactivebrokers.com/campus/ibkr-quant-news/beyond-the-straight-line-advanced-linear-regression-models-for-financial-data/)
- [Walk-forward validation](https://www.dcfmodeling.com/blogs/blog/leveraging-regression-financial-model)
- [Hierarchical forecast reconciliation](https://robjhyndman.com/papers/Hierarchical6.pdf)
- [SHAP under correlated features (Aas 2021)](https://martinjullum.com/publication/aas-2021-explaining/aas-2021-explaining.pdf)

## Verification run this session

- `python scripts/audit_prediction_data.py --days 500` → exit 0
- `pytest tests/test_prediction_data_consistency.py tests/test_history_panel.py` → 7 passed
- Hub `latest.json` cross-check: debate_merged breaks simple sum identity (documented)

## Internal cross-refs

- [prediction-north-star.mdc](../../../.cursor/rules/prediction-north-star.mdc)
- [2026-07-16-prediction-master-plan.md](../plans/2026-07-16-prediction-master-plan.md)
- [2026-07-17-prediction-risks-assumptions-premortem.md](../plans/2026-07-17-prediction-risks-assumptions-premortem.md)
