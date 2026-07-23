#!/usr/bin/env bash
# Run Nautilus bridge unit tests + optional live OpenAlgo dry-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Unit tests"
python -m pytest tests/test_nautilus_bridge_models.py tests/test_nautilus_vibe_trigger.py tests/test_nautilus_execute.py tests/test_nautilus_handoff.py tests/test_nautilus_preflight.py tests/test_nautilus_intent_queue.py tests/test_nautilus_stop_eval.py tests/test_nautilus_reconcile.py tests/test_nautilus_risk_state.py -q

NAUTILUS_PY="${ROOT}/.venv-nautilus/bin/python"
if [[ -x "$NAUTILUS_PY" ]]; then
  echo "==> Nautilus node config smoke (.venv-nautilus)"
  TRADE_INTEGRATIONS_SKIP_APPLY=1 PYTHONPATH="${ROOT}/integrations" "$NAUTILUS_PY" -c "
from nautilus_openalgo_bridge.node import build_trading_node_config
from nautilus_openalgo_bridge.factories import OpenAlgoLiveDataClientFactory
from nautilus_trader.live.node import TradingNode
cfg = build_trading_node_config(agent_id='aa_test')
node = TradingNode(config=cfg)
node.add_data_client_factory('OPENALGO', OpenAlgoLiveDataClientFactory)
node.build()
print('node config ok:', cfg.trader_id)
"
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

if [[ -n "${OPENALGO_API_KEY:-}" ]]; then
  echo "==> Skipping legacy poll dry-run (removed — use verify_autonomous_integration for live node)"
else
  echo "==> Skipping live verify (OPENALGO_API_KEY not set)"
fi

if [[ -n "${OPENALGO_API_KEY:-}" && -x "${ROOT}/.venv-nautilus/bin/python" ]]; then
  if [[ "${VERIFY_NAUTILUS_INTEGRATION:-1}" != "0" ]]; then
    echo "==> Full integration verify (Nautilus watch ON + alert→Vibe + optional US)"
    echo "    (set VERIFY_NAUTILUS_INTEGRATION=0 to skip — takes ~2–4 min)"
    NAUTILUS_WATCH_ENABLE=true ./scripts/verify_autonomous_integration.sh --skip-unit || {
      echo "Integration verification failed — see log/verify_nautilus_watch.log" >&2
      exit 1
    }
  fi
fi

echo "==> All bridge checks passed"
