# Company Research Enrichment Pipeline — Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a staged, market-aware data-collection pipeline that enriches a structured `CompanyResearchDoc` for **India-first (NSE/BSE)**, then **dual India + US** equities — identity, peers, upcoming events, news, fundamentals, sentiment, corporate-event forecasts, and forward signals — feeding TradingAgents analysts.

**Architecture:** `company_research/` package under `integrations/trade_integrations/dataflows/` with a **market router** (`IN` vs `US`) that selects stage adapters per ticker. India stages run first in build order; US stages added incrementally. Heavy services (ED-ALPHA, SentimentPulse) run as **optional Docker sidecars** queried via HTTP; lightweight libs (nselib, india-corp-actions, edgartools, finvizfinance) run in-process. Every step has a **smoke gate** before the next step starts.

**Tech Stack:** Python 3.12, `trade_integrations`, OpenAlgo (India live data), `nselib`, `india-corp-actions`, `dalal`, `yfinance`, `finvizfinance`, `edgartools`, `earnings`, `finverse`, ED-ALPHA (Docker), SentimentPulse (Docker or in-process `classify.py`), existing SearXNG/RSS/Polymarket/FRED.

## Global Constraints

- All new code in `integrations/trade_integrations/` — minimal TradingAgents submodule edits (one `@tool` in Phase 10).
- Follow `news_aggregator/` patterns: best-effort per stage, vendor attribution, never fail whole pipeline on one stage.
- **Build order: India stages first → US stages → cross-market batch.**
- **No modules skipped** — every researched project gets an adapter; stages that need infra report `status="skipped"` with reason until their service is up.
- Default horizon: `TRADINGAGENTS_RESEARCH_LOOKAHEAD_DAYS=14`.
- Default peers: `TRADINGAGENTS_RESEARCH_MAX_PEERS=8`.
- Each step ends with a runnable smoke command; do not proceed until it passes.
- User rule: no extra docs beyond this plan; smoke tests only (meaningful coverage per stage).

---

## Market Router (India-first, dual from start)

```python
# sources/market.py
class Market(str, Enum):
    IN = "IN"   # NSE / BSE
    US = "US"   # NYSE / NASDAQ

def detect_market(ticker: str) -> Market:
    t = ticker.strip().upper()
    if t.endswith(".NS") or t.endswith(".BO"):
        return Market.IN
    if t.startswith("^") and t in ("^NSEI", "^BSESN"):
        return Market.IN
    if t in ("NIFTY", "BANKNIFTY", "SENSEX", "NIFTY50"):
        return Market.IN
    # Plain symbol without suffix: default IN when OpenAlgo configured, else US
    if _openalgo_configured():
        return Market.IN
    return Market.US

def normalize_ticker(ticker: str, market: Market) -> str:
    # RELIANCE → RELIANCE.NS for yfinance; RELIANCE for OpenAlgo/NSE APIs
    ...
```

| Stage | India adapters (build first) | US adapters (build second) |
|-------|------------------------------|----------------------------|
| Identity | OpenAlgo quote + `dalal.meta()` / nselib PE | yfinance `.info` |
| Peers | nselib index constituents + sector peers | finvizfinance `ticker_peer()` |
| Calendar | `india-corp-actions` results + board meetings + nselib `event_calendar_for_equity()` | yfinance `Calendars` + `earnings` package |
| Fundamentals | `dalal.fundamentals()` (BSE) + nselib `financial_results_for_equity()` | yfinance + edgartools |
| SEC / Filings | NSE announcements + BSE corp filings (nselib/dalal) | edgartools + Alpha-Pulse (optional) |
| News | Existing aggregator + India RSS (ET, Livemint, Google News IN) | aggregator + finviz news |
| Sentiment | SentimentPulse `classify.py` on headlines | same |
| Corp-event forecast | — (ED-ALPHA is US SEC 8-K only) | ED-ALPHA HTTP API |
| Earnings surprise | — (Finverse US-focused) | Finverse `ml.earnings_surprise` |
| Forward / macro | India VIX (nselib), FII/DII, Polymarket | FRED + Polymarket + yfinance economic cal |
| Live price | OpenAlgo | yfinance |

---

## Full Module Registry (nothing skipped)

