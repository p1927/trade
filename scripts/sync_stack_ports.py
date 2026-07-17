#!/usr/bin/env python3
"""Sync stack/ports.yaml into .stack.ports.env, root .env, and openalgo/.env port keys."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.stack_ports import (  # noqa: E402
    build_env_map,
    check_port_listeners,
    ports_yaml_path,
    validate_ports,
)

GENERATED_HEADER = "# --- stack/ports.yaml (generated — do not edit) ---"
GENERATED_FOOTER = "# --- end stack/ports.yaml ---"


def _render_env_file(env_map: dict[str, str]) -> str:
    lines = [
        GENERATED_HEADER,
        f"# Source: {ports_yaml_path(root=ROOT).relative_to(ROOT)}",
        "# Regenerate: python scripts/sync_stack_ports.py --apply",
        "",
    ]
    for key in sorted(env_map):
        lines.append(f"{key}={env_map[key]}")
    lines.extend(["", GENERATED_FOOTER, ""])
    return "\n".join(lines)


def _merge_generated_block(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    pattern = re.compile(
        rf"{re.escape(GENERATED_HEADER)}.*?{re.escape(GENERATED_FOOTER)}\n?",
        re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n" + block
    path.write_text(text, encoding="utf-8")


def _update_dotenv_keys(path: Path, env_map: dict[str, str], keys: set[str]) -> None:
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in keys and key in env_map:
                out.append(f"{key}={env_map[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key in sorted(keys):
        if key in env_map and key not in seen:
            out.append(f"{key}={env_map[key]}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _shell_export(env_map: dict[str, str]) -> None:
    for key in sorted(env_map):
        val = env_map[key].replace("'", "'\\''")
        print(f"export {key}='{val}'")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync stack/ports.yaml to env files")
    parser.add_argument("--check", action="store_true", help="Validate port map only")
    parser.add_argument(
        "--check-listeners",
        action="store_true",
        help="Fail if registry ports are held by unexpected processes",
    )
    parser.add_argument("--apply", action="store_true", help="Write .stack.ports.env and merge .env files")
    parser.add_argument("--write-env", action="store_true", help="Write .stack.ports.env only")
    parser.add_argument("--shell", action="store_true", help="Print shell export statements")
    args = parser.parse_args()

    errors = validate_ports(root=ROOT)
    if errors:
        for err in errors:
            print(f"port conflict: {err}", file=sys.stderr)
        return 1

    if args.check_listeners:
        listener_errors = check_port_listeners(root=ROOT)
        if listener_errors:
            for err in listener_errors:
                print(f"port in use: {err}", file=sys.stderr)
            return 1
        print("stack port listeners OK")
        return 0

    env_map = build_env_map(root=ROOT)

    if args.check and not (args.apply or args.write_env or args.shell):
        print("stack ports OK")
        return 0

    if args.shell:
        _shell_export(env_map)
        return 0

    block = _render_env_file(env_map)

    if args.write_env or args.apply:
        out_path = ROOT / ".stack.ports.env"
        out_path.write_text(block, encoding="utf-8")
        print(f"wrote {out_path.relative_to(ROOT)}")

    if args.apply:
        example = ROOT / ".env.example"
        if example.is_file():
            _merge_generated_block(example, block)
            print(f"merged generated block into {example.relative_to(ROOT)}")

        dotenv = ROOT / ".env"
        if dotenv.is_file():
            _update_dotenv_keys(dotenv, env_map, set(env_map))
            print(f"updated port keys in {dotenv.relative_to(ROOT)}")

        openalgo_env = ROOT / "openalgo" / ".env"
        openalgo_keys = {
            "FLASK_PORT",
            "ZMQ_PORT",
            "WEBSOCKET_PORT",
            "WEBSOCKET_URL",
            "OPENALGO_HOST",
        }
        if openalgo_env.is_file():
            _update_dotenv_keys(openalgo_env, env_map, openalgo_keys)
            print(f"updated OpenAlgo port keys in {openalgo_env.relative_to(ROOT)}")

        vibe_env = ROOT / "vibetrading" / "agent" / ".env"
        vibe_keys = {
            "OPENALGO_HOST",
            "VIBE_BACKEND_URL",
            "NAUTILUS_REDIS_URL",
        }
        if vibe_env.is_file():
            _update_dotenv_keys(vibe_env, env_map, vibe_keys)
            print(f"updated Vibe agent port keys in {vibe_env.relative_to(ROOT)}")

    if not any((args.check, args.apply, args.write_env, args.shell)):
        parser.print_help()
        return 0

    if args.check:
        print("stack ports OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
