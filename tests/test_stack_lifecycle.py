"""Stack lifecycle script behavior (return codes and orchestration)."""

from __future__ import annotations

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


def test_stack_searxng_enabled_helper():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        stack_searxng_enabled && echo yes || echo no
        """
    )
    assert proc.returncode == 0
    assert "yes" in proc.stdout


def test_stack_ensure_redis_returns_nonzero_when_compose_missing():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        stack_docker_compose_file() { echo /nonexistent/docker-compose.stack.yml; }
        stack_docker_available() { return 0; }
        stack_probe_redis() { return 1; }
        stack_redis_enabled() { return 0; }
        stack_ensure_redis_docker
        echo exit:$?
        """
    )
    assert proc.returncode == 0
    assert "exit:1" in proc.stdout


def test_stack_ensure_searxng_returns_nonzero_when_docker_down():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        stack_docker_available() { return 1; }
        stack_probe_searxng() { return 1; }
        stack_docker_compose_file() { echo "$PWD/docker-compose.stack.yml"; }
        stack_ensure_searxng
        echo exit:$?
        """
    )
    assert proc.returncode == 0
    assert "exit:1" in proc.stdout


def test_stack_hub_docker_required_when_searxng_on():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        export STACK_START_SEARXNG=1
        stack_load_env
        stack_hub_docker_required && echo required || echo skip
        """
    )
    assert proc.returncode == 0
    assert "required" in proc.stdout


def test_stack_bootstrap_session_writes_json():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        rm -f log/stack.session
        stack_bootstrap_session test clean
        test -f log/stack.session && grep -q session_id log/stack.session && echo ok
        """
    )
    assert proc.returncode == 0
    assert "ok" in proc.stdout
