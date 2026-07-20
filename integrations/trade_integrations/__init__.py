"""Trade-stack integrations layered on top of the TradingAgents submodule."""

import os

from trade_integrations.ml_runtime_env import prepare_yfinance_runtime

prepare_yfinance_runtime()

if os.environ.get("TRADE_INTEGRATIONS_SKIP_APPLY", "").strip().lower() not in (
    "1",
    "true",
    "yes",
):
    from trade_integrations.register import apply

    apply()
