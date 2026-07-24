# External Predictions — Vision-Guided Navigation (Master Index)

> **For agentic workers:** Execute phases **sequentially** via `superpowers:subagent-driven-development`. One implementer subagent per task; never parallel implementers on the same phase.

**Goal:** When Crawl4AI hits cookie walls, notification modals, or thin/blocked pages, escalate to MiniMax M3 vision to plan and execute human-like clicks/scrolls — then extract forecasts from the cleared page.

**Why this plan exists:** Phases 0–4 shipped vision for **extraction** and **one-shot link picking** (`browse_agent._vision_pick_listing_link`). Popup handling stayed **mechanical JS** in `crawl4ai_client.py`. That bypasses the original intent: browse like a user, not strip the DOM.

**Design anchor:** [2026-07-23-external-predictions-expert-agent-design.md](../specs/2026-07-23-external-predictions-expert-agent-design.md) — exploratory browse max 8 steps; agent decides actions; path replay.

## Phase map

| Phase | Plan file | Type | Depends on | Status |
|-------|-----------|------|------------|--------|
| 1 | [2026-07-24-external-predictions-vision-nav-phase-1-blocked-detection.md](./2026-07-24-external-predictions-vision-nav-phase-1-blocked-detection.md) | implement | — | pending |
| 2 | [2026-07-24-external-predictions-vision-nav-phase-2-action-planner.md](./2026-07-24-external-predictions-vision-nav-phase-2-action-planner.md) | implement | Phase 1 | pending |
| 3 | [2026-07-24-external-predictions-vision-nav-phase-3-crawl-browse-integration.md](./2026-07-24-external-predictions-vision-nav-phase-3-crawl-browse-integration.md) | implement | Phase 2 | pending |
| 4 | [2026-07-24-external-predictions-vision-nav-phase-4-path-learning-cleanup.md](./2026-07-24-external-predictions-vision-nav-phase-4-path-learning-cleanup.md) | migrate + cleanup | Phase 3 | pending |

## Global constraints (all phases)

- Self-hosted: Crawl4AI + Playwright CDP + MiniMax M3 vision; no paid crawl APIs.
- User-initiated Miscellaneous refresh only; street forecasts display-only.
- Bounded cost: max **3 vision-nav rounds per page**, max **2 vision calls per browse step**, reuse existing `LoopGuard` / `MAX_BROWSE_STEPS=8`.
- Mechanical popup JS remains **fast-path fallback**, not primary — vision runs when blocked-page detector fires.
- India context: `en-IN` locale, NIFTY 50 forecast goal unchanged.
- Structured output: extend `NavigationStep` with `dismiss`, `click_text`, `click_selector`; persist in `saved_paths`.
- Do not feed street forecasts into quant combiner.

## Architecture (target)

```
Crawl4AI fetch (screenshot + markdown)
  → blocked_page_detector(markdown, screenshot, url)
      → ok: continue to extract / browse link pick
      → blocked: vision_navigator.plan_actions(screenshot, goal, page_state)
          → playwright_executor.run(actions)  [CDP session]
          → re-crawl or in-page DOM read
          → repeat until clear or budget exhausted
  → financial_expert_agent (existing vision extract + cross-check)
```

## Verification protocol (every task)

```
Per task: implement → targeted pytest → Pass 2 diff audit → Pass 3 Bugbot/manual checklist → task reviewer
Phase gate: phase pytest scope exits 0
Live gate (Phases 2–3): CDP crawl ET `/markets/stocks/news` — screenshot shows article listing, no cookie/alert modal
Program gate: minimum 3 Program Pass 2→3 cycles on combined diff
```

## Success criteria (program-level)

1. Cookie / “Get Top News alerts” / OneTrust visible in screenshot → vision dismiss succeeds on ET stocks news (CDP live test).
2. Browse loop uses vision to click forecast article when markdown link list is empty but screenshot shows links.
3. Learned paths replay dismiss + click steps; mechanical JS optional via env.
4. Token budget documented; refresh job logs `vision_nav` steps in pipeline + `NavigationTrace`.
5. No regression: existing `financial_expert_agent` vision extract + cross-check still passes `tests/test_external_predictions_vision.py`.

## Execution order

Phase 1 → 2 → 3 → 4. Do not start N+1 until phase gate passes.
