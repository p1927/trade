"""Stable request signatures for tiered API hub cache keys."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse


@dataclass(frozen=True)
class TieredRequest:
    """Fingerprint inputs for one vendor call (apikey/token stripped from params)."""

    method: str = "GET"
    url: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    body: str | bytes | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> dict[str, Any]:
        parsed = urlparse(self.url.strip())
        path = parsed.path.rstrip("/") or "/"
        host = (parsed.netloc or "").lower()
        scheme = (parsed.scheme or "https").lower()

        params = _strip_secrets(dict(self.params or {}))
        params_sorted = {k: _normalize_value(params[k]) for k in sorted(params)}

        body_hash = ""
        if self.body is not None:
            raw = self.body if isinstance(self.body, bytes) else self.body.encode("utf-8")
            body_hash = hashlib.sha256(raw).hexdigest()

        extra = {k: _normalize_value(self.extra[k]) for k in sorted(self.extra or {})}

        return {
            "method": (self.method or "GET").upper(),
            "scheme": scheme,
            "host": host,
            "path": path,
            "params": params_sorted,
            "body_hash": body_hash,
            "extra": extra,
        }


def _strip_secrets(params: dict[str, Any]) -> dict[str, Any]:
    secret_keys = {
        "apikey",
        "api_key",
        "token",
        "access_token",
        "authorization",
        "api_token",
    }
    return {k: v for k, v in params.items() if k.lower() not in secret_keys}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_normalize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    return str(value)


def request_hash(source: str, request: TieredRequest) -> str:
    """Return stable 16-char hex hash for cache path."""
    payload = {"source": source.strip().lower(), "request": request.normalized()}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def build_url(base: str, params: dict[str, Any] | None = None) -> str:
    """Build URL without secrets for fingerprinting."""
    parsed = urlparse(base)
    query = urlencode(_strip_secrets(dict(params or {})), doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))
