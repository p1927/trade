#!/usr/bin/env bash
# Load canonical stack ports from stack/ports.yaml (via generated .stack.ports.env).

stack_ports_root() {
  if [[ -n "${STACK_ROOT:-}" ]]; then
    echo "$STACK_ROOT"
    return
  fi
  if [[ -n "${ROOT:-}" ]]; then
    echo "$ROOT"
    return
  fi
  local here="${BASH_SOURCE[0]}"
  echo "$(cd "$(dirname "$here")/.." && pwd)"
}

stack_ports_pick_python() {
  local root py
  root="$(stack_ports_root)"
  py="$root/.venv/bin/python"
  if [[ -x "$py" ]]; then
    echo "$py"
    return
  fi
  echo "python3"
}

stack_ensure_ports_env() {
  local root yaml env_file py
  root="$(stack_ports_root)"
  yaml="$root/stack/ports.yaml"
  env_file="$root/.stack.ports.env"
  py="$(stack_ports_pick_python)"

  if [[ ! -f "$yaml" ]]; then
    echo "[stack] missing $yaml" >&2
    return 1
  fi

  if [[ ! -f "$env_file" ]] || [[ "$yaml" -nt "$env_file" ]]; then
    if ! "$py" "$root/scripts/sync_stack_ports.py" --write-env; then
      echo "[stack] failed to sync stack/ports.yaml — try: pip install pyyaml" >&2
      return 1
    fi
  fi

  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

stack_validate_ports_registry() {
  local root py
  root="$(stack_ports_root)"
  py="$(stack_ports_pick_python)"
  if ! "$py" "$root/scripts/sync_stack_ports.py" --check 2>/dev/null; then
    echo "[stack] stack/ports.yaml has conflicts — fix registry then: trade sync-ports" >&2
    return 1
  fi
  return 0
}

stack_check_port_listeners() {
  local root py strict="${STACK_PORTS_STRICT:-0}"
  strict="$(printf '%s' "$strict" | tr '[:upper:]' '[:lower:]')"
  root="$(stack_ports_root)"
  py="$(stack_ports_pick_python)"
  if [[ "$strict" == "1" || "$strict" == "true" || "$strict" == "yes" || "$strict" == "on" ]]; then
    if ! "$py" "$root/scripts/sync_stack_ports.py" --check-listeners; then
      echo "[stack] free conflicting ports or stop the foreign process" >&2
      return 1
    fi
  else
    "$py" "$root/scripts/sync_stack_ports.py" --check-listeners 2>/dev/null || {
      echo "[stack] warning: some registry ports are in use (set STACK_PORTS_STRICT=1 to fail)" >&2
      "$py" "$root/scripts/sync_stack_ports.py" --check-listeners 2>&1 | sed 's/^/[stack] /' >&2 || true
    }
  fi
  return 0
}

stack_print_ports_summary() {
  local root py
  root="$(stack_ports_root)"
  py="$(stack_ports_pick_python)"
  "$py" - "$root" <<'PY' 2>/dev/null || true
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "integrations"))
from trade_integrations.stack_ports import load_ports_registry
reg = load_ports_registry(root=str(root))
print("[stack] ports (stack/ports.yaml):")
for name, spec in reg["services"].items():
    print(f"  {name:18} :{spec['host_port']}  {spec.get('description') or ''}")
PY
}