| Module | Role | Integration mode | India | US |
|--------|------|------------------|-------|-----|
| **OpenAlgo** | Live NSE/BSE OHLCV, quote | In-process HTTP (existing) | ✅ | — |
| **nselib** | Event calendar, financials, index peers, India VIX, FII/DII | pip in-process | ✅ | — |
| **india-corp-actions** | Dividends, splits, results dates, board meetings | pip in-process | ✅ | — |
| **dalal** | Unified NSE/BSE quote, fundamentals, announcements | pip in-process | ✅ | — |
| **news_aggregator** | Multi-source news (SearXNG, yfinance, AV) | existing | ✅ | ✅ |
| **RSS feeds** | ET, Livemint, BSE, Google News IN | existing | ✅ | partial |
| **SentimentPulse** | FinBERT headline sentiment + NER | Docker sidecar OR in-process `classify.py` | ✅ | ✅ |
| **yfinance** | Profile, calendar, fundamentals | existing dep | ✅ (.NS/.BO) | ✅ |
| **finvizfinance** | US peers, screener, news | pip in-process | — | ✅ |
| **edgartools** | SEC 10-K/10-Q/8-K | pip in-process | — | ✅ |
| **earnings** (lcsrodriguez) | Earnings calendar, transcripts | pip in-process | — | ✅ |
| **EarningsPy** | PEAD, next-week earnings | pip in-process | — | ✅ |
| **Finverse** | Beat/miss probability, revenue forecast | pip in-process | — | ✅ |
| **ED-ALPHA** | Corporate event forecast (8-K) | Docker sidecar → FastAPI :8000 | — | ✅ |
| **Alpha-Pulse** | Real-time 8-K LLM parser | subprocess or HTTP | — | ✅ |
| **Polymarket** | Crowd-implied event odds | existing | macro | macro |
| **FRED** | US macro | existing | — | ✅ |
| **deanfi-collectors** | Reference patterns for earnings/news collectors | read-only reference | — | ✅ |

---

## Pipeline Stages (enrichment order)

```
Input: ticker (e.g. "RELIANCE" or "AAPL")
  │
  ▼
Stage 0: Market detect   → IN | US, normalized symbols
  │
  ▼
Stage 1: Identity        → name, sector, exchange, market cap
  │
  ▼
Stage 2: Universe        → peers[], sector/index context
  │
  ▼
Stage 3: Calendar        → results/earnings, board meetings, corp actions (next N days)
  │
  ▼
Stage 4: Fundamentals    → latest quarterly metrics, ratios
  │
  ▼
Stage 5: Filings         → NSE/BSE announcements (IN) or SEC 10-Q/8-K (US)
  │
  ▼
Stage 6: News            → company + top-3 peer headlines
  │
  ▼
Stage 7: Sentiment       → SentimentPulse scores on news batch
  │
  ▼
Stage 8: Corp events     → ED-ALPHA ranked 8-K predictions (US only)
  │
  ▼
Stage 9: Earnings signal → Finverse beat/miss (US) + historical surprise
  │
  ▼
Stage 10: Forward/Macro  → Polymarket, FRED, India VIX, FII/DII
  │
  ▼
Output: CompanyResearchDoc → format.py → markdown
```

Each stage → `StageResult(status, vendor, data, errors[])`. Pipeline always returns a doc; failed stages are `partial` or `skipped`.

---

## File Structure

```
integrations/trade_integrations/dataflows/company_research/
├── __init__.py
├── models.py
├── config.py
├── market.py                 # detect_market, normalize_ticker
├── aggregator.py             # run_company_research()
├── format.py
└── sources/
    ├── __init__.py           # STAGE_REGISTRY per market
    ├── identity_in.py        # OpenAlgo + dalal
    ├── identity_us.py        # yfinance
    ├── peers_in.py           # nselib index/sector
    ├── peers_us.py           # finvizfinance
    ├── calendar_in.py        # india-corp-actions + nselib
    ├── calendar_us.py        # yfinance + earnings + EarningsPy
    ├── fundamentals_in.py    # dalal + nselib
    ├── fundamentals_us.py    # yfinance + edgartools
    ├── filings_in.py         # nselib announcements, dalal
    ├── filings_us.py         # edgartools + alpha_pulse client
    ├── news.py               # delegates to news_aggregator (both markets)
    ├── sentiment.py          # SentimentPulse client
    ├── corp_events.py        # ED-ALPHA HTTP client
    ├── earnings_signal.py    # Finverse + earnings package
    └── macro.py              # FRED, Polymarket, nselib VIX/FII

integrations/trade_integrations/clients/
├── sentiment_pulse.py        # HTTP or in-process classify
├── ed_alpha.py               # ED-ALPHA FastAPI client
└── alpha_pulse.py            # optional 8-K parser subprocess

stack/
├── docker-compose.research.yml   # ED-ALPHA + SentimentPulse sidecars
└── research/                     # env samples for sidecars

scripts/
├── run_company_research.py
├── smoke_research_stage.py       # --stage identity_in --ticker RELIANCE
└── smoke_research_pipeline.py    # full pipeline smoke per market
```

