#!/usr/bin/env python3
"""One-time production import of the legacy Concrete database into Cement DB.

Run only during the approved cutover, after the legacy Concrete bot is stopped.
The original source database is read-only and is never modified.
"""
import json
import os
import sys

import psycopg2
from psycopg2.extras import Json, execute_values

SCHEMA = "concrete_logistics"
MIGRATION_KEY = "2026-11-17-production-concrete-import-v1"
SOURCE_DSN = os.environ.get("CONCRETE_SOURCE_DATABASE_URL", "")
TARGET_DSN = os.environ.get("DATABASE_URL", "").replace(
    "postgresql+asyncpg://", "postgresql://", 1
)

TABLES = {
    "legacy_users": "users",
    "jobs": "jobs",
    "trucks": "trucks",
    "trips": "trips",
    "job_orders": "job_orders",
}


def rows(source_cur, sql):
    source_cur.execute(sql)
    return source_cur.fetchall()


def insert_rows(cur, table, columns, values):
    if not values:
        return
    column_sql = ", ".join('"' + c + '"' for c in columns)
    execute_values(
        cur,
        f'INSERT INTO "{SCHEMA}"."{table}" ({column_sql}) VALUES %s',
        values,
        page_size=500,
    )


def count_rows(cur, table):
    cur.execute(f'SELECT count(*) FROM "{SCHEMA}"."{table}"')
    return cur.fetchone()[0]


