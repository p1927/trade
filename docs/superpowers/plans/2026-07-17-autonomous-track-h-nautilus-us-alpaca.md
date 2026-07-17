# Autonomous Track H — US Nautilus (Alpaca Data Client)

**Status:** Complete

**Goal:** US agents use Nautilus watch with Alpaca quote feed; execution stays Alpaca via Vibe.

## Shipped

- `alpaca_quote_feed.py`, `alpaca_live_data.py`, `AlpacaDataClientConfig`
- `factories.py`: `AlpacaLiveDataClientFactory`
- `nautilus_instruments.py`: US equity instruments
- `poll_loop.py`: `run_once_alpaca`
- `signal_actions.py` + `vibe_trigger.py`: US EXIT → Vibe revision (not OpenAlgo)
- `proposals.py`: US agents register in Nautilus watch + handoff sync
- `tests/test_alpaca_nautilus_feed.py`

## Verify

```bash
pytest tests/test_alpaca_nautilus_feed.py tests/test_autonomous_watch.py::test_us_agent_does_not_call_openalgo_poll -v
```
