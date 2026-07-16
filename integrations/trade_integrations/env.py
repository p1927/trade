"""Load trade-stack .env from repo root (setdefault — never overrides existing env)."""

from __future__ import annotations

import os
from pathlib import Path


def trade_repo_root() -> Path:
    """Return monorepo root: integrations/trade_integrations/env.py -> parents[2]."""
    return Path(__file__).resolve().parents[2]


def load_trade_env(*, root: Path | None = None) -> Path | None:
    """Load ``{root}/.env`` into os.environ with setdefault."""
    base = root or trade_repo_root()
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
