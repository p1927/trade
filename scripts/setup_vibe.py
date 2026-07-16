#!/usr/bin/env python3
"""Sync Vibe Trading operator config for the trade stack."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STACK_VIBE = ROOT / "stack" / "vibe"
TEMPLATE = STACK_VIBE / "agent.json.template"
SKILLS_SRC = STACK_VIBE / "skills"

PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}

PROVIDER_BASE_URL_ENV = {
    "minimax": ("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
    "moonshot": ("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"),
    "deepseek": ("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
}


def _load_trade_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def vibe_home() -> Path:
    return Path(os.getenv("VIBE_TRADING_HOME", Path.home() / ".vibe-trading")).expanduser()


def hub_dir() -> Path:
    raw = os.getenv("TRADE_STACK_HUB_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (ROOT / "reports" / "hub").resolve()


def openalgo_mcp_wrapper() -> Path:
    """Return the stdio wrapper that launches mcpserver from openalgo/.venv."""
    return (ROOT / "scripts" / "run_openalgo_mcp.sh").resolve()


def verify_openalgo_mcp() -> tuple[bool, str]:
    """Check that OpenAlgo MCP can import its SDK from openalgo/.venv."""
    wrapper = openalgo_mcp_wrapper()
    if not wrapper.is_file():
        return False, f"Missing {wrapper.relative_to(ROOT)}"
    py = ROOT / "openalgo" / ".venv" / "bin" / "python"
    if not py.is_file():
        return (
            False,
            "Missing openalgo/.venv — run: cd openalgo && python3 -m venv .venv "
            "&& .venv/bin/pip install -r requirements.txt",
        )
    probe = (
        "import sys; sys.path.insert(0, '.'); "
        "from openalgo import api, ta; "
        "import pandas; import mcp"
    )
    import subprocess

    result = subprocess.run(
        [str(py), "-c", probe],
        cwd=ROOT / "openalgo",
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "import failed").strip().splitlines()[-1]
        return False, f"OpenAlgo MCP import check failed: {detail}"
    return True, "ok"


def _redact_agent_json(payload: dict) -> dict:
    """Return a copy safe for dry-run printing (API keys masked)."""
    import copy

    redacted = copy.deepcopy(payload)
    for server in (redacted.get("mcpServers") or {}).values():
        args = server.get("args") or []
        if len(args) >= 2 and isinstance(args[1], str) and args[1] not in ("REPLACE_ME", ""):
            args[1] = "***"
    return redacted


def render_agent_json() -> dict:
    api_key = os.getenv("OPENALGO_API_KEY", "").strip()
    host = (os.getenv("OPENALGO_HOST") or "http://127.0.0.1:5001").rstrip("/")
    if not api_key:
        print(
            "Warning: OPENALGO_API_KEY not set — OpenAlgo MCP will fail until configured.",
            file=sys.stderr,
        )
    template = TEMPLATE.read_text(encoding="utf-8")
    rendered = (
        template.replace("{{OPENALGO_MCP_WRAPPER}}", str(openalgo_mcp_wrapper()))
        .replace("{{OPENALGO_API_KEY}}", api_key or "REPLACE_ME")
        .replace("{{OPENALGO_HOST}}", host)
    )
    return json.loads(rendered)


def sync_agent_json(dry_run: bool = False) -> Path:
    target = vibe_home() / "agent.json"
    payload = render_agent_json()
    if dry_run:
        print(json.dumps(_redact_agent_json(payload), indent=2))
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def _render_skill_content(skill_path: Path) -> str:
    return skill_path.read_text(encoding="utf-8").replace(
        "{{TRADE_STACK_HUB_DIR}}", str(hub_dir())
    )


def sync_skills(dry_run: bool = False) -> list[Path]:
    """Sync stack/vibe/skills/* into ~/.vibe-trading/skills/user/<name>/."""
    user_skills = vibe_home() / "skills" / "user"
    written: list[Path] = []
    if not SKILLS_SRC.is_dir():
        return written
    for skill_dir in sorted(SKILLS_SRC.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        target_dir = user_skills / skill_dir.name
        target = target_dir / "SKILL.md"
        content = _render_skill_content(skill_md)
        if dry_run:
            print(f"# {skill_dir.name}\n{content[:200]}...\n")
            written.append(target)
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written


def sync_skill(dry_run: bool = False) -> Path:
    """Backward-compatible: return trade-stack skill path after full sync."""
    paths = sync_skills(dry_run=dry_run)
    for path in paths:
        if path.parent.name == "trade-stack":
            return path
    return vibe_home() / "skills" / "user" / "trade-stack" / "SKILL.md"


def _provider_from_trade_env() -> tuple[str, str, str]:
    provider = (os.getenv("TRADINGAGENTS_LLM_PROVIDER") or os.getenv("LANGCHAIN_PROVIDER") or "openai").strip().lower()
    model = (
        os.getenv("TRADINGAGENTS_QUICK_THINK_LLM")
        or os.getenv("LANGCHAIN_MODEL_NAME")
        or ""
    ).strip()
    key_env = PROVIDER_KEY_ENV.get(provider, "OPENAI_API_KEY")
    api_key = os.getenv(key_env, "").strip()
    if provider == "minimax" and not api_key:
        api_key = os.getenv("MINIMAX_CN_API_KEY", "").strip()
    return provider, model, api_key


def sync_vibe_env(dry_run: bool = False, force: bool = False) -> Path | None:
    target = vibe_home() / ".env"
    if target.is_file() and not force:
        return None

    provider, model, api_key = _provider_from_trade_env()
    lines = [
        "# Generated by scripts/setup_vibe.py from trade stack .env",
        f"LANGCHAIN_PROVIDER={provider}",
    ]
    if model:
        lines.append(f"LANGCHAIN_MODEL_NAME={model}")
    if api_key:
        key_env = PROVIDER_KEY_ENV.get(provider, "OPENAI_API_KEY")
        lines.append(f"{key_env}={api_key}")
    if provider in PROVIDER_BASE_URL_ENV:
        env_name, default_url = PROVIDER_BASE_URL_ENV[provider]
        base_url = os.getenv(env_name, "").strip() or default_url
        lines.append(f"{env_name}={base_url}")

    hub = hub_dir()
    allowed = [str(hub), str(ROOT / "reports"), str(ROOT)]
    lines.append(f"VIBE_TRADING_ALLOWED_FILE_ROOTS={','.join(allowed)}")
    lines.append(f"TRADE_STACK_HUB_DIR={hub}")

    openalgo_host = (os.getenv("OPENALGO_HOST") or "http://127.0.0.1:5001").rstrip("/")
    openalgo_key = os.getenv("OPENALGO_API_KEY", "").strip()
    lines.append(f"OPENALGO_HOST={openalgo_host}")
    if openalgo_key:
        lines.append(f"OPENALGO_API_KEY={openalgo_key}")

    if dry_run:
        print("\n".join(lines))
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure Vibe Trading for the trade stack")
    parser.add_argument("--dry-run", action="store_true", help="Print config without writing")
    parser.add_argument("--force-env", action="store_true", help="Overwrite ~/.vibe-trading/.env")
    parser.add_argument("--verify", action="store_true", help="Verify OpenAlgo MCP imports and exit")
    args = parser.parse_args()

    _load_trade_env()

    if args.verify:
        ok, message = verify_openalgo_mcp()
        if ok:
            print("OpenAlgo MCP: ok")
            return 0
        print(f"OpenAlgo MCP: {message}", file=sys.stderr)
        return 1

    agent_path = sync_agent_json(dry_run=args.dry_run)
    skill_paths = sync_skills(dry_run=args.dry_run)
    env_path = sync_vibe_env(dry_run=args.dry_run, force=args.force_env)

    if args.dry_run:
        return 0

    print(f"Wrote {agent_path}")
    for skill_path in skill_paths:
        print(f"Wrote {skill_path}")
    if env_path:
        print(f"Wrote {env_path}")
    else:
        print(f"Kept existing {vibe_home() / '.env'} (use --force-env to replace)")

    ok, message = verify_openalgo_mcp()
    if ok:
        print("OpenAlgo MCP: ok")
    else:
        print(f"Warning: OpenAlgo MCP not ready — {message}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
