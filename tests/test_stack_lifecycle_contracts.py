"""Stack lifecycle design contracts (bounded heal, no infinite loops, manifest)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_trade_heal_does_not_start_heal_daemon():
    ctl = (ROOT / "scripts" / "stack_ctl.sh").read_text()
    ensure_block = ctl.split("stack_ctl_ensure_inner")[1].split("stack_ctl_status")[0]
    assert "stack_start_heal_daemon" not in ensure_block


def test_stack_heal_max_attempts_default_is_two():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        unset STACK_HEAL_MAX_ATTEMPTS
        stack_heal_max_attempts
        """
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "2"


def test_stack_instance_json_written_by_manifest():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        mkdir -p log/claims
        rm -f log/stack.instance.json log/stack.instance
        stack_write_instance_manifest
        test -f log/stack.instance.json
        python3 - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("log/stack.instance.json").read_text())
assert data["probes"]["vibe_api"] == "/health"
assert data["tier_owner"] in {"off", "dev", "daemon"}
assert "owner" in data
assert "heal" in data
PY
        """
    )
    assert proc.returncode == 0, proc.stderr


def test_stack_status_json_includes_ready_and_live():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        source scripts/stack_deps.sh
        STACK_ROOT="$PWD"
        stack_load_env
        mkdir -p log/claims
        stack_write_instance_manifest
        stack_status_json > /tmp/stack_status_contract.json
        python3 - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("/tmp/stack_status_contract.json").read_text())
assert "services" in data
for svc in ("openalgo", "vibe_api", "vibe_ui"):
    row = data["services"][svc]
    assert "ready" in row
    assert "live" in row
    assert "probe" in row
assert data["probes"]["vibe_api"] == "/health"
PY
        """
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_python_load_stack_instance_manifest():
    manifest_path = ROOT / "log" / "stack.instance.json"
    if not manifest_path.is_file():
        proc = _bash(
            """
            source scripts/stack_lib.sh
            STACK_ROOT="$PWD"
            stack_load_env
            mkdir -p log/claims
            stack_write_instance_manifest
            """
        )
        assert proc.returncode == 0, proc.stderr
    proc = subprocess.run(
        ["python3", "-c", "from trade_integrations.env import load_stack_instance_manifest; m=load_stack_instance_manifest(); assert m.get('probes', {}).get('vibe_api') == '/health'; print('ok')"],
        cwd=str(ROOT),
        env={**dict(__import__("os").environ), "PYTHONPATH": str(ROOT / "integrations")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_restart_kill_dev_requires_force():
    proc = _bash("scripts/stack_ctl.sh restart --kill-dev 2>&1; echo exit:$?")
    assert "requires --force" in proc.stderr or "requires --force" in proc.stdout
    assert "exit:1" in proc.stdout


def test_hub_prelude_skipped_for_heal_command():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        source scripts/stack_deps.sh
        STACK_ROOT="$PWD"
        stack_load_env
        stack_ensure_dependencies() { echo ENSURE_HUB; return 1; }
        export -f stack_ensure_dependencies
        stack_maybe_heal_before_command heal 2>&1
        """
    )
    assert proc.returncode == 0
    assert "ENSURE_HUB" not in proc.stdout


def test_e2e_ensure_stack_healthy_has_no_heal_subprocess():
    text = (ROOT / "scripts" / "realistic_e2e_lib.py").read_text()
    block = text.split("def ensure_stack_healthy")[1].split("\ndef ")[0]
    assert '"heal"' not in block
    assert "/health" in block
