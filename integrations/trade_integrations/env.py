"""Load trade-stack .env from repo root (setdefault — never overrides existing env)."""

from __future__ import annotations

import os
from pathlib import Path


def trade_repo_root() -> Path:
    """Return monorepo root: integrations/trade_integrations/env.py -> parents[2]."""
    return Path(__file__).resolve().parents[2]


def load_stack_ports_env(*, root: Path | None = None) -> None:
    """Apply defaults from stack/ports.yaml (does not override existing env)."""
    base = root or trade_repo_root()
    try:
        from trade_integrations.stack_ports import build_env_map

        for key, value in build_env_map(root=base).items():
            os.environ.setdefault(key, value)
    except Exception:
        # Ports yaml / PyYAML may be unavailable during partial installs.
        return


def load_trade_env(*, root: Path | None = None) -> Path | None:
    """Load stack ports + ``{root}/.env`` into os.environ with setdefault."""
    base = root or trade_repo_root()
    load_stack_ports_env(root=base)
    env_file = base / ".env"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)
    return env_file


def openalgo_watch_quote_ttl_seconds() -> int:
    """Return hub channel WATCH policy TTL in seconds (``OPENALGO_WATCH_QUOTE_TTL_SECONDS``, default 5)."""
    try:
        return max(0, int(os.getenv("OPENALGO_WATCH_QUOTE_TTL_SECONDS", "5")))
    except ValueError:
        return 5


def ensure_openalgo_env(*, root: Path | None = None) -> dict[str, str]:
    """Load trade .env and return OpenAlgo host + api key (may be empty strings)."""
    load_trade_env(root=root)
    from trade_integrations.stack_ports import openalgo_host

    host = openalgo_host(root=root)
    api_key = os.getenv("OPENALGO_API_KEY", "").strip()
    return {"host": host, "api_key": api_key}


def _stack_auto_heal_enabled() -> bool:
    return os.getenv("STACK_AUTO_HEAL", "1").strip().lower() not in {"0", "false", "no", "off"}


def ensure_vibe_stack_heal(*, root: Path | None = None) -> bool:
    """Heal dead OpenAlgo/Vibe processes via ``trade restart`` when unreachable."""
    if not _stack_auto_heal_enabled():
        return False

    base = root or trade_repo_root()
    cfg = ensure_openalgo_env(root=base)
    host = cfg["host"].rstrip("/")

    try:
        import urllib.request

        with urllib.request.urlopen(f"{host}/", timeout=3) as resp:
            return 200 <= getattr(resp, "status", 200) < 500
    except Exception:
        pass

    trade_cli = base / "trade"
    if not trade_cli.is_file():
        return False

    import subprocess

    subprocess.run(
        [str(trade_cli), "heal"],
        cwd=str(base),
        timeout=180,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        import urllib.request

        with urllib.request.urlopen(f"{host}/", timeout=20) as resp:
            return 200 <= getattr(resp, "status", 200) < 500
    except Exception:
        return False
