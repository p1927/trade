"""Read self-hosted Skyvern local credentials and sync MiniMax env for Docker."""

from __future__ import annotations

import os
import re
from pathlib import Path

_CRED_RE = re.compile(r"""cred\s*=\s*["']([^"']+)["']""")


def repo_root() -> Path:
    raw = os.environ.get("TRADE_STACK_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def credentials_file_paths() -> list[Path]:
    root = repo_root()
    return [
        root / ".skyvern-data" / ".skyvern" / "credentials.toml",
        root / ".skyvern" / "credentials.toml",
    ]


def read_local_skyvern_api_key() -> str:
    """Parse auto-generated cred from self-hosted Skyvern credentials.toml."""
    for path in credentials_file_paths():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        match = _CRED_RE.search(text)
        if match:
            return match.group(1).strip()
    return ""


def resolve_minimax_api_key() -> str:
    return (
        os.environ.get("MINIMAX_API_KEY", "").strip()
        or os.environ.get("MINIMAX_CN_API_KEY", "").strip()
    )


def resolve_minimax_base_url() -> str:
    return (
        os.environ.get("MINIMAX_BASE_URL", "").strip().rstrip("/")
        or "https://api.minimax.io/v1"
    )


def resolve_minimax_model() -> str:
    return os.environ.get("NSE_BROWSER_AGENT_MODEL", "").strip() or "MiniMax-M3"
