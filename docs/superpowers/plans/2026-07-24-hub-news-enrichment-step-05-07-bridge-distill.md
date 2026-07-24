# Steps 05–07 — Bridge Claims, Adjudicate, Event Distill

**Type:** refactor  
**Depends on:** Step 04  
**Modules:** `step_05_claims_bridge.py`, `step_06_adjudicate_bridge.py`, `step_07_event_distill_bridge.py`

## Goal

Chain existing pipeline pieces without duplicating LLM prompts. Step 04 owns causes/timeline; later steps **read** `article_enrichment`.

## Step 05 — Claims bridge

- Copy `distilled_summary` → ref summary
- Run `enrich_ref_with_claims` on enriched text
- Attach `cause_indicators` / `future_events` to ref dict for downstream

## Step 06 — Adjudicate bridge

- [ ] Skip when `HUB_NEWS_LLM_ADJUDICATION_ENABLED=0` (status `skipped`)
- [ ] Idempotent when `ref.adjudication` present
- [ ] Append `cause_indicators` hint to adjudication input
- [ ] Call `llm_adjudicate_refs([ref])`; attach verdict to ref
- [ ] Discard ref when adjudication marks `discard=True`

## Step 07 — Event distill bridge

- [ ] Build `pipeline_distill_hints` text from causes + future_events
- [ ] Attach `structured_enrichment` on ref for `distill_event`
- [ ] Wire `news_distillation._llm_distill` to include hints in prompt
- [ ] Never pass `article_opinions` into prediction paths

## Integration

| File | Action |
|------|--------|
| `news_resolver.py` | `through="step_07_event_distill_bridge"` |
| `pipeline_runner.py` | Register steps 02b, 06, 07 in `DEFAULT_STEP_ORDER` |
| `tests/hub_news_pipeline/test_pipeline_runner_chain.py` | Integration test steps 01–07 |

## Pytest

```bash
python -m pytest tests/hub_news_pipeline/test_pipeline_runner_chain.py tests/test_news_resolver.py -q --timeout=120
```
