"""Tests for tier-2 staging cluster dedupe."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_assign_cluster_ids_groups_similar():
    from trade_integrations.dataflows.index_research.news_embedding_cluster import assign_cluster_ids

    refs = [
        {"ref_id": "a", "title": "Nifty rises on FII inflows", "summary": "Markets up"},
        {"ref_id": "b", "title": "Nifty rises on FII inflows today", "summary": "Markets up sharply"},
        {"ref_id": "c", "title": "Oil prices fall on demand worry", "summary": "Crude down"},
    ]
    assign_cluster_ids(refs, threshold=0.7)
    assert refs[0]["cluster_id"] == refs[1]["cluster_id"]
    assert refs[2]["cluster_id"] != refs[0]["cluster_id"]


def test_assign_cluster_ids_transitive_via_best_of_cluster():
    from trade_integrations.dataflows.index_research.news_embedding_cluster import assign_cluster_ids

    refs = [
        {"ref_id": "a", "title": "RBI holds repo rate unchanged", "summary": "No change in policy"},
        {"ref_id": "b", "title": "RBI keeps repo rate steady", "summary": "Policy unchanged today"},
        {"ref_id": "c", "title": "Reserve Bank of India leaves rates unchanged", "summary": "Repo steady"},
    ]
    assign_cluster_ids(refs, threshold=0.65)
    assert refs[0]["cluster_id"] == refs[1]["cluster_id"] == refs[2]["cluster_id"]


def test_dedupe_pending_by_cluster_keeps_leader(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging_store
    from trade_integrations.dataflows.index_research.news_embedding_cluster import dedupe_pending_by_cluster

    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)

    refs = [
        {"ref_id": "r1", "title": "RBI holds rates steady", "summary": "No change"},
        {"ref_id": "r2", "title": "RBI holds rates steady", "summary": "No change in policy"},
    ]
    kept, stats = dedupe_pending_by_cluster(refs, ticker="NIFTY", threshold=0.75)
    assert stats["kept"] == 1
    assert stats["dropped"] == 1
    assert len(kept) == 1
    assert kept[0]["ref_id"] == "r1"
