"""Tests for unified watch registry (session + autonomous owners)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


@pytest.fixture
def log_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    log = tmp_path / "log"
    log.mkdir()
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch._log_dir",
        lambda: log,
    )
    return log


def _sample_spec() -> dict:
    return {
        "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5}],
        "cooldown_sec": 300,
    }


def test_create_list_delete_session_watch(hub_tmp: Path, log_dir: Path):
    from trade_integrations.watch_registry import (
        create_watch,
        delete_watch,
        list_watches,
        nautilus_owner_id,
    )

    watch = create_watch(
        owner_kind="session",
        owner_id="sess_abc",
        vibe_session_id="sess_abc",
        watch_spec=_sample_spec(),
        label="NIFTY move",
    )["watch"]
    assert watch["watch_id"].startswith("w_")
    assert watch["symbols"] == ["NIFTY"]

    rows = list_watches(owner_kind="session", owner_id="sess_abc")
    assert len(rows) == 1
    assert nautilus_owner_id(owner_kind="session", owner_id="sess_abc") == "ws_sess_abc"

    assert delete_watch(watch["watch_id"]) is not None
    assert list_watches(owner_kind="session", owner_id="sess_abc") == []


def test_sync_nautilus_registry_from_session_watch(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.watch_registry import create_watch, sync_nautilus_registry_from_watches
    from trade_integrations.autonomous_agents import nautilus_watch as nw

    monkeypatch.setattr(nw, "_launch_watch", lambda **_: None)
    monkeypatch.setattr(nw, "purge_nautilus_watch_processes", lambda: {"purged": True, "killed_pids": [], "survivors": []})
    monkeypatch.setattr(nw, "_read_pid", lambda: None)

    create_watch(
        owner_kind="session",
        owner_id="sess_sync",
        vibe_session_id="sess_sync",
        watch_spec=_sample_spec(),
    )
    result = sync_nautilus_registry_from_watches(restart_if_changed=False)
    assert result["status"] == "ok"
    assert "ws_sess_sync" in nw.get_registry_agent_ids()


def test_list_active_nautilus_owners_includes_infra_paused_plan_approved(
    hub_tmp: Path,
    log_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from trade_integrations.autonomous_agents.store import save_agent
    from trade_integrations.watch_registry import create_watch, list_active_nautilus_owners

    agents_dir = hub_tmp / "_data" / "autonomous_agents"
    agents_dir.mkdir(parents=True)
    agent_id = "aa_infra_watch"
    save_agent(
        {
            "id": agent_id,
            "type": "autonomous_agent.instance",
            "name": "Infra paused",
            "status": "paused",
            "pause_reason": "infra",
            "plan_approved_at": "2026-07-16T20:00:00+00:00",
            "vibe_session_id": "sess_infra",
            "symbols": ["NIFTY"],
            "execution_market": "IN",
            "execution_backend": "openalgo",
            "constraints": {"mode": "paper"},
            "mandate_config": {},
            "watch_spec": _sample_spec(),
        }
    )
    create_watch(
        owner_kind="autonomous_agent",
        owner_id=agent_id,
        vibe_session_id="sess_infra",
        watch_spec=_sample_spec(),
        symbols=["NIFTY"],
    )
    owners = list_active_nautilus_owners()
    assert any(row.get("owner_id") == agent_id for row in owners)


def test_sync_nautilus_registry_recovers_after_relaunch_failure(
    hub_tmp: Path,
    log_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from trade_integrations.autonomous_agents import nautilus_watch as nw
    from trade_integrations.watch_registry import create_watch
    from trade_integrations.watch_registry import store as wr_store

    class _PidState:
        live: int | None = None

        def read_pid(self) -> int | None:
            return self.live

        def process_alive(self, pid: int) -> bool:
            return self.live is not None and int(pid) == int(self.live)

        def stop(self) -> None:
            self.live = None

    state = _PidState()
    sync_results: list[dict] = []
    real_sync = wr_store.sync_nautilus_registry_from_watches

    def _capture_sync(**kwargs: object) -> dict:
        result = real_sync(**kwargs)
        sync_results.append(result)
        return result

    monkeypatch.setattr(wr_store, "sync_nautilus_registry_from_watches", _capture_sync)
    monkeypatch.setattr(nw, "_read_pid", state.read_pid)
    monkeypatch.setattr(nw, "_process_alive", state.process_alive)

    def _purge() -> dict:
        state.stop()
        return {"purged": True, "killed_pids": [], "survivors": []}

    monkeypatch.setattr(nw, "purge_nautilus_watch_processes", _purge)

    def _fail_launch(**_: object) -> None:
        raise RuntimeError("launch failed")

    monkeypatch.setattr(nw, "_launch_watch", _fail_launch)
    monkeypatch.setattr(nw, "ensure_nautilus_watch_for_running_agents", lambda: 0)

    create_watch(
        owner_kind="session",
        owner_id="sess_recover",
        vibe_session_id="sess_recover",
        watch_spec=_sample_spec(),
    )
    state.live = 9999

    create_watch(
        owner_kind="session",
        owner_id="sess_recover2",
        vibe_session_id="sess_recover2",
        watch_spec=_sample_spec(),
    )

    partial = [row for row in sync_results if row.get("status") == "partial"]
    assert partial, sync_results
    assert partial[-1].get("nautilus_ok") is False
    assert "ws_sess_recover2" in (partial[-1].get("agent_ids") or [])


def test_mcp_status_maps_skipped_sync_to_partial():
    from trade_integrations.watch_registry.api import _status_from_nautilus_sync

    assert (
        _status_from_nautilus_sync({"status": "skipped", "reason": "nautilus_watch unavailable"})
        == "partial"
    )


def test_delete_last_watch_purges_without_relaunch(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.autonomous_agents import nautilus_watch as nw
    from trade_integrations.watch_registry import create_watch, delete_watch

    launches: list[bool] = []
    purges: list[bool] = []

    monkeypatch.setattr(nw, "_launch_watch", lambda **_: launches.append(True))
    monkeypatch.setattr(
        nw,
        "purge_nautilus_watch_processes",
        lambda: purges.append(True) or {"purged": True, "killed_pids": [9999], "survivors": []},
    )
    monkeypatch.setattr(nw, "_read_pid", lambda: 9999)
    monkeypatch.setattr(nw, "_process_alive", lambda pid: int(pid) == 9999)

    watch = create_watch(
        owner_kind="session",
        owner_id="sess_last",
        vibe_session_id="sess_last",
        watch_spec=_sample_spec(),
    )["watch"]
    launches.clear()
    purges.clear()

    result = delete_watch(watch["watch_id"])
    assert result is not None
    assert result["nautilus_sync"]["status"] == "ok"
    assert purges, "expected purge when removing last watch with live node"
    assert not launches, "must not relaunch when registry becomes empty"


def test_delete_already_deleted_watch_skips_sync(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from trade_integrations.autonomous_agents import nautilus_watch as nw
    from trade_integrations.watch_registry import create_watch, delete_watch
    from trade_integrations.watch_registry import store as wr_store

    monkeypatch.setattr(nw, "_launch_watch", lambda **_: None)
    monkeypatch.setattr(nw, "purge_nautilus_watch_processes", lambda: {"purged": True, "killed_pids": [], "survivors": []})
    monkeypatch.setattr(nw, "_read_pid", lambda: None)

    sync_calls: list[bool] = []
    real_sync = wr_store._sync_nautilus_registry_from_watches_locked

    def _counting_sync(**kwargs: object) -> dict:
        sync_calls.append(True)
        return real_sync(**kwargs)

    monkeypatch.setattr(wr_store, "_sync_nautilus_registry_from_watches_locked", _counting_sync)

    watch = create_watch(
        owner_kind="session",
        owner_id="sess_redelete",
        vibe_session_id="sess_redelete",
        watch_spec=_sample_spec(),
    )["watch"]
    delete_watch(watch["watch_id"])
    sync_calls.clear()

    again = delete_watch(watch["watch_id"])
    assert again is not None
    assert again["nautilus_sync"]["reason"] == "already_deleted"
    assert sync_calls == []


def test_concurrent_create_watch_serializes_sync(hub_tmp: Path, log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    import threading
    import time

    from trade_integrations.autonomous_agents import nautilus_watch as nw
    from trade_integrations.watch_registry import create_watch
    from trade_integrations.watch_registry import store as wr_store

    monkeypatch.setattr(nw, "_launch_watch", lambda **_: None)
    monkeypatch.setattr(nw, "purge_nautilus_watch_processes", lambda: {"purged": True, "killed_pids": [], "survivors": []})
    monkeypatch.setattr(nw, "_read_pid", lambda: None)

    in_sync = {"n": 0, "max": 0}
    gate = threading.Lock()
    real_sync = wr_store._sync_nautilus_registry_from_watches_locked

    def _tracked_sync(**kwargs: object) -> dict:
        with gate:
            in_sync["n"] += 1
            in_sync["max"] = max(in_sync["max"], in_sync["n"])
        time.sleep(0.03)
        try:
            return real_sync(**kwargs)
        finally:
            with gate:
                in_sync["n"] -= 1

    monkeypatch.setattr(wr_store, "_sync_nautilus_registry_from_watches_locked", _tracked_sync)

    errors: list[BaseException] = []

    def _create(owner_id: str) -> None:
        try:
            create_watch(
                owner_kind="session",
                owner_id=owner_id,
                vibe_session_id=owner_id,
                watch_spec=_sample_spec(),
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_create, args=(f"sess_par_{i}",)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert in_sync["max"] == 1, f"sync ran concurrently (max overlap={in_sync['max']})"
