---
name: nse-browser
description: Route web research between preset NSE/NSDL data tools and local nodriver + MiniMax fallback.
---

# NSE Browser & Web Research

## Preset India market data (use first)

For FII/DII flows, NSDL FPI, bulk deals, delivery, PE/PB:

1. `get_nse_browser_status` — check hub freshness
2. `get_nse_browser_data(dataset=...)` — cache-first; `refresh=True` only when stale

| Need | dataset |
|------|---------|
| FII/DII institutional cash | `fii_dii` |
| NSDL FPI breakdown | `fpi` |
| Bulk/block deals | `bulk_deals` |
| Delivery % | `delivery` |
| Index PE/PB | `pe_pb` |

Fetch tiers: HTTP → nodriver deterministic → **MiniMax operator** → MiniMax extract.

## Ad-hoc web research

For RBI/SEBI filings, event pages, macro, company announcements:

- `run_browser_task(goal, start_urls)` — local nodriver + MiniMax operator/extract

Always pass `output_schema` when you need structured JSON for hub or trade plans (best-effort via MiniMax extract).

## CAPTCHA / bot blocks

1. `get_nse_browser_data(..., refresh_cookies=True)` for NSE
2. Ensure `NSE_BROWSER_HEADLESS=0` (headed Chrome)
3. Check `get_nse_browser_status` → `rescue.minimax.configured`

## Never

- Do not execute trades via browser tools — OpenAlgo MCP only
- Do not `refresh=True` on every turn — read hub cache first

## Env

- `MINIMAX_API_KEY` — powers nodriver operator loop and page extract fallback
