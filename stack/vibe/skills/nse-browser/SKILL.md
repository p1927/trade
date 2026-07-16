---
name: nse-browser
description: Route web research between preset NSE/NSDL data tools, Skyvern agentic browse, and hub cache.
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

Fetch tiers: HTTP → nodriver → **Skyvern** → MiniMax fallback.

## Ad-hoc web research

For RBI/SEBI filings, event pages, macro, company announcements, non-preset URLs:

- `run_browser_task(goal, start_urls, output_schema)` — Skyvern agentic extract
- Or use native **skyvern** MCP tools when configured

Always pass `output_schema` when you need structured JSON for hub or trade plans.

## CAPTCHA / bot blocks

1. `get_nse_browser_data(..., refresh_cookies=True)` for NSE
2. Ensure `NSE_BROWSER_HEADLESS=0` (headed Chrome)
3. Check `get_nse_browser_status` → `rescue.skyvern.reachable`

## Never

- Do not execute trades via browser tools — OpenAlgo MCP only
- Do not `refresh=True` on every turn — read hub cache first

## Env

- `SKYVERN_API_KEY` — from http://localhost:8080/settings after `docker compose -f docker-compose.skyvern.yml up -d`
- `MINIMAX_API_KEY` — fallback when Skyvern unavailable
