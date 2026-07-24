# Phase 1: Blocked-Page Detection

**Type:** implement | **Depends on:** — | **Supersedes:** — | **Out of scope:** LLM calls, Playwright clicks

**Goal:** Deterministic signal for “this page is not usable yet — escalate to vision navigation” before extraction or browse link scoring.

## Files

| Action | Path |
|--------|------|
| Create | `integrations/trade_integrations/dataflows/index_research/external_predictions/page_block_detector.py` |
| Modify | `integrations/trade_integrations/dataflows/index_research/external_predictions/crawl_resilience.py` (reuse bot-block helpers) |
| Test | `tests/test_external_predictions_page_block_detector.py` |

## Interfaces

**Produces:**

```python
@dataclass
class PageBlockSignal:
    blocked: bool
    reasons: list[str]  # e.g. "cookie_banner", "thin_markdown", "footer_only_viewport"
    confidence: float   # 0–1

def detect_blocked_page(
    *,
    url: str,
    markdown: str,
    screenshot_b64: str | None = None,
    title: str = "",
) -> PageBlockSignal: ...
```

**Consumes:** existing `is_crawl_bot_blocked`, `crawl_rows_have_usable_text`, keyword forecast heuristics from `url_policy.py`.

## Detection rules (v1)

| Signal | Heuristic |
|--------|-----------|
| `cookie_banner` | Markdown or title matches OneTrust / “We value your privacy” / “Accept All” / “cookie” within first 2 KB |
| `notification_modal` | “Get Top News alerts” / “Maybe Later” / “Enable” notification copy in markdown |
| `thin_markdown` | `< 1200` chars AND listing URL (`is_allowed_listing_url`) AND zero forecast keyword hits |
| `footer_only` | Markdown has “Latest News” grid + “Copyright © Bennett” but no article H1 / `articleshow` links in first 40 lines |
| `bot_blocked` | Delegate to `is_crawl_bot_blocked` / Akamai wrap |
| `vision_cookie_screenshot` | Optional fast OCR-free check: JPEG size > 50 KB AND markdown thin (defer full vision to Phase 2) |

**Not blocked:** article page with forecast body, indices page with Nifty table, markdown ≥ 1200 on listing with link candidates.

## Tasks

### Task 1: `page_block_detector.py`

- [ ] Implement `PageBlockSignal` + `detect_blocked_page`
- [ ] Export `BLOCK_REASONS` constants for pipeline logs
- [ ] Wire `screenshot_b64` as optional (detector works text-only; screenshot flag reserved for Phase 2 vision confirm)

### Task 2: Tests

- [ ] Fixture strings from live ET captures (cookie modal markdown snippet, clean stocks/news markdown, footer-only markdown)
- [ ] Assert `cookie_banner` on dismiss-off ET snippet
- [ ] Assert not blocked on `et_stocks_news_v6`-style markdown sample (forecast lines + articleshow links)
- [ ] Assert `thin_markdown` on `/topic/nifty-50` broken crawl sample

```bash
pytest tests/test_external_predictions_page_block_detector.py -q --timeout=60
```

**Phase gate:** all tests exit 0; no production call sites yet (detector only).
