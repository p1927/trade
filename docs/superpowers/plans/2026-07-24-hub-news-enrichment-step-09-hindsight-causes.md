# Step 09 — Hindsight Causes

**Type:** implement  
**Depends on:** Step 08  
**Module:** `hub_news_pipeline/step_09_hindsight_causes.py`

## Goal

After `future_events.expected_date` or event maturity, annotate whether **cited causes** aligned with actual factor/index move — not whether article price predictions were right.

## Logic

- For each `cause_indicator`: compare `direction_hint` vs actual factor/NIFTY move over window
- For `future_events`: after `expected_date`, note if event occurred / impact direction
- Skip `article_opinions` entirely
- Store `hindsight_causes[]` on ref — annotation only

## Wire

- `news_entity_worker.py` maintenance stage after impact refresh

## Files

| File | Action |
|------|--------|
| `hub_news_pipeline/step_09_hindsight_causes.py` | Create |
| `tests/hub_news_pipeline/test_step_09_hindsight_causes.py` | Create |
