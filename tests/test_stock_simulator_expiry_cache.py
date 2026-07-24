"""Replay-aware expiry filtering for stock_simulator symtoken cache."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OPENALGO = REPO / "openalgo"


def test_expiry_cache_replay_anchor_subprocess():
    env = {
        **os.environ,
        "STOCK_SIMULATOR_MODE": "replay",
        "NSE_REPLAY_DATE": "2024-04-15",
        "NSE_REPLAY_WEEK_MODE": "0",
    }
    script = """
import broker.stock_simulator.api._trade_path as _tp
_tp.hydrate_simulator_env_from_db = lambda: None
from services.expiry_service import _expiry_reference_date
from database.token_db_enhanced import get_cache, get_distinct_expiries_cached

assert _expiry_reference_date(None).isoformat() == "2024-04-15"

cache = get_cache()
cache.cache_loaded = True
cache._cache_valid = True
cache.expiries_by_exchange_underlying = {
    ("NFO", "NIFTY"): {"18-APR-24", "25-APR-24", "01-JAN-20"},
}
cache.expiries_by_exchange = {"NFO": {"18-APR-24", "25-APR-24", "01-JAN-20"}}

expiries = get_distinct_expiries_cached(exchange="NFO", underlying="NIFTY")
assert "18-APR-24" in expiries
assert "25-APR-24" in expiries
assert "01-JAN-20" not in expiries
print("ok")
"""
    proc = subprocess.run(
        ["uv", "run", "python3", "-c", script],
        cwd=OPENALGO,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
