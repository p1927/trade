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
    return os.getenv("STACK_AUTO_HEAL", "0").strip().lower() not in {"0", "false", "no", "off"}


def _dev_mode_flagged(*, root: Path | None = None) -> bool:
    mode_file = (root or trade_repo_root()) / "log" / "stack.mode"
    if not mode_file.is_file():
        return False
    try:
        return mode_file.read_text(encoding="utf-8").strip() in {"dev", "booting"}
    except OSError:
        return False


def _probe_vibe_stack(*, root: Path) -> bool:
    """Return True when OpenAlgo and Vibe API respond."""
    cfg = ensure_openalgo_env(root=root)
    host = cfg["host"].rstrip("/")
    try:
        import urllib.request

        with urllib.request.urlopen(f"{host}/", timeout=3) as resp:
            if not (200 <= getattr(resp, "status", 200) < 500):
                return False
    except Exception:
        return False

    from trade_integrations.stack_ports import vibe_backend_url

    api_base = vibe_backend_url(root=root).rstrip("/")
    try:
        import urllib.request

        with urllib.request.urlopen(f"{api_base}/health", timeout=3) as resp:
            return 200 <= getattr(resp, "status", 200) < 500
    except Exception:
        return False


def ensure_vibe_stack_heal(*, root: Path | None = None) -> bool:
    """Probe OpenAlgo + Vibe API; optionally run one ``trade heal`` when STACK_AUTO_HEAL=1."""
    base = root or trade_repo_root()
    if _probe_vibe_stack(root=base):
        return True

    if not _stack_auto_heal_enabled() or _dev_mode_flagged(root=base):
        return False

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
    return _probe_vibe_stack(root=base)


class StackUnavailableError(RuntimeError):
    """Raised when the Vibe stack is down and auto-heal is disabled."""


def stack_unavailable_message(*, root: Path | None = None) -> str:
    base = root or trade_repo_root()
    return (
        "Vibe stack is not reachable (OpenAlgo and/or Vibe API /health failed).\n"
        f"  Fix: cd {base} && trade doctor && trade up\n"
        "  Or dev mode: trade dev\n"
        "  Status: trade status --json\n"
        "  Auto-heal is off by default (STACK_AUTO_HEAL=0)."
    )


def load_stack_instance_manifest(*, root: Path | None = None) -> dict:
    """Read ``log/stack.instance.json`` written by ``stack_write_instance_manifest``."""
    path = (root or trade_repo_root()) / "log" / "stack.instance.json"
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except OSError:
        return {}


def require_vibe_stack(*, root: Path | None = None) -> None:
    """Raise ``StackUnavailableError`` when the stack is not ready and auto-heal is off."""
    base = root or trade_repo_root()
    if ensure_vibe_stack_heal(root=base):
        return
    raise StackUnavailableError(stack_unavailable_message(root=base))
