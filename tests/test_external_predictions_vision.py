"""Tests for external predictions vision pipeline (Phase 3)."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest

from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSource,
    ExternalPredictionTarget,
)
from trade_integrations.dataflows.index_research.external_predictions.screenshot_utils import (
    decode_screenshot_payload,
    m3_max_dimension,
    persist_screenshot,
    resize_for_m3_tiles,
    resolve_thumbnail_path,
)


def _solid_jpeg(*, width: int = 800, height: int = 2400) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(30, 60, 120))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture
def hub_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def test_m3_max_dimension_defaults_to_1024(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXTERNAL_PREDICTIONS_M3_MAX_DIM", raising=False)
    assert m3_max_dimension() == 1024
    monkeypatch.setenv("EXTERNAL_PREDICTIONS_M3_MAX_DIM", "512")
    assert m3_max_dimension() == 512


def test_resize_for_m3_tiles_scales_and_splits_tall_pages() -> None:
    raw = _solid_jpeg(width=1200, height=3000)
    tiles = resize_for_m3_tiles(raw, max_dim=1024)
    assert tiles
    assert len(tiles) >= 2
    from PIL import Image

    first = Image.open(io.BytesIO(tiles[0]))
    assert max(first.size) <= 1024


def test_persist_screenshot_writes_artifact_files(hub_dir: Path) -> None:
    raw = _solid_jpeg(width=600, height=900)
    artifacts = persist_screenshot(symbol="NIFTY", source_id="moneycontrol", raw=raw, run_id="test_run")
    assert artifacts.full_path.is_file()
    assert artifacts.thumbnail_path is not None
    assert artifacts.thumbnail_path.is_file()
    assert artifacts.m3_paths
    assert artifacts.thumbnail_api_path(symbol="NIFTY", source_id="moneycontrol").startswith("/trade/")


def test_resolve_thumbnail_path_from_record(hub_dir: Path) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.store import (
        upsert_prediction,
    )

    raw = _solid_jpeg()
    artifacts = persist_screenshot(symbol="NIFTY", source_id="moneycontrol", raw=raw, run_id="run123")
    rel_thumb = str(artifacts.thumbnail_path.relative_to(hub_dir / "NIFTY" / "external_predictions"))
    record = ExternalPredictionRecord(
        source_id="moneycontrol",
        fetch_status="ok",
        target=ExternalPredictionTarget(mid=25000.0),
        provenance={
            "artifact_run_id": "run123",
            "thumbnail_path": rel_thumb,
        },
    )
    upsert_prediction(record, symbol="NIFTY")
    path = resolve_thumbnail_path(symbol="NIFTY", source_id="moneycontrol")
    assert path is not None
    assert path.is_file()


def test_extract_forecast_text_fallback_when_vision_disabled(
    hub_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.financial_expert_agent import (
        extract_forecast,
    )

    monkeypatch.setenv("EXTERNAL_PREDICTIONS_EXPERT_VISION", "0")

    def _fake_text(**kwargs):
        return ExternalPredictionRecord(
            source_id=kwargs["source"].id,
            fetch_status="ok",
            target=ExternalPredictionTarget(mid=25000.0),
        )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.financial_expert_agent.extract_prediction_from_text",
        lambda **kwargs: _fake_text(**kwargs),
    )

    artifacts = persist_screenshot(
        symbol="NIFTY",
        source_id="moneycontrol",
        raw=_solid_jpeg(width=400, height=600),
        run_id="vision_off",
    )
    source = ExternalPredictionSource(id="moneycontrol", display_name="Moneycontrol")
    record = extract_forecast(
        source=source,
        horizon_days=14,
        spot=24000.0,
        title="Nifty target",
        url="https://example.com/nifty",
        snippet="Nifty 50 target 25000",
        body="Nifty 50 target 25000 on flows.",
        screenshot_artifacts=artifacts,
    )
    assert record.fetch_status == "ok"
    assert record.provenance.get("thumbnail_url")


def test_vision_cross_check_honors_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.minimax_vision import (
        vision_cross_check,
    )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.minimax_vision.call_minimax_vision_json",
        lambda **kwargs: {"supports_forecast": False, "reason": "page shows stock not index"},
    )
    ok, err = vision_cross_check(
        target_mid=25000.0,
        target_date="2026-08-01",
        direction="bullish",
        image_jpeg_b64_list=["abc"],
        url="https://example.com",
        title="Test",
    )
    assert ok is False
    assert "stock" in err


def test_vision_cross_check_fails_closed_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.minimax_vision import (
        vision_cross_check,
    )

    def _boom(**kwargs):
        raise RuntimeError("minimax timeout")

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.minimax_vision.call_minimax_vision_json",
        _boom,
    )
    ok, err = vision_cross_check(
        target_mid=25000.0,
        target_date="2026-08-01",
        direction="bullish",
        image_jpeg_b64_list=["abc"],
        url="https://example.com",
        title="Test",
    )
    assert ok is False
    assert "vision_unavailable" in err


def test_decode_screenshot_payload_accepts_data_uri() -> None:
    raw = _solid_jpeg(width=10, height=10)
    b64 = base64.b64encode(raw).decode("ascii")
    decoded = decode_screenshot_payload(f"data:image/jpeg;base64,{b64}")
    assert decoded == raw