---

## Docker Sidecars (ED-ALPHA + SentimentPulse)

Add `stack/docker-compose.research.yml` alongside existing SearXNG compose:

```yaml
# stack/docker-compose.research.yml (sketch)
services:
  ed-alpha-db:
    image: postgres:16
    # ED-ALPHA db schema on first boot
  ed-alpha-backend:
    build: ../vendor/ED-ALPHA  # git submodule or clone on first setup
    ports: ["8000:8000"]
    depends_on: [ed-alpha-db]
  sentiment-api:
    build: ../vendor/SentimentPulse
    ports: ["8081:8081"]
    # OR use classify-only mode without full Kafka stack
```

**Lightweight SentimentPulse path (recommended for P3):** vendor only `classify.py` in-process — no Kafka/TimescaleDB. Full Docker stack optional in P8.

**ED-ALPHA path:** clone as git submodule `vendor/ED-ALPHA`, run `docker compose -f stack/docker-compose.research.yml up ed-alpha-backend`. Pipeline calls `GET /api/predictions?ticker=AAPL` (exact path from ED-ALPHA OpenAPI docs).

Env vars (`.env.example`):
```
TRADINGAGENTS_RESEARCH_LOOKAHEAD_DAYS=14
TRADINGAGENTS_RESEARCH_MAX_PEERS=8
TRADINGAGENTS_RESEARCH_MARKET_DEFAULT=IN
SEC_EDGAR_IDENTITY=Your Name your@email.com
ED_ALPHA_BASE_URL=http://localhost:8000
SENTIMENT_PULSE_URL=http://localhost:8081
SENTIMENT_PULSE_MODE=inprocess   # inprocess | docker
OPENROUTER_API_KEY=              # ED-ALPHA scoring only
```

---

## Step-by-Step Build Order (India first, verify each gate)

### Step 0 — Scaffold + market router
**Gate:** `python -m pytest integrations/tests/test_company_research_market.py -v`

- [ ] Create `models.py`, `config.py`, `market.py`
- [ ] `detect_market("RELIANCE")` → `IN`
- [ ] `detect_market("RELIANCE.NS")` → `IN`
- [ ] `detect_market("AAPL")` → `US`
- [ ] `normalize_ticker` returns both OpenAlgo symbol and yfinance symbol

---

### Step 1 — India identity (OpenAlgo + dalal)
**Gate:** `python scripts/smoke_research_stage.py --stage identity_in --ticker RELIANCE`

- [ ] `sources/identity_in.py` — OpenAlgo quote when key set; dalal `meta()` / `quote()` fallback
- [ ] Returns `{name, sector, industry, exchange: "NSE", market_cap, last_price, currency: "INR"}`
- [ ] Graceful skip if OpenAlgo down (dalal-only)

---

### Step 2 — India calendar (india-corp-actions + nselib)
**Gate:** `python scripts/smoke_research_stage.py --stage calendar_in --ticker TCS --days 30`

- [ ] `pip install india-corp-actions nselib` in `[research]` extra
- [ ] `get_upcoming_results()`, `get_board_meetings()`, `get_actions_df(symbol=...)`
- [ ] nselib `event_calendar_for_equity(from_date, to_date)` as cross-check
- [ ] Merge + dedupe events into `calendar_events[]`

---

### Step 3 — India peers (nselib index constituents)
**Gate:** `python scripts/smoke_research_stage.py --stage peers_in --ticker RELIANCE`

- [ ] Map company → Nifty sector / industry via nselib or dalal
- [ ] Pull index constituents (e.g. Nifty 50, sectoral index) for peer list
- [ ] Return top 8 by market cap or index weight

---

### Step 4 — India fundamentals (dalal + nselib)
**Gate:** `python scripts/smoke_research_stage.py --stage fundamentals_in --ticker INFY`

- [ ] dalal `fundamentals()` — Revenue, PAT, EPS, OPM%, NPM%
- [ ] nselib `financial_results_for_equity()` — quarterly history
- [ ] PE from nselib `pe_ratio()` filtered to symbol

---

### Step 5 — India filings / announcements
**Gate:** `python scripts/smoke_research_stage.py --stage filings_in --ticker HDFCBANK`

