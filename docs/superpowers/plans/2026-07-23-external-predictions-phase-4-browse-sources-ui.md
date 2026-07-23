# Phase 4: Browse Agent + Add Sites UI

**Goal:** Bounded Playwright browse (max 8 steps); enhanced Add Site form with entry URLs; user Approve path.

**Type:** implement | **Depends on:** Phase 3

### Task 1: `browse_agent.py` exploratory loop
### Task 2: Auto-save path on success; user approve API
### Task 3: Add site form validation (domain + entry_urls)
### Task 4: Chart uses extracted `target_date`; horizon mismatch badge

```bash
pytest tests/test_external_predictions_browse.py tests/test_external_predictions.py -q --timeout=120
```
