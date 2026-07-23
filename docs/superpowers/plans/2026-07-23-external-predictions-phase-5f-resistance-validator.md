# Phase 5F — Resistance vs Target Validator

**Type:** implement  
**Depends on:** Phase 5E (optional parallel if 5E in flight)  
**Out of scope:** LLM prompt-only changes (already shipped)

## Goal

Close **R7** from Phase 5 convergence — prevent technical resistance/support levels from becoming `fetch_status: ok` analyst targets after `mid`-from-`high` promotion.

## Carried-forward finding

| ID | Was | Risk |
|----|-----|------|
| **R7** | HYPOTHESIS | LLM sets `target.high=24000` for "resistance near 24,000"; validator promotes to `mid`; body mentions "Nifty 50" → false `ok` |

## Design

### Post-extraction guard (after mid/high normalization)

Add `reject_resistance_only_target(record, body)` in `validators.py`:

- Regex/heuristic on **body + rationale** for resistance/support language near the numeric level without explicit target verbs (`target`, `forecast`, `expects`, `sees`, `projected`, house name + target).
- If match → `fetch_status: not_found`, `error_message: resistance_not_target` (distinct from `target_out_of_range`).
- Do **not** reject when text explicitly labels NIFTY target at that level (e.g. "NIFTY target of 24,500").

### Interaction with vision path

- Vision cross-check already tightened (Phase 5C); validator guard catches text-only / SearXNG path where vision is skipped.

### Files

| File | Change |
|------|--------|
| `validators.py` | `reject_resistance_only_target`; call from `validate_record` after mid-from-high |
| `tests/test_external_predictions_crawl_resilience.py` | Resistance-only body → `not_found`; explicit target body → `ok` |

## Tasks

- [x] **5F-1** — Implement heuristic + tests (failure-mode first)
- [x] **5F-2** — Wire into `validate_record`; ensure economictimes-style real targets still pass
- [ ] **5F-3** — Re-run live icici/motilal artifacts path; expect `not_found` not phantom `mid`

## Phase gate

```bash
python -m pytest tests/test_external_predictions*.py -q --timeout=120
```

## Success criteria

- "Face resistance near 24,000" without analyst target language → `not_found`, not `ok`.
- Weekly outlook "24,500 holds the key" with explicit NIFTY framing → still `ok` (economictimes regression).
