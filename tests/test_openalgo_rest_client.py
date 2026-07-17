"""Tests for the canonical OpenAlgo REST client."""


def test_rest_client_post_success(monkeypatch):
    from trade_integrations.openalgo.rest_client import OpenAlgoRestClient

    class FakeResp:
        ok = True
        content = b'{"status":"success","data":{"ltp":100}}'

        def json(self):
            return {"status": "success", "data": {"ltp": 100}}

    monkeypatch.setenv("OPENALGO_API_KEY", "test-key")
    monkeypatch.setenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    monkeypatch.setattr(
        "requests.post",
        lambda url, json, timeout: FakeResp(),
    )
    client = OpenAlgoRestClient()
    body = client.post("quotes", {"symbol": "NIFTY", "exchange": "NSE_INDEX"})
    assert body["data"]["ltp"] == 100
