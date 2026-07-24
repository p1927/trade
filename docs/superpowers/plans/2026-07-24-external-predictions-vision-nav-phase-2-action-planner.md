# Phase 2: Vision Action Planner + Playwright Executor

**Type:** implement | **Depends on:** Phase 1 | **Out of scope:** browse loop wiring, path persistence

**Goal:** Send screenshot + page goal to MiniMax M3; receive structured actions; execute them in the active CDP Playwright page like a user.

## Files

| Action | Path |
|--------|------|
| Create | `integrations/trade_integrations/dataflows/index_research/external_predictions/vision_navigator.py` |
| Create | `integrations/trade_integrations/dataflows/index_research/external_predictions/playwright_actions.py` |
| Modify | `integrations/trade_integrations/dataflows/index_research/external_predictions/minimax_vision.py` (shared resize helper) |
| Modify | `integrations/trade_integrations/dataflows/index_research/external_predictions/models.py` (`NavigationAction` extensions) |
| Test | `tests/test_external_predictions_vision_navigator.py` |

## Interfaces

**Consumes:** `detect_blocked_page`, `call_minimax_vision_json`, `resize_screenshot_for_m3` from `screenshot_utils.py`.

**Produces:**

```python
class VisionNavAction(TypedDict):
    action: Literal["click_text", "click_selector", "press_key", "scroll", "wait", "done"]
    target: str          # button label, selector, "Home", "down:800", ms for wait
    reason: str

def plan_vision_navigation(
    *,
    screenshot_b64: str,
    url: str,
    goal: str,
    block_reasons: list[str],
    prior_actions: list[VisionNavAction] | None = None,
) -> list[VisionNavAction]: ...

async def execute_vision_actions(
    page: Any,  # Playwright Page
    actions: list[VisionNavAction],
    *,
    pipeline: PipelineLogger | None = None,
) -> dict[str, Any]: ...
```

## MiniMax prompt contract

**System:** You help a financial researcher clear popups and reach NIFTY 50 forecast content on Indian financial news sites. Return ONLY JSON:

```json
{
  "actions": [
    {"action": "click_text", "target": "Maybe Later", "reason": "dismiss notification"},
    {"action": "click_text", "target": "Accept All", "reason": "cookie consent"},
    {"action": "scroll", "target": "up:0", "reason": "return to top"},
    {"action": "done", "target": "", "reason": "main content visible"}
  ],
  "page_clear": true
}
```

**User text includes:** URL, block_reasons, goal (`listing_forecast` | `dismiss_overlays` | `open_article`), up to 3 prior failed actions.

**Safety rails:**

- Allowlist actions only; reject `goto` off-domain URLs.
- Prefer `click_text` for ET/Moneycontrol modals (“Accept All”, “Maybe Later”, “×”).
- Max 5 actions per plan; max 3 plan rounds per page.
- Never click “Enable” on push notifications — always “Maybe Later” or close.

## Playwright executor

| Action | Implementation |
|--------|----------------|
| `click_text` | `page.get_by_text(target, exact=False).first.click(timeout=3000)` with fallback JS text scan |
| `click_selector` | `page.locator(target).first.click()` |
| `press_key` | `Home`, `Escape`, `PageDown` |
| `scroll` | Parse `up:N` / `down:N` → `window.scrollBy` |
| `wait` | `asyncio.sleep(ms/1000)` |
| `done` | no-op |

Return `{ "executed": [...], "errors": [...] }`.

## CDP session access

Crawl4AI does not expose the live `Page` after `arun()`. Phase 2 adds **`crawl_with_vision_recovery`** in `crawl4ai_client.py`:

1. Open CDP browser context (reuse `ensure_cdp_ready` + `_make_crawler`).
2. First pass: existing `_run_config` crawl.
3. If `detect_blocked_page` → capture fresh screenshot inside same session via direct Playwright page handle **before** crawler closes, OR refactor to `AsyncWebCrawler` hook / dedicated `vision_navigate_url()` that owns one page for up to 3 rounds.

**Recommended approach:** new async function `vision_navigate_and_extract(url) -> CrawlPageResult` that uses Crawl4AI strategy internals OR raw Playwright connected to CDP — single page, loop: screenshot → plan → execute → re-read markdown.

## Tasks

### Task 1: Extend `NavigationAction` in `models.py`

- [ ] Add `"dismiss"`, `"click_text"`, `"press_key"` to `NavigationAction` literal
- [ ] Add optional `target: str` field on `NavigationStep`
- [ ] Backward-compatible `from_dict`

### Task 2: `vision_navigator.py`

- [ ] `plan_vision_navigation` using `call_minimax_vision_json`
- [ ] Resize screenshot via existing `resize_for_m3` (512/1024)
- [ ] Parse + validate action list; clamp to allowlist

### Task 3: `playwright_actions.py`

- [ ] `execute_vision_actions` with error collection (no throw on single click miss)
- [ ] Unit tests with Playwright mock / stub page object

### Task 4: `vision_navigate_url()` skeleton in `crawl4ai_client.py`

- [ ] Async entry: blocked detect → plan → execute → re-scrape (max 3 rounds)
- [ ] Env gate: `EXTERNAL_PREDICTIONS_VISION_NAV=1` (default on when `vision_enabled()`)

```bash
pytest tests/test_external_predictions_vision_navigator.py tests/test_external_predictions_crawl_resilience.py -q --timeout=120
```

**Phase gate:** unit tests pass; manual CDP note in task report — live ET test deferred to Phase 3 integration gate.
