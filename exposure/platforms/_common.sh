#!/usr/bin/env bash
# Shared platform webhook helpers.

set -euo pipefail

EXPOSURE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/common.sh
source "$EXPOSURE_ROOT/lib/common.sh"

platform_base_url() {
  local override="${1:-}"
  if [[ -n "$override" ]]; then
    normalize_public_url "$override"
    return 0
  fi

  local from_state
  from_state="$(read_exposure_state public_url)"
  if [[ -n "$from_state" ]]; then
    echo "$from_state"
    return 0
  fi

  local from_env
  from_env="$(read_host_server)"
  if [[ -n "$from_env" ]]; then
    normalize_public_url "$from_env"
    return 0
  fi

  echo ""
}

platform_require_public_url() {
  local base
  base="$(platform_base_url "${1:-}")"
  if [[ -z "$base" ]]; then
    exposure_fail "No public URL configured"
    echo "  Start a tunnel: ./exposure/start.sh quick" >&2
    echo "  Or set HOST_SERVER in openalgo/.env" >&2
    return 1
  fi
  echo "$base"
}

platform_is_localhost() {
  local url="$1"
  [[ "$url" == *"127.0.0.1"* || "$url" == *"localhost"* ]]
}

platform_print_urls() {
  local name="$1"
  shift
  local override=""
  if [[ $# -gt 0 && "$1" == http* ]]; then
    override="$1"
    shift
  fi

  local base
  base="$(platform_require_public_url "$override")" || return 1

  if platform_is_localhost "$base"; then
    exposure_warn "$name webhooks will not work with localhost — start a tunnel first"
  fi

  echo ""
  echo "$name webhook endpoints (base: $base)"
  while [[ $# -ge 2 ]]; do
    local label="$1"
    local path="$2"
    echo "  $label -> ${base}${path}"
    shift 2
  done
}
