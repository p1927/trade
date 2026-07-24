"""Regression tests for stack preflight health probes."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_preflight_skip_uses_vibe_health_not_root():
    """Preflight must probe /health — Vibe API root returns 404 when healthy."""
    root = Path(__file__).resolve().parents[1]
    stack_lib = root / "scripts" / "stack_lib.sh"
    text = stack_lib.read_text()
    preflight_block = text.split("stack_preflight_start")[1].split("stack_ensure_vibe_stack")[0]
    assert "stack_vibe_api_http_ok" in preflight_block
    assert 'stack_http_ok "http://127.0.0.1:${api_port}/"' not in preflight_block


def test_stack_lib_bash_syntax():
    root = Path(__file__).resolve().parents[1]
    for script in ("scripts/stack_lib.sh", "scripts/stack_ctl.sh", "exposure/lib/common.sh"):
        path = root / script
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_stack_start_vibe_api_uses_health_not_root():
    root = Path(__file__).resolve().parents[1]
    stack_lib = (root / "scripts" / "stack_lib.sh").read_text()
    block = stack_lib.split("stack_start_vibe_api")[1].split("stack_start_vibe_ui")[0]
    assert 'stack_http_ok "$base/"' not in block
    assert "$base/health" in block
    assert "stack_vibe_api_http_ok" in block


def test_stack_reconcile_stale_claims_vibe_api_uses_health():
    root = Path(__file__).resolve().parents[1]
    stack_lib = (root / "scripts" / "stack_lib.sh").read_text()
    block = stack_lib.split("stack_reconcile_stale_claims")[1].split("stack_service_for_pid")[0]
    assert '"$service" == "vibe-api"' in block
    assert "stack_vibe_api_http_ok" in block


def test_stack_heal_daemon_disabled_by_default():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            "bash",
            "-c",
            """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        unset STACK_HEAL_DAEMON
        stack_heal_daemon_enabled && echo enabled || echo disabled
        """,
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "disabled" in proc.stdout


def test_stack_lib_has_no_infinite_heal_loops():
    root = Path(__file__).resolve().parents[1]
    stack_lib = (root / "scripts" / "stack_lib.sh").read_text()
    assert "while true; do" not in stack_lib.lower()
    assert "stack_heal_max_attempts" in stack_lib
    assert "stack_heal_fail_loud" in stack_lib


def test_stack_wait_max_attempts_default_is_bounded():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            "bash",
            "-c",
            """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        unset STACK_WAIT_MAX_ATTEMPTS
        stack_wait_max_attempts
        """,
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "15"
