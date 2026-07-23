"""Screenshot resize and artifact persistence for external predictions vision."""

from __future__ import annotations

import base64
import io
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from trade_integrations.dataflows.index_research.external_predictions.models import utc_now_iso
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    external_predictions_root,
)

logger = logging.getLogger(__name__)

_DEFAULT_M3_MAX = 1024
_FALLBACK_M3_MAX = 512
_THUMBNAIL_WIDTH = 240
_JPEG_QUALITY = 80
_MAX_TILE_HEIGHT = 1024


@dataclass
class ScreenshotArtifacts:
    run_id: str
    full_path: Path
    m3_paths: list[Path] = field(default_factory=list)
    thumbnail_path: Path | None = None

    def thumbnail_api_path(self, *, symbol: str, source_id: str) -> str:
        return (
            f"/trade/index-prediction/external-predictions/sources/{source_id}/thumbnail"
            f"?ticker={symbol.upper()}&run_id={self.run_id}"
        )


def m3_max_dimension() -> int:
    raw = os.environ.get("EXTERNAL_PREDICTIONS_M3_MAX_DIM", str(_DEFAULT_M3_MAX)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_M3_MAX
    if value not in {512, 1024}:
        return _DEFAULT_M3_MAX
    return value


def artifact_run_dir(symbol: str, source_id: str, run_id: str) -> Path:
    path = external_predictions_root(symbol) / "sources" / source_id / "artifacts" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_run_id() -> str:
    return utc_now_iso().replace(":", "").replace("+", "_")


def _open_image(raw: bytes):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow required for screenshot resize — pip install Pillow") from exc
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _save_jpeg(img, path: Path, *, quality: int = _JPEG_QUALITY) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="JPEG", quality=quality, optimize=True)


def resize_for_m3_tiles(raw: bytes, *, max_dim: int | None = None) -> list[bytes]:
    """Resize screenshot for MiniMax M3; tile vertically when page is very tall."""
    limit = max_dim if max_dim is not None else m3_max_dimension()
    img = _open_image(raw)
    width, height = img.size
    if width <= 0 or height <= 0:
        return []

    if width > limit:
        scale = limit / width
        new_w = max(1, int(width * scale))
        new_h = max(1, int(height * scale))
        img = img.resize((new_w, new_h))

    tiles: list[bytes] = []
    _, page_height = img.size
    if page_height <= _MAX_TILE_HEIGHT:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return [buf.getvalue()]

    y = 0
    while y < page_height:
        box = (0, y, img.size[0], min(page_height, y + _MAX_TILE_HEIGHT))
        crop = img.crop(box)
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        tiles.append(buf.getvalue())
        y += _MAX_TILE_HEIGHT
    return tiles


def make_thumbnail(raw: bytes, *, width: int = _THUMBNAIL_WIDTH) -> bytes:
    img = _open_image(raw)
    w, h = img.size
    if w <= 0 or h <= 0:
        return raw
    scale = width / w
    thumb = img.resize((width, max(1, int(h * scale))))
    buf = io.BytesIO()
    thumb.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def decode_screenshot_payload(payload: str | bytes | None) -> bytes | None:
    if payload is None:
        return None
    if isinstance(payload, bytes):
        return payload if payload else None
    text = str(payload).strip()
    if not text:
        return None
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text)
    except (ValueError, TypeError):
        return None


def persist_screenshot(
    *,
    symbol: str,
    source_id: str,
    raw: bytes,
    run_id: str | None = None,
) -> ScreenshotArtifacts:
    """Write full-page, M3 tile(s), and thumbnail JPEGs under hub artifacts."""
    rid = run_id or new_run_id()
    root = artifact_run_dir(symbol, source_id, rid)
    full_path = root / "screenshot.jpg"
    full_path.write_bytes(raw)

    m3_paths: list[Path] = []
    try:
        tiles = resize_for_m3_tiles(raw)
        for idx, tile in enumerate(tiles):
            name = "screenshot_m3.jpg" if len(tiles) == 1 else f"screenshot_m3_{idx + 1}.jpg"
            path = root / name
            path.write_bytes(tile)
            m3_paths.append(path)
    except Exception as exc:
        logger.warning("M3 screenshot resize failed for %s: %s", source_id, exc)

    thumb_path = root / "thumbnail.jpg"
    try:
        thumb_path.write_bytes(make_thumbnail(raw))
    except Exception as exc:
        logger.warning("thumbnail resize failed for %s: %s", source_id, exc)
        thumb_path = None

    return ScreenshotArtifacts(
        run_id=rid,
        full_path=full_path,
        m3_paths=m3_paths,
        thumbnail_path=thumb_path,
    )


def persist_screenshot_b64(
    *,
    symbol: str,
    source_id: str,
    screenshot_b64: str,
    run_id: str | None = None,
) -> ScreenshotArtifacts | None:
    raw = decode_screenshot_payload(screenshot_b64)
    if not raw:
        return None
    return persist_screenshot(symbol=symbol, source_id=source_id, raw=raw, run_id=run_id)


def jpeg_file_to_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def resolve_thumbnail_path(
    *,
    symbol: str,
    source_id: str,
    run_id: str | None = None,
) -> Path | None:
    if run_id:
        candidate = artifact_run_dir(symbol, source_id, run_id) / "thumbnail.jpg"
        return candidate if candidate.is_file() else None
    from trade_integrations.dataflows.index_research.external_predictions.store import (
        load_source_prediction,
    )

    record = load_source_prediction(source_id, symbol=symbol)
    if record and record.provenance:
        rel = str(record.provenance.get("thumbnail_path") or "").strip()
        if rel:
            path = external_predictions_root(symbol) / rel
            if path.is_file():
                return path
        stored_run = str(record.provenance.get("artifact_run_id") or "").strip()
        if stored_run:
            return resolve_thumbnail_path(symbol=symbol, source_id=source_id, run_id=stored_run)
    return None
