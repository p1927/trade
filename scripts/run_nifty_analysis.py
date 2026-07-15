#!/usr/bin/env python3
"""Run a TradingAgents analysis using live OpenAlgo data."""

from __future__ import annotations

import copy
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tradingagents"))

# Load trade .env
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))

import trade_integrations  # noqa: F401 — register OpenAlgo + news integrations

import tradingagents.default_config as default_config
from tradingagents.dataflows.config import set_config
from tradingagents.graph.trading_graph import TradingAgentsGraph

TICKER = os.environ.get("TRADINGAGENTS_TICKER", "^NSEI")
ANALYSIS_DATE = os.environ.get("TRADINGAGENTS_ANALYSIS_DATE", datetime.now().strftime("%Y-%m-%d"))

config = copy.deepcopy(default_config.DEFAULT_CONFIG)
config["data_vendors"]["core_stock_apis"] = os.environ.get(
    "TRADINGAGENTS_CORE_STOCK_DATA_VENDOR", "openalgo,yfinance"
)
config["data_vendors"]["technical_indicators"] = os.environ.get(
    "TRADINGAGENTS_TECHNICAL_INDICATORS_DATA_VENDOR", "openalgo,yfinance"
)
config["data_vendors"]["news_data"] = os.environ.get("TRADINGAGENTS_NEWS_DATA_VENDOR", "aggregated")
config["max_debate_rounds"] = int(os.environ.get("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1"))
config["max_risk_discuss_rounds"] = int(os.environ.get("TRADINGAGENTS_MAX_RISK_ROUNDS", "1"))
set_config(config)

print(f"=== Nifty 50 analysis via OpenAlgo + TradingAgents ===")
print(f"Ticker: {TICKER}")
print(f"Date:   {ANALYSIS_DATE}")
print(f"LLM:    {config['llm_provider']} / {config['quick_think_llm']}")
print(f"Data:   {config['data_vendors']['core_stock_apis']}")
print()

# Index: skip fundamentals (not meaningful for NIFTY index)
selected_analysts = ("market", "social", "news")

graph = TradingAgentsGraph(
    selected_analysts=selected_analysts,
    debug=True,
    config=config,
)

print("Starting multi-agent analysis (this may take several minutes)...")
final_state, decision = graph.propagate(TICKER, ANALYSIS_DATE, asset_type="stock")

reports_dir = Path(config["results_dir"]) / TICKER.replace("^", "") / ANALYSIS_DATE
reports_dir.mkdir(parents=True, exist_ok=True)

from tradingagents.reporting import write_report_tree

report_path = write_report_tree(final_state, TICKER, reports_dir)
print()
print("=== Final decision ===")
print(decision)
print()
print(f"Report saved to: {report_path}")
