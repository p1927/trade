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
