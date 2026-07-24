# Phase 4: Path Learning + Mechanical Fallback Cleanup

**Type:** migrate + cleanup | **Depends on:** Phase 3 | **Out of scope:** new LLM prompts

**Goal:** Persist successful vision dismiss/click sequences into `saved_paths`; replay on fast path; demote mechanical JS to opt-in fallback.

## Files

| Action | Path |
|--------|------|
| Modify | `integrations/trade_integrations/dataflows/index_research/external_predictions/navigation_paths.py` |
| Modify | `integrations/trade_integrations/dataflows/index_research/external_predictions/path_store.py` |
| Modify | `integrations/trade_integrations/dataflows/crawl4ai_client.py` |
| Modify | `.env.example` |
| Test | `tests/test_external_predictions_browse.py`, `tests/test_external_predictions_paths.py` |

## Path replay extension

Today `replay_navigation_path` only replays final URL via single crawl. **Target:**

```
for step in trace.steps:
    if step.action == "goto": navigate url
    elif step.action == "click_text": execute on page
    elif step.action == "dismiss": run stored selector/text
    elif step.action == "scroll": ...
screenshot + markdown capture
```

On replay failure → increment `replay_failures`, fall back to exploratory (existing behavior).

## Auto-save vision steps

When `run_exploratory_browse` or `vision_navigate_url` succeeds:

- Append `NavigationStep(action="click_text", target="Maybe Later", url=current_url)` etc.
- `auto_save_path(source, horizon_days, trace)` — existing API

User **Approve path** in UI promotes dismiss steps to `approved_paths`.

## Mechanical JS demotion

| Before | After |
|--------|-------|
| `_popup_dismiss_js` always in `_run_config` | Default **off** when `EXTERNAL_PREDICTIONS_VISION_NAV=1` |
| Built-in `remove_overlay_elements` false | unchanged (keep off) |
| Tier-0 mechanical | Env `CRAWL4AI_MECHANICAL_DISMISS=1` for offline / no MiniMax |

Document in `.env.example`:

```
# Vision-first navigation (Miscellaneous crawl). Mechanical dismiss is fallback only.
EXTERNAL_PREDICTIONS_VISION_NAV=1
CRAWL4AI_MECHANICAL_DISMISS=0
```

## Tasks

### Task 1: Replay executor for non-goto steps

- [ ] `replay_navigation_path` uses `playwright_actions.execute_vision_actions` when CDP available
- [ ] Tests with synthetic trace (goto ET → click_text Maybe Later → goto article)

### Task 2: Save vision steps on success

- [ ] `browse_agent` / `vision_navigate_url` return executed steps
- [ ] `path_store.auto_save_path` merges dismiss steps

### Task 3: Env migration + docs

- [ ] Flip defaults per table above
- [ ] Update expert-agent index with Phase 5 vision-nav row

### Task 4: Program verification

```bash
pytest tests/test_external_predictions_browse.py tests/test_external_predictions_paths.py tests/test_external_predictions_page_block_detector.py tests/test_external_predictions_vision_navigator.py -q --timeout=120
```

**Phase gate:** replay test passes; ET fast-path replay from saved trace clears popups on second run without vision call (mock vision budget counter).
