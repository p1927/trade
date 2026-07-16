#!/usr/bin/env python3
"""End-to-end verification for RELIANCE unified research → widget pipeline."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))
sys.path.insert(0, str(ROOT / "tradingagents"))

import os

os.environ.setdefault("TRADE_INTEGRATIONS_SKIP_APPLY", "1")
os.environ.setdefault("OPENALGO_MCP_HTTP_BOOT", "1")
if not os.getenv("TRADE_STACK_HUB_DIR"):
    os.environ["TRADE_STACK_HUB_DIR"] = str(ROOT / "reports" / "hub")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def _is_hold_recommendation(rec: dict) -> bool:
    return str(rec.get("action") or "").upper() == "HOLD" or rec.get("name") == "hold_cash"


def _load_mcp_module():
    """Load mcpserver without requiring pip-installed openalgo SDK."""
    import importlib.util

    if "openalgo" not in sys.modules:
        stub = types.ModuleType("openalgo")
        stub.api = lambda **kwargs: None
        stub.ta = types.ModuleType("openalgo.ta")
        sys.modules["openalgo"] = stub

    mcpserver_path = ROOT / "openalgo" / "mcp" / "mcpserver.py"
    spec = importlib.util.spec_from_file_location("openalgo_mcpserver_verify", mcpserver_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {mcpserver_path}")
    mcp_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp_mod)
    api_key = os.getenv("OPENALGO_API_KEY", "")
    host = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    if api_key and hasattr(mcp_mod, "init_for_http"):
        mcp_mod.init_for_http(api_key, host)
    return mcp_mod


def main() -> int:
    failures = 0

    from trade_integrations.research.orchestrator import ensure_research_complete, get_research_status
    from trade_integrations.research.registry import ResearchKind
    from trade_integrations.dataflows.stock_research.widget_payload import build_stock_trade_widget
    from trade_integrations.trade_widgets.presentability import is_widget_presentable

    print("=== RELIANCE research orchestrator ===")
    result = ensure_research_complete(
        "RELIANCE",
        kind=ResearchKind.STOCK,
        refresh=True,
        horizon_days=1,
        require_debate=False,
    )
    if not _check("orchestrator completes", result.status == "complete", f"status={result.status}, missing={result.missing}"):
        failures += 1
    if result.doc:
        pred = result.doc.prediction or {}
        rec = result.doc.recommended or {}
        ch = result.doc.charges or {}
        hold = _is_hold_recommendation(rec)
        failures += not _check("prediction.range", bool(pred.get("range", {}).get("low") and pred.get("range", {}).get("high")))
        failures += not _check("prediction.provenance", bool(pred.get("provenance")))
        if hold:
            failures += not _check("recommended hold_cash", rec.get("name") == "hold_cash", str(rec.get("name")))
            failures += not _check("recommended.max_profit == 0 for HOLD", rec.get("max_profit") == 0, str(rec.get("max_profit")))
            failures += not _check("recommended.max_loss == 0 for HOLD", rec.get("max_loss") == 0, str(rec.get("max_loss")))
        else:
            failures += not _check("recommended.max_profit > 0 for BUY", (rec.get("max_profit") or 0) > 0, str(rec.get("max_profit")))
            failures += not _check("recommended.max_loss < 0 for BUY", (rec.get("max_loss") or 0) < 0, str(rec.get("max_loss")))
            failures += not _check("recommended.legs", bool(rec.get("legs")))
        failures += not _check("charges.round_trip", (ch.get("round_trip_charges") or 0) >= 0, str(ch.get("round_trip_charges")))
        failures += not _check("charges.broker indmoney", ch.get("broker_preset") == "indmoney", str(ch.get("broker_preset")))

    print("\n=== get_research_status ===")
    status = get_research_status("RELIANCE", kind=ResearchKind.STOCK)
    failures += not _check("research status kind", status.get("kind") == "stock")
    failures += not _check("research status not error", status.get("status") in ("complete", "partial", "incomplete"))

    print("\n=== widget build + presentability ===")
    widget = build_stock_trade_widget("RELIANCE", refresh=False, widget_intent="stock_trade")
    failures += not _check("widget type", widget.get("type") == "trade_plan.widget")
    failures += not _check("widget asset stock", widget.get("asset_type") == "stock")
    w_pred = widget.get("prediction") or {}
    failures += not _check("widget prediction range", bool(w_pred.get("range")))
    w_rec = widget.get("recommended") or {}
    failures += not _check("widget recommended max_profit", w_rec.get("max_profit") is not None)
    w_ch = widget.get("charges") or {}
    failures += not _check("widget round_trip_charges", w_ch.get("round_trip_charges") is not None)
    presentable = is_widget_presentable(widget, "stock_trade")
    failures += not _check("widget presentable gate", presentable, f"plan_status={widget.get('plan_status')}")

    print("\n=== MCP tool simulation ===")
    try:
        mcp_mod = _load_mcp_module()
        mcp_status_out = json.loads(mcp_mod.get_research_status("RELIANCE", asset_type="stock"))
        failures += not _check("MCP get_research_status", mcp_status_out.get("kind") == "stock")

        mcp_widget_out = json.loads(mcp_mod.get_stock_trade_widget("RELIANCE", refresh=False, lookahead_days=1))
        failures += not _check("MCP get_stock_trade_widget", mcp_widget_out.get("underlying") == "RELIANCE")
        failures += not _check(
            "MCP widget has payoff fields",
            (mcp_widget_out.get("recommended") or {}).get("max_profit") is not None,
        )
        failures += not _check(
            "MCP widget presentable fields",
            bool((mcp_widget_out.get("prediction") or {}).get("provenance")),
        )
    except Exception as exc:
        failures += not _check("MCP tool calls", False, str(exc))

    print(f"\n=== Summary: {failures} failure(s) ===")
    if failures == 0:
        print(json.dumps({
            "spot": widget.get("spot"),
            "prediction": widget.get("prediction"),
            "recommended": {
                k: w_rec.get(k)
                for k in ("name", "action", "target", "stop", "max_profit", "max_loss", "net_max_profit", "net_max_loss")
            },
            "charges": {
                "round_trip_charges": w_ch.get("round_trip_charges"),
                "broker_preset": w_ch.get("broker_preset"),
                "per_leg_sample": (w_ch.get("per_leg") or [{}])[0],
            },
        }, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
