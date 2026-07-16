"""Silent CDP downloads for nodriver — avoids Chrome Save As dialog."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from trade_integrations.nse_browser.registry import downloads_dir

logger = logging.getLogger(__name__)

DEFAULT_DOWNLOAD_TIMEOUT_S = float(os.environ.get("NSE_BROWSER_DOWNLOAD_TIMEOUT_S", "25"))


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "download")
    cleaned = cleaned.strip(". ") or "download"
    return cleaned[:255]


def _unique_path(directory: Path, filename: str) -> Path:
    base = directory / filename
    if not base.exists():
        return base
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for i in range(1, 100):
        candidate = directory / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_{os.getpid()}{suffix}"


@dataclass
class DownloadManager:
    """Configure page-level CDP download behavior and wait for files."""

    directory: Path = field(default_factory=downloads_dir)
    _pending: dict[str, str] = field(default_factory=dict)
    _completed: dict[str, Path] = field(default_factory=dict)
    _events: list[asyncio.Event] = field(default_factory=list)
    _configured: bool = False

    def __post_init__(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    async def configure(self, tab) -> None:
        """Enable silent downloads on a nodriver tab (page-level CDP session)."""
        if self._configured:
            return
        try:
            from nodriver import cdp
        except ImportError:
            logger.warning("nodriver not installed; download manager inactive")
            return

        path = str(self.directory.resolve())
        try:
            await tab.set_download_path(self.directory)
        except Exception as exc:
            logger.debug("set_download_path failed: %s", exc)

        try:
            await tab.send(
                cdp.browser.set_download_behavior(
                    "allowAndName",
                    download_path=path,
                    events_enabled=True,
                )
            )
        except Exception as exc:
            logger.debug("allowAndName failed, trying allow: %s", exc)
            try:
                await tab.send(
                    cdp.browser.set_download_behavior(
                        "allow",
                        download_path=path,
                        events_enabled=True,
                    )
                )
            except Exception as exc2:
                logger.warning("CDP set_download_behavior failed: %s", exc2)
                return

        for event_cls in (
            getattr(cdp.browser, "DownloadWillBegin", None),
            getattr(cdp.page, "DownloadWillBegin", None),
        ):
            if event_cls is not None:
                tab.add_handler(event_cls, self._on_download_will_begin)

        for event_cls in (
            getattr(cdp.browser, "DownloadProgress", None),
            getattr(cdp.page, "DownloadProgress", None),
        ):
            if event_cls is not None:
                tab.add_handler(event_cls, self._on_download_progress)

        self._configured = True
        logger.debug("Download manager configured: %s", path)

    async def _on_download_will_begin(self, event) -> None:
        guid = getattr(event, "guid", None) or getattr(event, "download_id", None)
        suggested = getattr(event, "suggested_filename", None) or getattr(event, "suggestedFilename", None)
        if guid and suggested:
            self._pending[str(guid)] = _sanitize_filename(str(suggested))
            logger.debug("Download started: %s -> %s", guid, suggested)

    async def _on_download_progress(self, event) -> None:
        state = getattr(event, "state", None)
        guid = getattr(event, "guid", None) or getattr(event, "download_id", None)
        if not guid:
            return
        guid = str(guid)
        if state and str(state).lower() in {"completed", "complete"}:
            await self._finalize_download(guid)

    async def _finalize_download(self, guid: str) -> None:
        suggested = self._pending.pop(guid, None)
        if not suggested:
            return
        guid_path = self.directory / guid
        if guid_path.is_file():
            dest = _unique_path(self.directory, suggested)
            try:
                guid_path.rename(dest)
                self._completed[guid] = dest
                logger.info("Download saved: %s", dest.name)
                return
            except Exception as exc:
                logger.debug("GUID rename failed: %s", exc)

        # allowAndName may write suggested name directly
        direct = self.directory / suggested
        if direct.is_file():
            self._completed[guid] = direct
            logger.info("Download saved: %s", direct.name)

    async def wait_for_download(self, *, timeout_s: float | None = None) -> Path | None:
        """Wait until a new file appears in the download directory."""
        timeout = DEFAULT_DOWNLOAD_TIMEOUT_S if timeout_s is None else timeout_s
        before = {p.name for p in self.directory.iterdir() if p.is_file()}
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            after = [p for p in self.directory.iterdir() if p.is_file()]
            new_files = [p for p in after if p.name not in before]
            if new_files:
                newest = max(new_files, key=lambda p: p.stat().st_mtime)
                if newest.stat().st_size > 0:
                    return newest
            if self._completed:
                return next(reversed(self._completed.values()))
            await asyncio.sleep(0.3)
        return None

    async def trigger_and_wait(
        self,
        click_fn: Callable[[], Awaitable[None]],
        *,
        timeout_s: float | None = None,
    ) -> Path | None:
        """Run click_fn after CDP is configured, then wait for download file."""
        if not self._configured:
            logger.warning("trigger_and_wait called before configure()")
        before = {p.name for p in self.directory.iterdir() if p.is_file()}
        await click_fn()
        timeout = DEFAULT_DOWNLOAD_TIMEOUT_S if timeout_s is None else timeout_s
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for p in self.directory.iterdir():
                if p.is_file() and p.name not in before and p.stat().st_size > 0:
                    return p
            if self._completed:
                return next(reversed(self._completed.values()))
            await asyncio.sleep(0.3)
        return None

    def read_download_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.debug("read_download_text failed: %s", exc)
            return ""
