"""Propagate trade root .env to OpenAlgo, Vibe agent, and stack defaults."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from trade_integrations.stack_ports import build_env_map, trade_repo_root

GENERATED_HEADER = "# --- trade stack env sync (generated — do not edit) ---"
GENERATED_FOOTER = "# --- end trade stack env sync ---"


# Defaults applied to root .env when missing (setdefault — never overwrite user values).
ROOT_ENV_DEFAULTS: dict[str, str] = {
    "NAUTILUS_WATCH_ENABLE": "true",
    "TIMESCALE_ENABLED": "true",
    "OPENALGO_WS_ENABLED": "1",
    "OPENALGO_PAPER_MODE": "true",
    "VIBE_TRADING_ENABLE_SCHEDULER": "1",
    "INDEX_RESEARCH_ENABLE_SCHEDULER": "1",
    "INDEX_MONITOR_ENABLE_SCHEDULER": "1",
}

# Keys copied from root .env into openalgo/.env when present in root.
OPENALGO_ENV_KEYS: tuple[str, ...] = (
    "FLASK_PORT",
    "ZMQ_PORT",
    "WEBSOCKET_PORT",
    "WEBSOCKET_URL",
    "OPENALGO_HOST",
    "OPENALGO_WS_ENABLED",
    "OPENALGO_WS_URL",
    "OPENALGO_PAPER_MODE",
)

# Root key -> openalgo key aliases (both directions resolved from root source).
OPENALGO_KEY_ALIASES: dict[str, str] = {
    "OPENALGO_HOST": "HOST_SERVER",
}

# Keys copied from root .env into vibetrading/agent/.env when present in root.
VIBE_AGENT_ENV_KEYS: tuple[str, ...] = (
    "OPENALGO_HOST",
    "OPENALGO_API_KEY",
    "OPENALGO_PAPER_MODE",
    "VIBE_BACKEND_URL",
    "VIBE_BACKEND_PORT",
    "VIBE_FRONTEND_URL",
    "VIBE_FRONTEND_PORT",
    "NAUTILUS_REDIS_URL",
    "NAUTILUS_WATCH_ENABLE",
    "NAUTILUS_PYTHON",
    "NAUTILUS_AGENT_ID",
    "NAUTILUS_INSTANCE_ID",
    "TIMESCALE_ENABLED",
    "TIMESCALE_DATABASE_URL",
    "SEARXNG_BASE_URL",
    "TRADE_STACK_HUB_DIR",
    "TRADE_STACK_ROOT",
    "VIBE_TRADING_ENABLE_SCHEDULER",
    "INDEX_RESEARCH_ENABLE_SCHEDULER",
    "INDEX_MONITOR_ENABLE_SCHEDULER",
    "LANGCHAIN_PROVIDER",
    "LANGCHAIN_MODEL_NAME",
)

_LOOPBACK_HOST_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?/?$", re.I)


def _parse_dotenv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            out[key] = value
    return out


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return _parse_dotenv(path.read_text(encoding="utf-8"))


def _merge_dotenv_keys(
    path: Path,
    values: dict[str, str],
    keys: set[str],
    *,
    aliases: dict[str, str] | None = None,
) -> bool:
    """Merge keys into path; return True if file changed."""
    aliases = aliases or {}
    inverse_aliases = {dst: src for src, dst in aliases.items()}
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    out: list[str] = []
    seen: set[str] = set()

    def value_for_key(key: str) -> str | None:
        if key in values and key in keys:
            return values[key]
        src = inverse_aliases.get(key)
        if src and src in values and src in keys:
            if key == "HOST_SERVER" and not _should_sync_host_server(values.get(src, "")):
                return None
            return values[src]
        return None

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            new_val = value_for_key(key)
            if new_val is not None:
                out.append(f"{key}={new_val}")
                seen.add(key)
                continue
        out.append(line)

    for key in sorted(keys):
        if key in values and key not in seen:
            out.append(f"{key}={values[key]}")
            seen.add(key)
    for src, dst in aliases.items():
        if src not in values or src not in keys or dst in seen:
            continue
        if dst == "HOST_SERVER" and not _should_sync_host_server(values.get(src, "")):
            continue
        out.append(f"{dst}={values[src]}")
        seen.add(dst)

    new_text = "\n".join(out).rstrip() + "\n"
    if new_text != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
        return True
    return False


def _should_sync_host_server(openalgo_host: str) -> bool:
    """Only mirror OPENALGO_HOST -> HOST_SERVER for local loopback URLs."""
    return bool(openalgo_host and _LOOPBACK_HOST_RE.match(openalgo_host.rstrip("/")))


def _ensure_root_dotenv(root: Path) -> Path:
    dotenv = root / ".env"
    example = root / ".env.example"
    if not dotenv.is_file() and example.is_file():
        shutil.copyfile(example, dotenv)
    return dotenv


def _apply_root_defaults(root: Path) -> bool:
    dotenv = _ensure_root_dotenv(root)
    if not dotenv.is_file():
        return False
    text = dotenv.read_text(encoding="utf-8")
    parsed = _parse_dotenv(text)
    additions: list[str] = []
    for key, value in ROOT_ENV_DEFAULTS.items():
        if key not in parsed:
            additions.append(f"{key}={value}")
    if not additions:
        return False
    block = "\n# Defaults from trade stack setup\n" + "\n".join(additions) + "\n"
    dotenv.write_text(text.rstrip() + block, encoding="utf-8")
    return True


def _port_env_map(root: Path) -> dict[str, str]:
    return build_env_map(root=root)


def sync_stack_env_files(*, root: Path | None = None, apply: bool = True) -> dict[str, object]:
    """Sync root .env + ports.yaml into OpenAlgo and Vibe agent env files."""
    base = (root or trade_repo_root()).resolve()
    report: dict[str, object] = {"root": str(base), "updated": [], "created": []}

    if apply:
        _apply_root_defaults(base)

    root_env_path = base / ".env"
    root_values = _read_dotenv(root_env_path)
    root_values.update(_port_env_map(base))

    openalgo_env = base / "openalgo" / ".env"
    vibe_env = base / "vibetrading" / "agent" / ".env"

    if apply:
        if not openalgo_env.is_file():
            openalgo_env.write_text(
                "# OpenAlgo environment — port keys synced from trade root .env\n",
                encoding="utf-8",
            )
            report["created"].append(str(openalgo_env.relative_to(base)))

        if not vibe_env.is_file():
            example = base / "vibetrading" / "agent" / ".env.example"
            if example.is_file():
                shutil.copyfile(example, vibe_env)
                report["created"].append(str(vibe_env.relative_to(base)))

        openalgo_changed = _merge_dotenv_keys(
            openalgo_env,
            root_values,
            set(OPENALGO_ENV_KEYS) | set(_port_env_map(base)),
            aliases=OPENALGO_KEY_ALIASES,
        )
        vibe_changed = _merge_dotenv_keys(
            vibe_env,
            root_values,
            set(VIBE_AGENT_ENV_KEYS) | set(_port_env_map(base)),
        )
        if openalgo_changed:
            report["updated"].append(str(openalgo_env.relative_to(base)))
        if vibe_changed:
            report["updated"].append(str(vibe_env.relative_to(base)))

        root_changed = _merge_dotenv_keys(
            root_env_path,
            root_values,
            set(_port_env_map(base)),
        )
        if root_changed:
            report["updated"].append(str(root_env_path.relative_to(base)))

    report["root_keys"] = len(root_values)
    return report
