#!/usr/bin/env python3
"""Verify Nautilus ↔ OpenAlgo bridge toolchain (M0)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


def _check_python_version() -> tuple[bool, str]:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 12):
        return True, f"{major}.{minor}"
    return False, f"{major}.{minor} (need 3.12+ for Nautilus TradingNode)"


def _check_nautilus_import(*, venv_python: Path | None) -> tuple[bool, str]:
    py = venv_python if venv_python and venv_python.is_file() else Path(sys.executable)
    import subprocess

    try:
        out = subprocess.check_output(
            [str(py), "-c", "import nautilus_trader; print(nautilus_trader.__version__)"],
            text=True,
            timeout=30,
        ).strip()
        return True, out or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _check_bridge_models() -> tuple[bool, str]:
    try:
        from nautilus_openalgo_bridge.models import ExecutionIntent, PositionHandoff, WatchSpec

        spec = WatchSpec.from_dict({"rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5}]})
        handoff = PositionHandoff(agent_id="aa_test", widget_id=None, underlying="NIFTY", legs=[], entry_spot=0.0)
        intent = ExecutionIntent.from_dict({"action": "HOLD", "agent_id": "aa_test", "rationale": "probe"})
        _ = spec.to_dict(), handoff.to_dict(), intent.to_dict()
        return True, "models ok"
    except Exception as exc:
        return False, str(exc)


def _check_openalgo(host: str) -> tuple[bool, str]:
    try:
        from nautilus_openalgo_bridge.openalgo_client import BridgeOpenAlgoClient

        client = BridgeOpenAlgoClient(host=host.rstrip("/"), api_key=os.getenv("OPENALGO_API_KEY", ""))
        funds = client.get_funds()
        if isinstance(funds, dict):
            return True, "funds ok"
        return True, "reachable"
    except Exception as exc:
        return False, str(exc)


def _check_vibe(url: str) -> tuple[bool, str]:
    try:
        from nautilus_openalgo_bridge.vibe_trigger import ping_vibe_backend
        from nautilus_openalgo_bridge.config import BridgeConfig

        cfg = BridgeConfig(vibe_backend_url=url.rstrip("/"))
        result = ping_vibe_backend(cfg)
        status = str(result.get("status") or "unknown")
        if status in {"ok", "auth_required"}:
            return True, status
        return False, json.dumps(result)[:200]
    except Exception as exc:
        return False, str(exc)


def _check_redis() -> tuple[bool, str]:
    url = os.getenv("NAUTILUS_REDIS_URL", "redis://127.0.0.1:6379/0").strip()
    if not url:
        return True, "not configured"
    try:
        import redis

        client = redis.from_url(url, socket_connect_timeout=3)
        client.ping()
        return True, "PONG"
    except Exception as exc:
        return False, str(exc)


def _check_timescale() -> tuple[bool, str]:
    if os.getenv("TIMESCALE_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return True, "disabled"
    try:
        from trade_integrations.hub_storage.timescale_ticks import timescale_health

        health = timescale_health()
        if health.get("ok"):
            return True, "ok"
        return False, str(health.get("error") or health)
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Nautilus bridge toolchain")
    parser.add_argument("--skip-openalgo", action="store_true")
    parser.add_argument("--skip-vibe", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-redis", action="store_true")
    parser.add_argument("--skip-timescale", action="store_true")
    args = parser.parse_args()

    venv_py = ROOT / ".venv-nautilus" / "bin" / "python"
    host = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    vibe = os.getenv("VIBE_BACKEND_URL", "http://127.0.0.1:8899")

    checks: dict[str, dict[str, str | bool]] = {}

    ok, detail = _check_python_version()
    checks["python"] = {"ok": ok, "detail": detail}

    ok, detail = _check_bridge_models()
    checks["bridge_models"] = {"ok": ok, "detail": detail}

    ok, detail = _check_nautilus_import(venv_python=venv_py if venv_py.is_file() else None)
    checks["nautilus_trader"] = {"ok": ok, "detail": detail}
    if venv_py.is_file():
        checks["nautilus_venv"] = {"ok": True, "detail": str(venv_py)}
    else:
        checks["nautilus_venv"] = {"ok": False, "detail": "missing — run ./scripts/setup_nautilus.sh"}

    if not args.skip_redis and os.getenv("NAUTILUS_REDIS_URL", "").strip():
        ok, detail = _check_redis()
        checks["redis"] = {"ok": ok, "detail": detail, "url": os.getenv("NAUTILUS_REDIS_URL", "")}

    if not args.skip_timescale:
        ok, detail = _check_timescale()
        checks["timescale"] = {"ok": ok, "detail": detail}

    if not args.skip_openalgo:
        ok, detail = _check_openalgo(host)
        checks["openalgo"] = {"ok": ok, "detail": detail, "host": host}

    if not args.skip_vibe:
        ok, detail = _check_vibe(vibe)
        checks["vibe"] = {"ok": ok, "detail": detail, "url": vibe}

    all_ok = all(bool(row.get("ok")) for row in checks.values())

    if args.json:
        print(json.dumps({"ok": all_ok, "checks": checks}, indent=2))
    else:
        print("Nautilus bridge toolchain verification")
        print("=" * 40)
        for name, row in checks.items():
            mark = "OK" if row.get("ok") else "FAIL"
            extra = f" ({row['host']})" if "host" in row else (f" ({row['url']})" if "url" in row else "")
            print(f"  [{mark}] {name}{extra}: {row.get('detail')}")
        print("=" * 40)
        print("PASS" if all_ok else "FAIL")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
