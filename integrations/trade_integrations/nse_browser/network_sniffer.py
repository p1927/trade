"""CDP network capture — grab JSON/CSV API responses without clicking download."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_URL_HINTS = re.compile(
    r"(fiidii|/api/|\.csv|tradeReact|TradeCSV|download)",
    re.IGNORECASE,
)


@dataclass
class CapturedResponse:
    url: str
    status: int
    mime_type: str
    body: str
    request_id: str = ""


@dataclass
class NetworkSniffer:
    """Capture XHR/fetch bodies during page navigation."""

    url_pattern: re.Pattern[str] = field(default_factory=lambda: _URL_HINTS)
    captured: list[CapturedResponse] = field(default_factory=list)
    _pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    _enabled: bool = False
    _tab: Any = None

    async def attach(self, tab) -> None:
        if self._enabled:
            return
        try:
            from nodriver import cdp
        except ImportError:
            logger.warning("nodriver not installed; network sniffer inactive")
            return

        self._tab = tab
        await tab.send(cdp.network.enable())
        tab.add_handler(cdp.network.ResponseReceived, self._on_response_received)
        tab.add_handler(cdp.network.LoadingFinished, self._on_loading_finished)
        self._enabled = True

    async def _on_response_received(self, event) -> None:
        try:
            response = event.response
            url = str(getattr(response, "url", "") or "")
            if not url or not self.url_pattern.search(url):
                return
            mime = str(getattr(response, "mime_type", "") or getattr(response, "mimeType", "") or "")
            status = int(getattr(response, "status", 0) or 0)
            request_id = str(getattr(event, "request_id", "") or getattr(event, "requestId", "") or "")
            if not request_id:
                return
            self._pending[request_id] = {"url": url, "status": status, "mime_type": mime}
        except Exception as exc:
            logger.debug("ResponseReceived handler error: %s", exc)

    async def _on_loading_finished(self, event) -> None:
        request_id = str(getattr(event, "request_id", "") or getattr(event, "requestId", "") or "")
        meta = self._pending.pop(request_id, None)
        if not meta or self._tab is None:
            return
        try:
            from nodriver import cdp

            body, is_base64 = await self._tab.send(cdp.network.get_response_body(request_id=request_id))
            if not body:
                return
            if is_base64:
                body = base64.b64decode(body).decode("utf-8", errors="replace")
            self.captured.append(
                CapturedResponse(
                    url=meta["url"],
                    status=meta["status"],
                    mime_type=meta.get("mime_type", ""),
                    body=str(body),
                    request_id=request_id,
                )
            )
            logger.debug("Sniffed %s bytes from %s", len(body), meta["url"][:80])
        except Exception as exc:
            logger.debug("get_response_body failed for %s: %s", meta.get("url", ""), exc)

    def clear(self) -> None:
        self.captured.clear()
        self._pending.clear()

    def bodies_matching(self, *needles: str) -> list[CapturedResponse]:
        out: list[CapturedResponse] = []
        for item in self.captured:
            url_lower = item.url.lower()
            if any(n.lower() in url_lower for n in needles):
                out.append(item)
        return out
