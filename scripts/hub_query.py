#!/usr/bin/env python3
"""Run read-only DuckDB SQL against hub parquet views."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.env import load_trade_env
from trade_integrations.hub_analytics.duckdb_views import (
    execute_readonly_query,
    list_builtin_queries,
    list_views,
    run_builtin_query,
)


def _print_table(result: dict) -> None:
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if not columns:
        print("(no columns)")
        return
    widths = [len(str(col)) for col in columns]
    for row in rows:
        for idx, col in enumerate(columns):
            widths[idx] = max(widths[idx], len(str(row.get(col, ""))))
    header = " | ".join(str(col).ljust(widths[i]) for i, col in enumerate(columns))
    print(header)
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(str(row.get(col, "")).ljust(widths[i]) for i, col in enumerate(columns)))
    if result.get("truncated"):
        print(f"... truncated at {result.get('row_count')} rows")


def main() -> int:
    parser = argparse.ArgumentParser(description="Query hub parquet ledgers via DuckDB views")
    parser.add_argument("sql", nargs="?", help="Read-only SQL (SELECT / WITH / DESCRIBE)")
    parser.add_argument("--builtin", "-b", help="Run a named built-in query")
    parser.add_argument("--list-views", action="store_true", help="List registered view names")
    parser.add_argument("--list-builtins", action="store_true", help="List built-in query names")
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    parser.add_argument("--limit", type=int, default=500, help="Max rows returned")
    args = parser.parse_args()

    load_trade_env()

    if args.list_views:
        print("\n".join(list_views()))
        return 0
    if args.list_builtins:
        print("\n".join(list_builtin_queries()))
        return 0
    if args.builtin:
        result = run_builtin_query(args.builtin, limit=args.limit)
    elif args.sql:
        result = execute_readonly_query(args.sql, limit=args.limit)
    else:
        parser.error("provide SQL or --builtin")

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_table(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
