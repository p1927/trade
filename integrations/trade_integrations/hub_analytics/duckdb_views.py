"""Register DuckDB views over reports/hub parquet ledgers (read-only analytics)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb

from trade_integrations.context.hub import get_hub_dir

HUB_VIEWS = (
    "index_predictions",
    "options_predictions",
    "outcomes",
    "executions",
    "fills",
    "index_factors_daily",
    "ticks_daily",
    "news_daily",
    "derivatives_chain_daily",
)

_BUILTIN_QUERIES: dict[str, str] = {
    "strategy_pnl": """
        SELECT
            COALESCE(strategy, '(unknown)') AS strategy,
            COUNT(*) AS closes,
            ROUND(AVG(CAST(net_pnl_inr AS DOUBLE)), 2) AS avg_net_pnl_inr,
            ROUND(SUM(CAST(net_pnl_inr AS DOUBLE)), 2) AS total_net_pnl_inr,
            SUM(CASE WHEN CAST(net_pnl_inr AS DOUBLE) > 0 THEN 1 ELSE 0 END) AS wins
        FROM outcomes
        WHERE net_pnl_inr IS NOT NULL
        GROUP BY 1
        ORDER BY closes DESC, total_net_pnl_inr DESC
    """,
    "execution_pnl": """
        SELECT
            COALESCE(strategy, '(unknown)') AS strategy,
            COUNT(*) AS executions,
            SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
            ROUND(AVG(CAST(realized_pnl_inr AS DOUBLE)), 2) AS avg_realized_pnl_inr,
            ROUND(SUM(CAST(realized_pnl_inr AS DOUBLE)), 2) AS total_realized_pnl_inr
        FROM executions
        GROUP BY 1
        ORDER BY executions DESC
    """,
    "index_accuracy": """
        SELECT
            COUNT(*) AS forecasts,
            SUM(CASE WHEN direction_correct = true THEN 1 ELSE 0 END) AS direction_hits,
            ROUND(
                100.0 * SUM(CASE WHEN direction_correct = true THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0),
                2
            ) AS direction_hit_rate_pct,
            ROUND(AVG(CAST(actual_return_pct AS DOUBLE)), 4) AS avg_actual_return_pct
        FROM index_predictions
        WHERE actual_return_pct IS NOT NULL
    """,
    "options_accuracy": """
        SELECT
            underlying,
            COUNT(*) AS picks,
            SUM(CASE WHEN direction_correct = true THEN 1 ELSE 0 END) AS direction_hits,
            ROUND(
                100.0 * SUM(CASE WHEN direction_correct = true THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0),
                2
            ) AS direction_hit_rate_pct
        FROM options_predictions
        WHERE direction_correct IS NOT NULL
        GROUP BY 1
        ORDER BY picks DESC
    """,
    "fills_by_symbol": """
        SELECT
            symbol,
            COUNT(*) AS fills,
            ROUND(SUM(CAST(qty AS DOUBLE) * CAST(price AS DOUBLE)), 2) AS notional_inr
        FROM fills
        GROUP BY 1
        ORDER BY fills DESC
        LIMIT 25
    """,
    "execution_outcome_join": """
        SELECT
            e.execution_id,
            e.underlying,
            e.strategy,
            e.status,
            e.executed_at,
            e.closed_at,
            e.realized_pnl_inr,
            o.net_pnl_inr AS outcome_net_pnl_inr,
            o.intent_source
        FROM executions e
        LEFT JOIN outcomes o
            ON o.widget_id = e.widget_id
            AND o.action = 'CLOSE'
        ORDER BY e.executed_at DESC
        LIMIT 50
    """,
}

_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|COPY|ATTACH|DETACH|INSTALL|LOAD|EXPORT|IMPORT|PRAGMA|CALL)\b",
    re.IGNORECASE,
)


def _resolve_readable_path(path: Path) -> Path | None:
    if path.is_file():
        return path
    csv_path = path.with_suffix(".csv")
    if csv_path.is_file():
        return csv_path
    return None


def _read_fn(path: Path) -> str:
    resolved = _resolve_readable_path(path)
    if resolved is None:
        return "SELECT NULL WHERE false"
    escaped = str(resolved).replace("'", "''")
    if resolved.suffix.lower() == ".csv":
        return f"read_csv_auto('{escaped}')"
    return f"read_parquet('{escaped}')"


def _register_daily_glob_view(
    con: duckdb.DuckDBPyConnection,
    view_name: str,
    directory: Path,
) -> None:
    if directory.is_dir() and any(directory.glob("*.parquet")):
        glob_path = str(directory / "*.parquet").replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT * FROM read_parquet('{glob_path}', union_by_name=true)"
        )
    elif directory.is_dir() and any(directory.glob("*.csv")):
        glob_path = str(directory / "*.csv").replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT * FROM read_csv_auto('{glob_path}', union_by_name=true)"
        )
    else:
        con.execute(f"CREATE OR REPLACE VIEW {view_name} AS SELECT NULL WHERE false")


def hub_data_paths() -> dict[str, Path]:
    data = get_hub_dir() / "_data"
    return {
        "index_predictions": data / "index_predictions" / "ledger.parquet",
        "options_predictions": data / "options_predictions" / "ledger.parquet",
        "outcomes": data / "auto_paper" / "outcomes.parquet",
        "executions": data / "trades" / "executions.parquet",
        "fills": data / "trades" / "fills.parquet",
        "index_factors_daily": data / "index_factors" / "daily",
        "ticks_daily": data / "ticks" / "daily",
        "news_daily": data / "news" / "daily",
        "derivatives_chain_daily": data / "derivatives_chain" / "daily",
    }


def register_hub_views(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Create or replace views over hub parquet/csv ledgers. Returns registered view names."""
    paths = hub_data_paths()
    registered: list[str] = []

    single_views = {
        "index_predictions": paths["index_predictions"],
        "options_predictions": paths["options_predictions"],
        "outcomes": paths["outcomes"],
        "executions": paths["executions"],
        "fills": paths["fills"],
    }
    for view_name, path in single_views.items():
        source = _read_fn(path)
        con.execute(f"CREATE OR REPLACE VIEW {view_name} AS SELECT * FROM {source}")
        registered.append(view_name)

    daily_dir = paths["index_factors_daily"]
    _register_daily_glob_view(con, "index_factors_daily", daily_dir)
    registered.append("index_factors_daily")

    for view_name, key in (
        ("ticks_daily", "ticks_daily"),
        ("news_daily", "news_daily"),
        ("derivatives_chain_daily", "derivatives_chain_daily"),
    ):
        _register_daily_glob_view(con, view_name, paths[key])
        registered.append(view_name)
    return registered


