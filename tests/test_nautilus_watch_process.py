"""Tests for Nautilus watch process management."""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_integrations.autonomous_agents import nautilus_watch as nw


@pytest.mark.unit
def test_reconcile_stale_watch_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    pidfile = log_dir / "nautilus-watch.pid"
    agent_file = log_dir / "nautilus-watch.agent_id"
    pidfile.write_text("999999", encoding="utf-8")
    agent_file.write_text("aa_dead", encoding="utf-8")

    monkeypatch.setattr(nw, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(nw, "_pidfile", lambda: pidfile)
    monkeypatch.setattr(nw, "_agent_id_file", lambda: agent_file)

    assert nw.reconcile_stale_watch_pid() is True
    assert not pidfile.exists()
    assert not agent_file.exists()


@pytest.mark.unit
def test_get_watch_process_status_clears_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    pidfile = log_dir / "nautilus-watch.pid"
    pidfile.write_text("999999", encoding="utf-8")

    monkeypatch.setattr(nw, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(nw, "_pidfile", lambda: pidfile)
    monkeypatch.setattr(nw, "_agent_id_file", lambda: log_dir / "nautilus-watch.agent_id")
    monkeypatch.setattr(nw, "_logfile", lambda: log_dir / "nautilus-watch.log")
    monkeypatch.setattr(nw, "_watch_enabled", lambda: True)

    status = nw.get_watch_process_status()
    assert status["alive"] is False
    assert status["pid"] is None


@pytest.mark.unit
def test_ensure_nautilus_watch_skips_launch_when_node_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    pidfile = log_dir / "nautilus-watch.pid"
    pidfile.write_text("424242", encoding="utf-8")
    registry = {
        "node_pid": 424242,
        "agents": [{"agent_id": "aa_live", "market": "IN", "symbols": ["NIFTY"]}],
    }
    reg_file = log_dir / "nautilus-watch.agents.json"
    reg_file.write_text(__import__("json").dumps(registry), encoding="utf-8")

    monkeypatch.setattr(nw, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(nw, "_pidfile", lambda: pidfile)
    monkeypatch.setattr(nw, "_registry_file", lambda: reg_file)
    monkeypatch.setattr(nw, "_watch_enabled", lambda: True)
    monkeypatch.setattr(nw, "_process_alive", lambda pid: pid == 424242)
    monkeypatch.setattr(nw, "is_agent_in_registry", lambda aid: aid == "aa_live")
    monkeypatch.setattr(
        "trade_integrations.watch_registry.store.sync_nautilus_registry_from_watches",
        lambda **kw: None,
    )
    monkeypatch.setattr(nw, "_stamp_handoff_market_context", lambda agent_id: None)

    launched: list[bool] = []
    monkeypatch.setattr(nw, "_launch_watch", lambda **kw: launched.append(True))
    monkeypatch.setattr(
        nw,
        "add_agent_to_registry",
        lambda agent_id: registry,
    )

    assert nw.ensure_nautilus_watch_for_agent("aa_live") is None
    assert launched == []


@pytest.mark.unit
def test_ensure_nautilus_watch_stamps_handoff_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    pidfile = log_dir / "nautilus-watch.pid"
    pidfile.write_text("424242", encoding="utf-8")
    registry = {
        "node_pid": 424242,
        "agents": [{"agent_id": "aa_live", "market": "IN", "symbols": ["NIFTY"]}],
    }
    reg_file = log_dir / "nautilus-watch.agents.json"
    reg_file.write_text(__import__("json").dumps(registry), encoding="utf-8")

    monkeypatch.setattr(nw, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(nw, "_pidfile", lambda: pidfile)
    monkeypatch.setattr(nw, "_registry_file", lambda: reg_file)
    monkeypatch.setattr(nw, "_watch_enabled", lambda: True)
    monkeypatch.setattr(nw, "_process_alive", lambda pid: pid == 424242)
    monkeypatch.setattr(nw, "is_agent_in_registry", lambda aid: aid == "aa_live")
    monkeypatch.setattr(
        "trade_integrations.watch_registry.store.sync_nautilus_registry_from_watches",
        lambda **kw: None,
    )
    monkeypatch.setattr(nw, "_launch_watch", lambda **kw: 424242)

    stamped: list[tuple[str, str]] = []

    def _fake_stamp(agent_id: str) -> None:
        stamped.append((agent_id, "2026-07-23T09:15:00+05:30"))

    monkeypatch.setattr(nw, "_stamp_handoff_market_context", _fake_stamp)

    assert nw.ensure_nautilus_watch_for_agent("aa_live") is None
    assert stamped == [("aa_live", "2026-07-23T09:15:00+05:30")]


@pytest.mark.unit
def test_get_research_status_marks_stages_complete_when_overall_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.research.orchestrator import ResearchResult, get_research_status
    from trade_integrations.research.registry import ResearchKind

    monkeypatch.setattr(
        "trade_integrations.research.orchestrator.ensure_research_complete",
        lambda *a, **k: ResearchResult(
            status="complete",
            kind=ResearchKind.OPTIONS,
            ticker="NIFTY",
            stages_run=["options_research:cache", "agent_debate"],
            debate_pending=False,
        ),
    )

    out = get_research_status("NIFTY", kind="options")
    assert out["status"] == "complete"
    assert all(s["complete"] for s in out["stages"])


def _wire_nautilus_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    registry: dict | None = None,
    pid: int | None = None,
) -> Path:
    log_dir = tmp_path / "log"
    log_dir.mkdir(parents=True)
    reg_file = log_dir / "nautilus-watch.agents.json"
    payload = registry if registry is not None else {"node_pid": pid, "agents": []}
    reg_file.write_text(__import__("json").dumps(payload), encoding="utf-8")
    pidfile = log_dir / "nautilus-watch.pid"
    if pid is not None:
        pidfile.write_text(str(pid), encoding="utf-8")

    monkeypatch.setattr(nw, "_log_dir", lambda: log_dir)
    monkeypatch.setattr(nw, "_pidfile", lambda: pidfile)
    monkeypatch.setattr(nw, "_registry_file", lambda: reg_file)
    monkeypatch.setattr(nw, "_agent_id_file", lambda: log_dir / "nautilus-watch.agent_id")
    monkeypatch.setattr(nw, "_logfile", lambda: log_dir / "nautilus-watch.log")
    monkeypatch.setattr(nw, "_watch_enabled", lambda: True)
    monkeypatch.setattr(nw, "_trade_root", lambda: tmp_path)
    from trade_integrations.watch_registry.sync_lock import reset_watch_registry_sync_lock_for_tests

    reset_watch_registry_sync_lock_for_tests()
    return log_dir


@pytest.mark.unit
def test_run_stack_nautilus_start_launches_with_agent_id_when_registry_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = _wire_nautilus_paths(tmp_path, monkeypatch, registry={"node_pid": None, "agents": []})
    launch_args: list[dict] = []

    def _fake_launch(**kw):
        launch_args.append(kw)
        return 4242

    monkeypatch.setattr(nw, "reconcile_stale_watch_pid", lambda: False)
    monkeypatch.setattr(nw, "_process_alive", lambda pid: False)
    monkeypatch.setattr(nw, "_launch_watch", _fake_launch)
    monkeypatch.setattr(
        "trade_integrations.watch_registry.store._sync_nautilus_registry_from_watches_locked",
        lambda **kw: None,
    )

    result = nw.run_stack_nautilus_start(agent_id="aa_flagtest")
    assert result["status"] == "ok"
    assert result["pid"] == 4242
    assert launch_args == [{"use_registry": False, "agent_id": "aa_flagtest"}]


@pytest.mark.unit
def test_run_stack_nautilus_start_uses_registry_when_agents_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = {
        "node_pid": None,
        "agents": [{"agent_id": "aa_sync", "market": "IN", "symbols": ["NIFTY"]}],
    }
    _wire_nautilus_paths(tmp_path, monkeypatch, registry=registry)
    synced: list[bool] = []
    launch_args: list[dict] = []

    monkeypatch.setattr(nw, "reconcile_stale_watch_pid", lambda: False)
    monkeypatch.setattr(nw, "_process_alive", lambda pid: False)
    monkeypatch.setattr(nw, "_launch_watch", lambda **kw: launch_args.append(kw) or 4242)
    monkeypatch.setattr(
        "trade_integrations.watch_registry.store._sync_nautilus_registry_from_watches_locked",
        lambda **kw: synced.append(True),
    )

    result = nw.run_stack_nautilus_start()
    assert result["status"] == "ok"
    assert synced == [True]
    assert launch_args == [{"use_registry": True, "agent_id": None}]


@pytest.mark.unit
def test_run_stack_skip_adopt_aborts_when_purge_leaves_survivors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_nautilus_paths(
        tmp_path,
        monkeypatch,
        registry={"node_pid": 4242, "agents": [{"agent_id": "aa_purgefail"}]},
        pid=4242,
    )
    launched: list[bool] = []
    monkeypatch.setattr(nw, "reconcile_stale_watch_pid", lambda: False)
    monkeypatch.setattr(nw, "_process_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(
        nw,
        "purge_nautilus_watch_processes",
        lambda: {"purged": False, "killed_pids": [4242], "survivors": [4242]},
    )
    monkeypatch.setattr(nw, "_launch_watch", lambda **kw: launched.append(True) or 9999)

    result = nw.run_stack_nautilus_start(skip_adopt=True)
    assert result["status"] == "error"
    assert result["reason"] == "purge_incomplete"
    assert result["survivors"] == [4242]
    assert launched == []


@pytest.mark.unit
def test_purge_reports_incomplete_when_survivors_remain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_nautilus_paths(tmp_path, monkeypatch, registry={"node_pid": 7777, "agents": []}, pid=7777)
    monkeypatch.setattr(nw, "reconcile_stale_watch_pid", lambda: False)
    monkeypatch.setattr(nw, "_process_alive", lambda pid: pid == 7777)
    monkeypatch.setattr(nw, "_pgrep_watch_pids", lambda: [7777])
    monkeypatch.setattr(nw, "_process_in_trade_repo", lambda pid: True)
    monkeypatch.setattr(nw, "_kill_pid_graceful", lambda pid: None)

    result = nw.purge_nautilus_watch_processes()
    assert result["purged"] is False
    assert result["survivors"] == [7777]


@pytest.mark.unit
def test_watch_registry_mutation_lock_is_reentrant_same_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_nautilus_paths(tmp_path, monkeypatch)
    from trade_integrations.watch_registry.sync_lock import watch_registry_mutation_lock

    depth = {"max": 0, "current": 0}

    with watch_registry_mutation_lock():
        depth["current"] = 1
        depth["max"] = max(depth["max"], depth["current"])
        with watch_registry_mutation_lock():
            depth["current"] = 2
            depth["max"] = max(depth["max"], depth["current"])
        depth["current"] = 1
    depth["current"] = 0
    assert depth["max"] == 2


@pytest.mark.unit
def test_nautilus_watch_cli_stack_purge_exits_one_on_survivors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trade_integrations.autonomous_agents import nautilus_watch_cli as cli

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.purge_nautilus_watch_processes",
        lambda: {"purged": False, "killed_pids": [], "survivors": [4242]},
    )
    assert cli.main(["stack-purge"]) == 1
