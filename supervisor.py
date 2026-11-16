"""Development supervisor for the combined CoBuilt Telegram worker.

Starts the Cement bot first so its SQLAlchemy metadata creates the shared
Cement tables, applies the additive development migration, then starts the
Concrete bot. Each bot keeps its own Telegram token and polling process.
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


def start_child(label, script, env):
    LOG.info("Starting %s", label)
    proc = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / script)],
        cwd=str(ROOT),
        env=env,
    )
    children.append(proc)
    return proc


def wait_for_cement_tables(timeout_seconds=90):
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline and not stopping:
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


def apply_development_migration():
    migration = ROOT / "migrations" / "dev" / "001_add_external_consumption_reference.sql"
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
    LOG.info("External consumption reference migration applied.")


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    required = ("BOT_TOKEN", "CEMENT_BOT_TOKEN", "DATABASE_URL")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    env = os.environ.copy()
    env["COBUILT_CEMENT_INTEGRATION_ENABLED"] = "true"
    env["COBUILT_SUPERVISED_WORKER"] = "true"
    env["DB_SCHEMA"] = "concrete_logistics"
    env["CEMENT_SCHEMA"] = "public"

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    cement = start_child("Cement Stock bot", "bot.py", env)
    try:
        wait_for_cement_tables()
        if stopping:
            return
        apply_development_migration()
        if cement.poll() is not None:
            raise RuntimeError("Cement Stock bot exited during initialization.")
        concrete = start_child(
            "Concrete Logistics bot",
            "concrete_app/concrete_logistics_bot.py",
            env,
        )

        while not stopping:
            for label, proc in (("Cement Stock", cement), ("Concrete Logistics", concrete)):
                code = proc.poll()
                if code is not None:
                    raise RuntimeError(f"{label} bot exited unexpectedly with status {code}.")
            time.sleep(2)
    finally:
        stop_children()


if __name__ == "__main__":
    main()