def get_hub_connection(*, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Return an in-memory DuckDB connection with hub views registered."""
    con = duckdb.connect(database=":memory:", read_only=False)
    register_hub_views(con)
    return con


def list_views() -> list[str]:
    return list(HUB_VIEWS)


def validate_readonly_sql(sql: str) -> None:
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise ValueError("empty SQL")
    if _FORBIDDEN_SQL.search(text):
        raise ValueError("mutating or privileged SQL keywords are not allowed")
    upper = text.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH") or upper.startswith("DESCRIBE")):
        raise ValueError("only SELECT / WITH / DESCRIBE queries are allowed")
    if ";" in text:
        raise ValueError("multiple SQL statements are not allowed")


def execute_readonly_query(sql: str, *, limit: int = 500) -> dict[str, Any]:
    """Run a read-only SQL query against hub views."""
    validate_readonly_sql(sql)
    con = get_hub_connection()
    try:
        relation = con.execute(sql)
        columns = [col[0] for col in (relation.description or [])]
        rows = relation.fetchmany(limit + 1)
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]
        serializable = [dict(zip(columns, row)) for row in rows]
        return {
            "columns": columns,
            "rows": serializable,
            "row_count": len(serializable),
            "truncated": truncated,
        }
    finally:
        con.close()


def run_builtin_query(name: str, *, limit: int = 500) -> dict[str, Any]:
    """Execute a named built-in analytics query."""
    key = name.strip().lower()
    if key not in _BUILTIN_QUERIES:
        known = ", ".join(sorted(_BUILTIN_QUERIES))
        raise ValueError(f"unknown builtin query {name!r}; known: {known}")
    return execute_readonly_query(_BUILTIN_QUERIES[key], limit=limit)


def list_builtin_queries() -> list[str]:
    return sorted(_BUILTIN_QUERIES)