- [ ] india-corp-actions `get_announcements(symbol)`
- [ ] dalal `announcements()`
- [ ] Last 10 corp announcements with dates

---

### Step 6 — India news (existing aggregator + RSS)
**Gate:** `python scripts/smoke_research_stage.py --stage news --ticker RELIANCE --market IN`

- [ ] Delegate to `news_aggregator` with ticker `RELIANCE.NS`
- [ ] Append RSS from `TRADINGAGENTS_SENTIMENT_RSS_FEEDS` (already India-heavy in your `.env`)
- [ ] Fetch news for top 3 peers

---

### Step 7 — SentimentPulse (in-process first)
**Gate:** `python scripts/smoke_research_stage.py --stage sentiment --text "Reliance Q1 profit beats estimates"`

- [ ] Vendor SentimentPulse repo to `vendor/SentimentPulse` OR pip install from git
- [ ] `clients/sentiment_pulse.py` — wrap `classify.py --text` or HTTP `:8081/classify`
- [ ] Score each news headline: `{label, score, entities[]}`
- [ ] Aggregate: `% positive / neutral / negative` for company + peers

---

### Step 8 — India macro (nselib VIX, FII/DII)
**Gate:** `python scripts/smoke_research_stage.py --stage macro_in`

- [ ] nselib `india_vix_data()`, FII/DII activity functions
- [ ] Polymarket macro topics (existing): "India RBI rate", "recession 2026"

---

### Step 9 — India full pipeline + CLI
**Gate:** `python scripts/run_company_research.py RELIANCE --days 14`

- [ ] `aggregator.py` runs IN stages 1–8 in order
- [ ] `format.py` markdown with India-specific sections (Results calendar, Board meetings, FII/DII)
- [ ] `--json` flag for structured output
- [ ] `python scripts/smoke_research_pipeline.py --market IN --ticker RELIANCE,TCS,INFY`

**✅ India milestone — do not start US until all 9 gates pass.**

---

### Step 10 — US identity + calendar
**Gate:** `python scripts/smoke_research_stage.py --stage identity_us --ticker AAPL`
**Gate:** `python scripts/smoke_research_stage.py --stage calendar_us --ticker AAPL --days 14`

- [ ] yfinance `.info` + `Calendars.get_earnings_calendar()`
- [ ] `earnings` package for confirmed dates + transcripts metadata
- [ ] EarningsPy `get_next_week_earnings()` filtered to ticker

---

### Step 11 — US peers (finvizfinance)
**Gate:** `python scripts/smoke_research_stage.py --stage peers_us --ticker TSLA`

- [ ] `finvizfinance(ticker).ticker_peer()`
- [ ] Sector screener compare table for top peers

---

### Step 12 — US fundamentals + SEC (edgartools)
**Gate:** `python scripts/smoke_research_stage.py --stage fundamentals_us --ticker MSFT`
**Gate:** `python scripts/smoke_research_stage.py --stage filings_us --ticker MSFT`

- [ ] edgartools latest 10-Q income statement + 8-K earnings releases
- [ ] `SEC_EDGAR_IDENTITY` required

---

### Step 13 — Finverse earnings surprise
**Gate:** `python scripts/smoke_research_stage.py --stage earnings_signal --ticker AAPL`

- [ ] `pip install finverse` in `[research-plus]` extra
- [ ] `earnings_surprise.analyze(data)` → beat probability, revision momentum
- [ ] Only runs for `market=US`; IN returns `skipped` with reason

---

### Step 14 — ED-ALPHA corporate event forecast
**Gate:** `docker compose -f stack/docker-compose.research.yml up -d ed-alpha-backend`
**Gate:** `python scripts/smoke_research_stage.py --stage corp_events --ticker AAPL`

- [ ] Clone ED-ALPHA to `vendor/ED-ALPHA` (git submodule)
- [ ] `clients/ed_alpha.py` — query predictions API
- [ ] Returns ranked 8-K event probabilities + supporting news signals
- [ ] US-only; requires OpenRouter key for live scoring (pre-computed batch OK for smoke)

---

### Step 15 — Alpha-Pulse 8-K parser (US)
**Gate:** `python scripts/smoke_research_stage.py --stage alpha_pulse --ticker AAPL`

- [ ] `clients/alpha_pulse.py` — subprocess `alpha-pulse/run-workflow` or import
- [ ] Structured JSON: event type, materiality, sentiment, expected flag
- [ ] Attach to `filings_us` stage enrichment

---