def main():
    if not SOURCE_DSN or not TARGET_DSN:
        raise RuntimeError(
            "Production import requires DATABASE_URL and CONCRETE_SOURCE_DATABASE_URL."
        )

    source = psycopg2.connect(SOURCE_DSN, connect_timeout=15, sslmode="require")
    target = psycopg2.connect(TARGET_DSN, connect_timeout=15)
    try:
        source.set_session(readonly=True, autocommit=False)
        with source.cursor() as src:
            src.execute("SET TIME ZONE 'UTC'")
            src.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
            source_tables = {r[0] for r in src.fetchall()}
            required = {"users", "jobs", "trucks", "trips", "job_orders"}
            missing = sorted(required - source_tables)
            if missing:
                raise RuntimeError("Concrete source is missing tables: " + ", ".join(missing))

            source_counts = {}
            for table in sorted(required):
                src.execute(f'SELECT count(*) FROM public."{table}"')
                source_counts[table] = src.fetchone()[0]

            data = {
                "legacy_users": rows(
                    src,
                    "SELECT user_id, username, role, joined_at::timestamptz "
                    "FROM public.users ORDER BY user_id",
                ),
                "jobs": rows(
                    src,
                    "SELECT id, name, status, created_at::timestamptz, "
                    "updated_at::timestamptz FROM public.jobs ORDER BY id",
                ),
                "trucks": rows(
                    src,
                    "SELECT id, plate, added_at::timestamptz "
                    "FROM public.trucks ORDER BY id",
                ),
                "trips": rows(
                    src,
                    "SELECT id, user_id, logged_at::timestamptz, job_name, "
                    "concrete_type, truck_plate, volume, concrete_grade "
                    "FROM public.trips ORDER BY id",
                ),
                "job_orders": rows(
                    src,
                    "SELECT id, job_name, concrete_grade, ordered_qty, created_at "
                    "FROM public.job_orders ORDER BY id",
                ),
            }

        with target:
            with target.cursor() as cur:
                cur.execute("SET LOCAL TIME ZONE 'UTC'")
                cur.execute("SELECT to_regclass('public.consumption'), to_regclass('public.users')")
                cement_tables = cur.fetchone()
                if not all(cement_tables):
                    raise RuntimeError(
                        "Target DATABASE_URL is not the initialized Cement database."
                    )

                cur.execute("SELECT to_regnamespace(%s)", (SCHEMA,))
                if cur.fetchone()[0] is not None:
                    cur.execute(
                        f'SELECT to_regclass(%s)', (f"{SCHEMA}._migration_history",)
                    )
                    history_exists = cur.fetchone()[0] is not None
                    if history_exists:
                        cur.execute(
                            f'SELECT counts FROM "{SCHEMA}"._migration_history WHERE migration_key=%s',
                            (MIGRATION_KEY,),
                        )
                        previous = cur.fetchone()
                        if previous:
                            print("Production Concrete import already completed; no changes made.")
                            return
                    raise RuntimeError(
                        f'Target schema "{SCHEMA}" already exists without a completed import marker. '
                        "Refusing to overwrite or merge automatically."
                    )

                cur.execute(f'CREATE SCHEMA "{SCHEMA}"')
                cur.execute(
                    f"""
                    CREATE TABLE "{SCHEMA}"._migration_history (
                        migration_key TEXT PRIMARY KEY,
                        completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        counts JSONB NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE "{SCHEMA}".users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        role TEXT NOT NULL DEFAULT 'worker',
                        joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE TABLE "{SCHEMA}".legacy_users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        role TEXT NOT NULL,
                        joined_at TIMESTAMPTZ NOT NULL
                    );
                    CREATE TABLE "{SCHEMA}".jobs (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ
                    );
                    CREATE TABLE "{SCHEMA}".trucks (
                        id SERIAL PRIMARY KEY,
                        plate TEXT NOT NULL UNIQUE,
                        added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE TABLE "{SCHEMA}".trips (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        job_name TEXT NOT NULL,
                        concrete_type TEXT NOT NULL DEFAULT '',
                        truck_plate TEXT NOT NULL,
                        volume REAL NOT NULL,
                        concrete_grade TEXT NOT NULL DEFAULT '',
                        source_message_id BIGINT
                    );
                    CREATE TABLE "{SCHEMA}".job_orders (
                        id SERIAL PRIMARY KEY,
                        job_name TEXT NOT NULL,
                        concrete_grade TEXT NOT NULL,
                        ordered_qty REAL NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX idx_jobs_status ON "{SCHEMA}".jobs(status);
                    CREATE INDEX idx_trips_logged ON "{SCHEMA}".trips(logged_at);
                    CREATE INDEX idx_trips_job ON "{SCHEMA}".trips(job_name);
                    CREATE INDEX idx_job_orders_name ON "{SCHEMA}".job_orders(job_name);
                    CREATE UNIQUE INDEX uq_trips_user_source_message
                        ON "{SCHEMA}".trips(user_id, source_message_id)
                        WHERE source_message_id IS NOT NULL;
                    """
                )

                insert_rows(
                    cur, "legacy_users",
                    ["user_id", "username", "role", "joined_at"], data["legacy_users"]
                )
                insert_rows(
                    cur, "jobs",
                    ["id", "name", "status", "created_at", "updated_at"], data["jobs"]
                )
                insert_rows(
                    cur, "trucks",
                    ["id", "plate", "added_at"], data["trucks"]
                )
                insert_rows(
                    cur, "trips",
                    ["id", "user_id", "logged_at", "job_name", "concrete_type",
                     "truck_plate", "volume", "concrete_grade"],
                    data["trips"],
                )
                insert_rows(
                    cur, "job_orders",
                    ["id", "job_name", "concrete_grade", "ordered_qty", "created_at"],
                    data["job_orders"],
                )

                for table in ("jobs", "trucks", "trips", "job_orders"):
                    cur.execute(
                        "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                        "COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) "
                        f'FROM "{SCHEMA}"."{table}"',
                        (f"{SCHEMA}.{table}",),
                    )

                target_counts = {
                    "users": count_rows(cur, "legacy_users"),
                    "jobs": count_rows(cur, "jobs"),
                    "trucks": count_rows(cur, "trucks"),
                    "trips": count_rows(cur, "trips"),
                    "job_orders": count_rows(cur, "job_orders"),
                }
                expected_counts = {
                    "users": source_counts["users"],
                    "jobs": source_counts["jobs"],
                    "trucks": source_counts["trucks"],
                    "trips": source_counts["trips"],
                    "job_orders": source_counts["job_orders"],
                }
                if target_counts != expected_counts:
                    raise RuntimeError(
                        f"Row-count verification failed: source={expected_counts}, target={target_counts}"
                    )
                cur.execute(
                    f'INSERT INTO "{SCHEMA}"._migration_history (migration_key, counts) VALUES (%s, %s)',
                    (MIGRATION_KEY, Json(target_counts)),
                )

        print("Production Concrete data import completed and verified.")
        print(json.dumps(expected_counts, sort_keys=True))
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"PRODUCTION IMPORT FAILED: {exc}", file=sys.stderr)
        raise
