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


def test_stack_searxng_http_ok_includes_real_ip_header():
    proc = _bash(
        """
        source scripts/stack_docker_lib.sh
        declare -f stack_searxng_http_ok
        """
    )
    assert proc.returncode == 0
    assert "X-Real-IP" in proc.stdout


def test_searxng_remediation_hint_dns():
    proc = _bash(
        """
        source scripts/stack_docker_lib.sh
        _searxng_remediation_hint "bing: requests exception [Errno -2] Name or service not known" 1>&2
        """
    )
    assert proc.returncode == 0
    assert "DNS failed" in proc.stderr
    assert "docker-compose.stack.yml" in proc.stderr


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


def test_stack_nautilus_registry_has_agents_false_when_empty():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        mkdir -p log
        echo '{"agents":[],"node_pid":null}' > log/nautilus-watch.agents.json
        stack_nautilus_registry_has_agents && echo has || echo no
        """
    )
    assert proc.returncode == 0
    assert "no" in proc.stdout


def test_stack_nautilus_registry_has_agents_false_for_blank_agent_rows():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        mkdir -p log
        echo '{"agents":[{}],"node_pid":null}' > log/nautilus-watch.agents.json
        stack_nautilus_registry_has_agents && echo has || echo no
        """
    )
    assert proc.returncode == 0
    assert "no" in proc.stdout


def test_stack_nautilus_registry_has_agents_true_when_populated():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        mkdir -p log
        echo '{"agents":[{"agent_id":"aa_live"}],"node_pid":null}' > log/nautilus-watch.agents.json
        stack_nautilus_registry_has_agents && echo has || echo no
        """
    )
    assert proc.returncode == 0
    assert "has" in proc.stdout


def test_stack_nautilus_watch_required_false_when_idle():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        export NAUTILUS_WATCH_ENABLE=true
        stack_load_env
        mkdir -p log
        echo '{"agents":[],"node_pid":null}' > log/nautilus-watch.agents.json
        stack_primary_nautilus_agent_id() { return 0; }
        export -f stack_primary_nautilus_agent_id
        stack_nautilus_watch_required && echo required || echo idle
        """
    )
    assert proc.returncode == 0
    assert "idle" in proc.stdout


def test_stack_nautilus_watch_required_true_when_registry_has_agents():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        export NAUTILUS_WATCH_ENABLE=true
        stack_load_env
        mkdir -p log
        echo '{"agents":[{"agent_id":"aa_live"}],"node_pid":null}' > log/nautilus-watch.agents.json
        stack_nautilus_watch_required && echo required || echo idle
        """
    )
    assert proc.returncode == 0
    assert "required" in proc.stdout


def test_stack_start_nautilus_watch_passes_agent_id_when_registry_empty():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        export NAUTILUS_WATCH_ENABLE=true
        stack_load_env
        mkdir -p log
        echo '{"agents":[],"node_pid":null}' > log/nautilus-watch.agents.json
        stack_reconcile_nautilus_watch_pid() { return 0; }
        stack_adopt_running_nautilus_watch() { return 1; }
        stack_nautilus_pid_valid() { return 1; }
        stack_launch_detached() {
          echo "LAUNCH:$*"
          echo 4242 > "$1"
          return 0
        }
        export -f stack_reconcile_nautilus_watch_pid stack_adopt_running_nautilus_watch \\
          stack_nautilus_pid_valid stack_launch_detached
        stack_start_nautilus_watch aa_flagtest 2>&1 | tee /tmp/nautilus_launch_test.out
        grep -q 'LAUNCH:.*--agent-id aa_flagtest' /tmp/nautilus_launch_test.out
        """
    )
    assert proc.returncode == 0


def test_stack_status_shows_nautilus_idle_when_not_required():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        export NAUTILUS_WATCH_ENABLE=true
        stack_load_env
        mkdir -p log/claims
        echo '{"agents":[],"node_pid":null}' > log/nautilus-watch.agents.json
        stack_primary_nautilus_agent_id() { return 0; }
        stack_nautilus_watch_required() { return 1; }
        stack_nautilus_pid_valid() { return 1; }
        stack_reconcile_for_status() { return 0; }
        stack_http_ok() { return 0; }
        stack_port_listener_pid() { echo ""; }
        stack_pid_alive() { return 1; }
        stack_process_in_trade_repo() { return 0; }
        stack_claim_pid() { echo ""; }
        stack_status_hub_docker() { return 0; }
        stack_probe_llm_wiki() { return 0; }
        export -f stack_primary_nautilus_agent_id stack_nautilus_watch_required \\
          stack_nautilus_pid_valid stack_reconcile_for_status stack_http_ok \\
          stack_port_listener_pid stack_pid_alive stack_process_in_trade_repo \\
          stack_claim_pid stack_status_hub_docker stack_probe_llm_wiki
        stack_status_vibe_stack 2>&1 | tee /tmp/nautilus_status_test.out
        grep -q 'idle (no agents registered)' /tmp/nautilus_status_test.out
        """
    )
    assert proc.returncode == 0


def test_stack_start_nautilus_watch_syncs_registry_without_agent_id():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        export NAUTILUS_WATCH_ENABLE=true
        stack_load_env
        mkdir -p log
        echo '{"agents":[{"agent_id":"aa_sync"}],"node_pid":null}' > log/nautilus-watch.agents.json
        sync_calls=0
        stack_sync_nautilus_registry_quiet() { sync_calls=$((sync_calls + 1)); }
        stack_reconcile_nautilus_watch_pid() { return 0; }
        stack_adopt_running_nautilus_watch() { return 1; }
        stack_nautilus_pid_valid() { return 1; }
        stack_launch_detached() {
          echo "LAUNCH:$*"
          echo 4242 > "$1"
          return 0
        }
        export -f stack_sync_nautilus_registry_quiet stack_reconcile_nautilus_watch_pid \\
          stack_adopt_running_nautilus_watch stack_nautilus_pid_valid stack_launch_detached
        stack_start_nautilus_watch 2>&1 | tee /tmp/nautilus_sync_test.out
        test "$sync_calls" -eq 1
        grep -q 'LAUNCH:.*--registry' /tmp/nautilus_sync_test.out
        """
    )
    assert proc.returncode == 0


def test_stack_restart_nautilus_watch_purges_and_skips_adopt():
    proc = _bash(
        """
        source scripts/stack_lib.sh
        STACK_ROOT="$PWD"
        stack_load_env
        purge=0
        ensure_skip=0
        stack_purge_nautilus_watch_processes() { purge=1; }
        stack_ensure_nautilus_watch() {
          if [[ "${STACK_NAUTILUS_SKIP_ADOPT:-}" == "1" ]]; then
            ensure_skip=1
          fi
          return 0
        }
        export -f stack_purge_nautilus_watch_processes stack_ensure_nautilus_watch
        stack_restart_nautilus_watch
        test "$purge" -eq 1
        test "$ensure_skip" -eq 1
        echo ok
        """
    )
    assert proc.returncode == 0
    assert "ok" in proc.stdout


def test_reload_vibe_stack_bash_syntax_valid():
    proc = _bash("bash -n scripts/reload_vibe_stack.sh && echo ok")
    assert proc.returncode == 0
    assert "ok" in proc.stdout
