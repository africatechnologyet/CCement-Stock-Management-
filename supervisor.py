"""Supervisor for the combined CoBuilt Cement and Concrete Telegram worker.

Initializes Cement, applies environment-specific schema migrations, optionally
imports legacy Concrete production data once, then starts the Concrete bot.
"""
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import psycopg2

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("cobuilt.supervisor")
children = []
stopping = False
LOCK_KEY = 0x434F4255494C54  # Stable PostgreSQL advisory-lock key for this worker.


def database_dsn():
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def stop_children():
    global children
    for child in reversed(children):
        if child.poll() is None:
            LOG.info("Stopping child process %s", child.args[-1])
            child.terminate()
    for child in reversed(children):
        if child.poll() is None:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                LOG.warning("Force-killing child process %s", child.args[-1])
                child.kill()
                child.wait(timeout=5)
    children = []


def handle_signal(signum, _frame):
    global stopping
    stopping = True
    LOG.info("Received signal %s; stopping both bots", signum)
    stop_children()


def acquire_singleton_lock():
    """Ensure overlapping Render deploys never run two polling supervisors."""
    conn = psycopg2.connect(database_dsn(), connect_timeout=10)
    conn.autocommit = True
    while not stopping:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
                acquired = cur.fetchone()[0]
            if acquired:
                LOG.info("Acquired the CoBuilt worker singleton lock.")
                return conn
        except Exception:
            conn.close()
            raise
        LOG.warning("Another CoBuilt worker still holds the singleton lock; waiting before starting Telegram bots.")
        time.sleep(3)
    conn.close()
    return None


def start_child(label, script, env):
    LOG.info("Starting %s", label)
    proc = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / script)],
        cwd=str(ROOT),
        env=env,
    )
    children.append(proc)
    return proc


def wait_for_cement_tables(cement_process, timeout_seconds=90):
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline and not stopping:
        if cement_process.poll() is not None:
            raise RuntimeError(f"Cement Stock bot exited during database initialization with status {cement_process.returncode}.")
        conn = None
        try:
            conn = psycopg2.connect(database_dsn(), connect_timeout=5)
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.consumption')")
                if cur.fetchone()[0]:
                    LOG.info("Cement tables are initialized.")
                    return
        except Exception as exc:
            last_error = exc
        finally:
            if conn is not None:
                conn.close()
        time.sleep(2)
    raise RuntimeError(f"Cement database tables did not initialize in time: {last_error}")


def apply_consumption_migration():
    environment = os.getenv("COBUILT_ENVIRONMENT", "development").lower()
    migration_dir = "production" if environment == "production" else "dev"
    migration = ROOT / "migrations" / migration_dir / "001_add_external_consumption_reference.sql"
    sql = migration.read_text(encoding="utf-8")
    conn = psycopg2.connect(database_dsn(), connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    LOG.info("%s Cement consumption migration applied.", environment.title())


def ensure_production_concrete_import():
    if os.getenv("COBUILT_ENVIRONMENT", "development").lower() != "production":
        return

    import_enabled = os.getenv("COBUILT_RUN_PRODUCTION_IMPORT", "false").lower() == "true"
    if import_enabled:
        script = ROOT / "migrations" / "production" / "import_concrete_data.py"
        LOG.warning("Running guarded one-time production Concrete data import.")
        subprocess.run([sys.executable, str(script)], cwd=str(ROOT), check=True)
        return

    conn = psycopg2.connect(database_dsn(), connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='concrete_logistics' AND table_name='_migration_history'"
            )
            if not cur.fetchone():
                raise RuntimeError(
                    "Production Concrete data import is not complete. "
                    "Set COBUILT_RUN_PRODUCTION_IMPORT=true for the approved cutover."
                )
            cur.execute(
                'SELECT 1 FROM concrete_logistics._migration_history '
                "WHERE migration_key=%s",
                ("2026-11-17-production-concrete-import-v1",),
            )
            if not cur.fetchone():
                raise RuntimeError("Production Concrete import marker is missing.")
    finally:
        conn.close()
    LOG.info("Verified production Concrete data import marker.")


def validate_production_import_script():
    script = ROOT / "migrations" / "production" / "import_concrete_data.py"
    compile(script.read_text(encoding="utf-8"), str(script), "exec")
    LOG.info("Production data-import script syntax check passed.")


def main():
    validate_production_import_script()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    environment = os.getenv("COBUILT_ENVIRONMENT", "development").lower()
    cement_token = os.getenv("CEMENT_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
    concrete_token = os.getenv("CONCRETE_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
    missing = []
    if not os.getenv("DATABASE_URL"):
        missing.append("DATABASE_URL")
    if not cement_token:
        missing.append("BOT_TOKEN or CEMENT_BOT_TOKEN")
    if not concrete_token or (environment == "production" and not os.getenv("CONCRETE_BOT_TOKEN")):
        missing.append("CONCRETE_BOT_TOKEN" if environment == "production" else "BOT_TOKEN or CONCRETE_BOT_TOKEN")
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    base_env = os.environ.copy()
    base_env["COBUILT_CEMENT_INTEGRATION_ENABLED"] = "true"
    base_env["COBUILT_SUPERVISED_WORKER"] = "true"
    base_env["DB_SCHEMA"] = "concrete_logistics"
    base_env["CEMENT_SCHEMA"] = "public"

    cement_env = base_env.copy()
    cement_env["BOT_TOKEN"] = cement_token
    cement_env["CEMENT_BOT_TOKEN"] = cement_token

    concrete_env = base_env.copy()
    concrete_env["BOT_TOKEN"] = concrete_token
    concrete_env["CEMENT_BOT_TOKEN"] = cement_token

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    lock_conn = acquire_singleton_lock()
    if lock_conn is None:
        return
    try:
        cement = start_child("Cement Stock bot", "bot.py", cement_env)
        wait_for_cement_tables(cement)
        if stopping:
            return
        apply_consumption_migration()
        ensure_production_concrete_import()
        if cement.poll() is not None:
            raise RuntimeError("Cement Stock bot exited during initialization.")
        concrete = start_child(
            "Concrete Logistics bot",
            "concrete_app/concrete_logistics_bot.py",
            concrete_env,
        )

        while not stopping:
            for label, proc in (("Cement Stock", cement), ("Concrete Logistics", concrete)):
                code = proc.poll()
                if code is not None:
                    raise RuntimeError(f"{label} bot exited unexpectedly with status {code}.")
            time.sleep(2)
    finally:
        stop_children()
        lock_conn.close()


if __name__ == "__main__":
    main()
