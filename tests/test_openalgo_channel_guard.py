"""Guard: market-data reads must not bypass trade_integrations.openalgo + hub channel."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ALLOWED_OPENALGO_POST = {
    "integrations/trade_integrations/openalgo/market_data.py",
    "integrations/trade_integrations/dataflows/openalgo.py",
}

FORBIDDEN_SDK_MARKET_PATTERNS = (
    "client.optionchain(",
    "client.quotes(",
    "client.multiquotes(",
)


def test_no_direct_openalgo_post_outside_package():
    result = subprocess.run(
        ["rg", "_openalgo_post|openalgo_post\\(", "integrations/", "openalgo/mcp/", "-g", "*.py", "--no-heading"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    offenders = []
    for line in lines:
        path = line.split(":", 1)[0]
        norm = path.replace("\\", "/")
        if norm in ALLOWED_OPENALGO_POST:
            continue
        if "openalgo/market_data.py" in norm or "openalgo/rest_client.py" in norm:
            continue
        offenders.append(line)
    assert not offenders, "Direct openalgo_post outside package:\n" + "\n".join(offenders)


def test_mcp_market_tools_avoid_sdk_client_calls():
    mcpserver = (ROOT / "openalgo/mcp/mcpserver.py").read_text(encoding="utf-8")
    for fn in ("def get_quote(", "def get_multi_quotes(", "def get_option_chain(", "def get_options_browse("):
        start = mcpserver.find(fn)
        assert start >= 0, f"missing {fn}"
        next_def = mcpserver.find("\ndef ", start + 1)
        block = mcpserver[start:next_def] if next_def > start else mcpserver[start : start + 2000]
        for pattern in FORBIDDEN_SDK_MARKET_PATTERNS:
            assert pattern not in block, f"{fn} still uses {pattern}"


def test_chain_openalgo_uses_hub_fetch_option_chain():
    src = (
        ROOT / "integrations/trade_integrations/dataflows/options_research/sources/chain_openalgo.py"
    ).read_text(encoding="utf-8")
    assert "fetch_option_chain(" in src
    assert "fetch_option_chain_with_fallback(" not in src