### Step 16 — US macro (FRED + Polymarket)
**Gate:** `python scripts/smoke_research_stage.py --stage macro_us`

- [ ] Existing FRED + Polymarket tools
- [ ] yfinance economic events calendar

---

### Step 17 — Dual-market full pipeline
**Gate:** `python scripts/smoke_research_pipeline.py --market IN,US --ticker RELIANCE,AAPL`

- [ ] `aggregator.py` routes per ticker market
- [ ] Single report can include both if passed `RELIANCE,AAPL` (batch mode)

---

### Step 18 — Agent integration (prefetch)
**Gate:** run TradingAgents on `RELIANCE` with research block in News Analyst prompt

- [ ] `tools/company_research_tools.py` — `@tool get_company_research`
- [ ] `register.py` — prefetch on stock tickers before graph run
- [ ] Patch News Analyst tools list

---

### Step 19 — SentimentPulse full Docker (optional upgrade)
**Gate:** `curl http://localhost:8081/health` + pipeline sentiment stage uses HTTP

- [ ] Full Kafka/TimescaleDB stack from SentimentPulse `docker-compose.yml`
- [ ] Switch `SENTIMENT_PULSE_MODE=docker`

---

### Step 20 — Batch: upcoming India results scan
**Gate:** `python scripts/run_company_research.py --upcoming-results --days 7 --market IN`

- [ ] Pull all `get_upcoming_results()` for next 7 days
- [ ] Run full pipeline for each (rate-limited, cached)

---

## pyproject.toml extras

```toml
[project.optional-dependencies]
research = [
    "nselib>=2.0",
    "india-corp-actions>=0.2.0",
    "dalal>=0.2.1",
    "finvizfinance>=1.3.0",
    "edgartools>=5.0.0",
    "earnings>=0.2",
    "earningspy>=0.1",
]
research-plus = [
    "trade-stack[research]",
    "finverse>=0.7",
]
research-ml = [
    # SentimentPulse — install from git submodule, not PyPI
    "transformers>=4.40",
    "torch>=2.0",
]
dev = ["pytest>=8.0"]
```

Install: `pip install -e ".[research,research-plus]"`

---

## Smoke script contract

```python
# scripts/smoke_research_stage.py
# Usage:
#   python scripts/smoke_research_stage.py --stage identity_in --ticker RELIANCE
#   python scripts/smoke_research_stage.py --stage calendar_us --ticker AAPL --days 14
#   python scripts/smoke_research_stage.py --stage sentiment --text "..."
#
# Exit 0 = stage ok or partial with data
# Exit 1 = stage error with no data
# Prints JSON StageResult to stdout
```

---

## Verification matrix (final)

| # | Command | Expected |
|---|---------|----------|
| 1 | `smoke_research_stage.py --stage identity_in --ticker RELIANCE` | name + NSE exchange |
| 2 | `smoke_research_stage.py --stage calendar_in --ticker TCS` | ≥1 upcoming event |
| 3 | `smoke_research_stage.py --stage peers_in --ticker RELIANCE` | ≥3 peers |
| 4 | `run_company_research.py RELIANCE --days 14` | full IN markdown |
| 5 | `smoke_research_stage.py --stage identity_us --ticker AAPL` | name + NASDAQ |
| 6 | `smoke_research_stage.py --stage corp_events --ticker AAPL` | ED-ALPHA predictions or graceful skip |
| 7 | `smoke_research_stage.py --stage sentiment --ticker RELIANCE` | scored headlines |
| 8 | `smoke_research_pipeline.py --market IN,US` | both markets pass |

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| NSE anti-bot blocks nselib | Session warmup, retries, fallback to india-corp-actions |
| OpenAlgo not running | dalal/yfinance `.NS` fallback for identity |
| ED-ALPHA needs Postgres + batch data | Submodule + compose; smoke uses pre-seeded demo or `skipped` until ingested |
| SentimentPulse model download slow | Cache `models/finbert-finetuned/` in vendor dir; in-process mode |
| Finverse US-only | Explicit `skipped` for IN with message |
| Finviz blocks scraping | US peers fallback to yfinance sector comparison |
| Rate limits on batch scan | `--max-tickers 20`, disk cache per stage (`~/.tradingagents/cache/research/`) |

---

## Execution handoff

**Plan v2 saved.** Build strictly in step order (0 → 9 India, then 10 → 17 US, then 18–20 integration).

**Recommended:** Subagent-driven — one step per session, run smoke gate, review, proceed.

**Start with Step 0** when ready to implement.
