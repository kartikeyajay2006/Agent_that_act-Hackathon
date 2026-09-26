#!/bin/bash
# Schema + a least-privilege monitoring role used by the MCP server's
# database health tool (pg_monitor can read pg_stat_activity, nothing more).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE TABLE IF NOT EXISTS payments (
        id              uuid PRIMARY KEY,
        customer_id     text        NOT NULL,
        amount_cents    integer     NOT NULL,
        currency        char(3)     NOT NULL,
        status          text        NOT NULL,
        service_version text        NOT NULL,
        created_at      timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS payment_audit (
        id           bigserial   PRIMARY KEY,
        payment_id   uuid        NOT NULL,
        customer_id  text        NOT NULL,
        amount_cents integer     NOT NULL,
        event        text        NOT NULL,
        created_at   timestamptz NOT NULL DEFAULT now()
    );

    CREATE ROLE forgesre_monitor LOGIN PASSWORD '${MONITOR_PASSWORD}';
    GRANT pg_monitor TO forgesre_monitor;
    -- Operators keep access even when application pools exhaust max_connections.
    GRANT pg_use_reserved_connections TO forgesre_monitor;
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO forgesre_monitor;
SQL
