# Step 04 — Ref Enrich LLM (Causes + Future Timeline)

**Type:** implement  
**Depends on:** Step 03  
**Module:** `hub_news_pipeline/step_04_ref_enrich_llm.py`

## Goal

**Core LLM step.** Extract prediction-valuable **causes** and **future event timelines**. Explicitly **disregard article price predictions** for downstream use.

## LLM output schema (stored as `ctx.article_enrichment`)

```json
{
  "relevant": true,
  "enrichment_mode": "full|snippet_fallback",
  "cause_indicators": [
    {
      "factor": "fii_net_5d",
      "mechanism": "Foreign selling pressure on index heavyweights",
      "direction_hint": "bearish|bullish|mixed|unclear",
      "confidence": 0.0,
      "evidence_quote": "..."
    }
  ],
  "future_events": [
    {
      "event": "RBI MPC decision",
      "timeline_phrase": "next week",
      "expected_date": "2026-03-22",
      "date_confidence": "high|medium|low",
      "index_impact_mechanism": "Rate hold could support banks, weigh on growth"
    }
  ],
  "article_opinions": [
    {
      "kind": "price_prediction",
      "text": "NIFTY may hit 25000",
      "use_for_prediction": false,
      "reason_discarded": "article opinion not hub signal"
    }
  ],
  "facts": [{"text": "...", "as_of": "ISO"}],
  "distilled_summary": "...",
  "prediction_value_score": 0.0
}
```

## Prompt contract (locked instructions)

- Extract **what could cause** index/factor moves (causal mechanisms)
- Extract **future-dated events** the article references; resolve timeline phrases to `expected_date` relative to `publish_day`
- Move explicit NIFTY targets / price calls to `article_opinions` with `use_for_prediction: false`
- `snippet_fallback` mode: conservative, lower confidence, no invented numbers
- JSON only; no chain-of-thought in stored fields

## Modes

- `full` — use `ctx.article_body`
- `snippet_fallback` — title + RSS summary only

## Files

| File | Action |
|------|--------|
| `hub_news_pipeline/step_04_ref_enrich_llm.py` | Create |
| `hub_news_pipeline/models.py` | Create — dataclasses + parse/validate |
| `tests/hub_news_pipeline/test_step_04_ref_enrich_llm.py` | Create — mock MiniMax, fixture articles |
| `tests/hub_news_pipeline/fixtures/` | Sample HTML + expected JSON |

## Test cases (required)

- Earnings + RBI future event → `future_events` populated with dates
- "NIFTY to hit 25000" → lands in `article_opinions`, not causes
- FII outflow narrative → `cause_indicators` with factor key
- Snippet only → still returns causes with lower confidence
- Irrelevant sport → `relevant: false`

## Pytest

```bash
python -m pytest tests/hub_news_pipeline/test_step_04_ref_enrich_llm.py -q
```

## Gate

This is the **quality bar** step — do not wire resolver until step 04 tests pass.
