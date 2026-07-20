#!/usr/bin/env python3
"""One-shot migration: point trade-stack callers at trade_integrations.http."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def w(rel: str, content: str) -> None:
    path = ROOT / rel
    path.write_text(content, encoding="utf-8")
    print(f"wrote {rel}")


def patch(rel: str, old: str, new: str, *, count: int = 1) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"pattern not found in {rel}: {old[:80]!r}")
    path.write_text(text.replace(old, new, count), encoding="utf-8")
    print(f"patched {rel}")


# --- core modules already created; patch callers ---

patch(
    "integrations/trade_integrations/dataflows/throttled_http.py",
    "import requests\n",
    "from trade_integrations.http import HTTPError, RequestException, get\n",
)
patch(
    "integrations/trade_integrations/dataflows/throttled_http.py",
    "            resp = requests.get(url, headers={\"User-Agent\": _UA}, timeout=timeout)\n            if resp.status_code in _RETRYABLE_STATUS:\n                raise requests.HTTPError(f\"{resp.status_code} for {url}\", response=resp)",
    "            resp = get(url, headers={\"User-Agent\": _UA}, timeout=timeout)\n            if resp.status_code in _RETRYABLE_STATUS:\n                raise HTTPError(f\"{resp.status_code} for {url}\", response=resp)",
)
patch(
    "integrations/trade_integrations/dataflows/throttled_http.py",
    "        except requests.HTTPError as exc:",
    "        except HTTPError as exc:",
)
patch(
    "integrations/trade_integrations/dataflows/throttled_http.py",
    "        except (requests.Timeout, requests.ConnectionError) as exc:",
    "        except RequestException as exc:",
)

patch(
    "integrations/trade_integrations/tiered_api/http.py",
    "import requests\n",
    "from trade_integrations.http import get, post\n",
)
patch(
    "integrations/trade_integrations/tiered_api/http.py",
    "        resp = requests.get(url, params=call_params, headers=hdrs, timeout=timeout)",
    "        resp = get(url, params=call_params, headers=hdrs, timeout=timeout)",
)
patch(
    "integrations/trade_integrations/tiered_api/http.py",
    "        resp = requests.post(url, json=json_body, headers=hdrs, timeout=timeout)",
    "        resp = post(url, json=json_body, headers=hdrs, timeout=timeout)",
)

patch(
    "integrations/trade_integrations/dataflows/searxng_client.py",
    "    requests.get(SEARXNG_BASE_URL + \"/search\", ...)",
    "    import requests; requests.get(...)",
)
patch(
    "integrations/trade_integrations/dataflows/searxng_client.py",
    "import requests\n\nfrom trade_integrations.context.hub import get_hub_dir",
    "from trade_integrations.context.hub import get_hub_dir\nfrom trade_integrations.http import RequestException, get",
)
patch(
    "integrations/trade_integrations/dataflows/searxng_client.py",
    "            resp = requests.get(url, params=params, timeout=timeout)\n            resp.raise_for_status()\n        except requests.RequestException as exc:",
    "            resp = get(url, params=params, timeout=timeout)\n            resp.raise_for_status()\n        except RequestException as exc:",
)

patch(
    "integrations/trade_integrations/openalgo/rest_client.py",
    "    def post(self, path: str, payload: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:\n        import requests\n\n        url = f\"{self.host}/api/v1/{path.lstrip('/')}\"\n        last_exc: Exception | None = None\n        for attempt in range(2):\n            try:\n                response = requests.post(url, json=payload, timeout=timeout)\n                body = response.json() if response.content else {}\n            except requests.RequestException as exc:",
    "    def post(self, path: str, payload: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:\n        from trade_integrations.http import PoolKind, RequestException, post\n\n        url = f\"{self.host}/api/v1/{path.lstrip('/')}\"\n        last_exc: Exception | None = None\n        for attempt in range(2):\n            try:\n                response = post(url, json=payload, timeout=timeout, pool=PoolKind.OPENALGO)\n                body = response.json() if response.content else {}\n            except RequestException as exc:",
)

# index research sources
for rel, old, new in [
    (
        "integrations/trade_integrations/dataflows/index_research/macro_global.py",
        "        import requests\n\n        end = today",
        "        from trade_integrations.http import get\n\n        end = today",
    ),
    (
        "integrations/trade_integrations/dataflows/index_research/macro_global.py",
        "        response = requests.get(",
        "        response = get(",
    ),
    (
        "integrations/trade_integrations/dataflows/index_research/sources/weights_nse.py",
        "        import requests\n    except ImportError:",
        "        from trade_integrations.http import get\n    except ImportError:",
    ),
    (
        "integrations/trade_integrations/dataflows/index_research/sources/weights_nse.py",
        "        response = requests.get(",
        "        response = get(",
    ),
    (
        "integrations/trade_integrations/dataflows/index_research/sources/gdelt_events.py",
        "        import requests\n    except ImportError:",
        "        from trade_integrations.http import get\n    except ImportError:",
    ),
    (
        "integrations/trade_integrations/dataflows/index_research/sources/gdelt_events.py",
        "        response = requests.get(url, timeout=60)",
        "        response = get(url, timeout=60)",
    ),
    (
        "integrations/trade_integrations/dataflows/company_research/sources/sentiment.py",
        "        import requests\n\n        scores = []",
        "        from trade_integrations.http import post\n\n        scores = []",
    ),
    (
        "integrations/trade_integrations/dataflows/company_research/sources/sentiment.py",
        "            response = requests.post(",
        "            response = post(",
    ),
    (
        "integrations/trade_integrations/monitor/execution_ledger.py",
        "    import requests\n\n    host = os.getenv",
        "    from trade_integrations.http import PoolKind, post\n\n    host = os.getenv",
    ),
    (
        "integrations/trade_integrations/monitor/execution_ledger.py",
        "        response = requests.post(\n            f\"{host}/api/v1/positionbook\",\n            json={\"apikey\": api_key},\n            timeout=15,\n        )",
        "        response = post(\n            f\"{host}/api/v1/positionbook\",\n            json={\"apikey\": api_key},\n            timeout=15,\n            pool=PoolKind.OPENALGO,\n        )",
    ),
]:
    patch(rel, old, new)

print("done")
