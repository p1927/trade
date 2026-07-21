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


def test_searxng_probe_ignores_unrelated_unresponsive_engines():
    proc = _bash(
        """
        source scripts/stack_docker_lib.sh
        echo '{"unresponsive_engines":[["mojeek","403"],["qwant","CAPTCHA"]],"results":[]}' \\
          | _searxng_probe_check_unresponsive bing
        echo exit:$?
        """
    )
    assert proc.returncode == 0
    assert "exit:0" in proc.stdout


def test_searxng_probe_fails_when_required_engine_unresponsive():
    proc = _bash(
        """
        source scripts/stack_docker_lib.sh
        echo '{"unresponsive_engines":[["bing","CAPTCHA"]],"results":[]}' \\
          | _searxng_probe_check_unresponsive bing 2>/dev/null
        echo exit:$?
        """
    )
    assert proc.returncode == 0
    assert "exit:1" in proc.stdout


def test_searxng_probe_engine_match_is_case_insensitive():
    proc = _bash(
        """
        source scripts/stack_docker_lib.sh
        echo '{"unresponsive_engines":[["Bing","CAPTCHA"]],"results":[]}' \\
          | _searxng_probe_check_unresponsive bing 2>/dev/null
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


def test_stack_command_needs_heal_excludes_status():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_command_needs_heal status && echo heal || echo noheal
        stack_command_needs_heal reload && echo reload_heal || echo reload_noheal
        stack_command_is_status status && echo is_status || echo not_status
        """
    )
    assert proc.returncode == 0
    assert "noheal" in proc.stdout
    assert "reload_heal" in proc.stdout
    assert "is_status" in proc.stdout


def test_stack_reconcile_for_status_omits_scheduler_boot():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        stack_recover_stale_scheduler_jobs() { echo SCHEDULER_BOOT; }
        stack_reconcile_for_status 2>/dev/null
        """
    )
    assert proc.returncode == 0
    assert "SCHEDULER_BOOT" not in proc.stdout


def test_stack_status_does_not_start_services():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        mkdir -p log/claims
        stack_start_nautilus_watch() { echo STARTED_NAUTILUS; return 1; }
        stack_ensure_dependencies() { echo STARTED_ENSURE; return 1; }
        stack_ensure_hub_docker() { echo STARTED_HUB; return 1; }
        export -f stack_start_nautilus_watch stack_ensure_dependencies stack_ensure_hub_docker
        stack_status_vibe_stack 2>&1 | tee /tmp/stack_status_test.out
        if grep -q STARTED /tmp/stack_status_test.out; then exit 1; fi
        echo ok
        """
    )
    assert proc.returncode == 0
    assert "ok" in proc.stdout
    assert "STARTED" not in proc.stdout


def test_stack_cleanup_after_stop_removes_stale_pidfiles():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        mkdir -p log/claims
        echo 99999 > log/openalgo.pid
        echo 99998 > log/vibe-api.pid
        echo pid=99999 > log/claims/openalgo.claim
        stack_cleanup_after_stop 0
        test ! -f log/openalgo.pid
        test ! -f log/vibe-api.pid
        test ! -f log/claims/openalgo.claim
        echo ok
        """
    )
    assert proc.returncode == 0
    assert "ok" in proc.stdout


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
