#!/usr/bin/env bash
# Report Cloudflare tunnel and OpenAlgo HOST_SERVER exposure state.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$ROOT/lib/common.sh"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  OpenAlgo exposure status"
echo "══════════════════════════════════════════════════════════"

local_url="$(openalgo_local_url)"
if probe_openalgo; then
  exposure_ok "OpenAlgo local   $local_url"
else
  exposure_fail "OpenAlgo local   not reachable at $local_url"
fi

host_server="$(read_host_server)"
if [[ -n "$host_server" ]]; then
  if [[ "$host_server" == *"127.0.0.1"* || "$host_server" == *"localhost"* ]]; then
    exposure_warn "HOST_SERVER        $host_server (localhost — external webhooks blocked)"
  else
    exposure_ok "HOST_SERVER        $host_server"
  fi
else
  exposure_warn "HOST_SERVER        not set in openalgo/.env"
fi

mode="$(read_exposure_state mode)"
public_url="$(read_exposure_state public_url)"
tunnel_pid="$(read_exposure_state pid)"

if [[ -n "$mode" ]]; then
  if [[ -n "$tunnel_pid" ]] && kill -0 "$tunnel_pid" 2>/dev/null; then
    exposure_ok "Tunnel ($mode)     pid $tunnel_pid"
    if [[ -n "$public_url" ]]; then
      exposure_ok "Public URL         $public_url"
    fi
  else
    exposure_warn "Tunnel state stale (mode=$mode, pid not running)"
  fi
elif pgrep -x cloudflared >/dev/null 2>&1; then
  exposure_warn "cloudflared running but not tracked by exposure scripts"
else
  exposure_warn "No active tunnel — run: ./exposure/start.sh quick"
fi

echo ""
echo "Platform URLs: ./exposure/start.sh urls all"
echo ""
