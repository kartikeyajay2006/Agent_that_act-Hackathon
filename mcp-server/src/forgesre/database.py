"""PostgreSQL health via the least-privilege forgesre_monitor role (pg_monitor)."""

from __future__ import annotations

from typing import Any

import psycopg

from .results import ToolError

_ACTIVITY_SQL = """
SELECT coalesce(nullif(application_name, ''), backend_type) AS client,
       coalesce(state, 'n/a') AS state,
       count(*) AS connections,
       round(max(extract(epoch FROM now() - state_change))::numeric, 1) AS oldest_state_age_s
FROM pg_stat_activity
WHERE datname IS NOT NULL
GROUP BY 1, 2
ORDER BY connections DESC
"""


def database_health(dsn: str) -> dict[str, Any]:
    try:
        with psycopg.connect(dsn, application_name="forgesre-mcp", connect_timeout=3) as conn:
            conn.execute("SET statement_timeout = '3s'")
            max_conn = int(conn.execute("SHOW max_connections").fetchone()[0])
            reserved = int(conn.execute("SHOW superuser_reserved_connections").fetchone()[0])
            reserved += int(conn.execute("SHOW reserved_connections").fetchone()[0])
            rows = conn.execute(_ACTIVITY_SQL).fetchall()
    except psycopg.OperationalError as exc:
        msg = str(exc).splitlines()[0][:300]
        code = "DB_CONNECTIONS_EXHAUSTED" if "too many" in msg.lower() else "DB_UNREACHABLE"
        raise ToolError(code, f"cannot query PostgreSQL: {msg}") from exc
    clients: dict[str, dict[str, Any]] = {}
    total = 0
    for client, state, n, age in rows:
        total += n
        c = clients.setdefault(client, {"client": client, "connections": 0, "by_state": {}})
        c["connections"] += n
        c["by_state"][state] = {"count": n, "oldest_state_age_seconds": float(age) if age is not None else None}
    ranked = sorted(clients.values(), key=lambda c: c["connections"], reverse=True)
    for c in ranked:
        c["share_of_max_connections"] = round(c["connections"] / max_conn, 3)
    return {
        "max_connections": max_conn,
        "reserved_connections": reserved,
        "total_connections": total,
        "utilization": round(total / max_conn, 3),
        "available_for_clients": max_conn - reserved - total,
        "connections_by_client": ranked[:15],
    }
