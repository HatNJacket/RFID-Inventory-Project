"""Database engine and session management.

Azure SQL (SQL Server) in production, via pymssql -- chosen over pyodbc
because it needs no system ODBC driver, which Azure's Linux Python images
no longer ship. The engine is created lazily so the app can still start and
serve Shopify lookups even before DATABASE_URL is set (useful during local
development before the Azure database exists).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app import config


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None
# Set for real (pooled) databases only; the watchdog stays inert on the
# sqlite test/dev engines, which have no pool to exhaust.
_pool_capacity = None


def _normalize_url(url: str) -> str:
    """Accept a plain 'mssql://' URL and route it to the pymssql driver.
    'mssql+pymssql://' and 'sqlite:///' pass through untouched."""
    if url.startswith("mssql://"):
        return url.replace("mssql://", "mssql+pymssql://", 1)
    return url


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        if not config.DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set. Add it to .env (local) or App "
                "Service environment variables (Azure) before using the "
                "database."
            )
        url = _normalize_url(config.DATABASE_URL)
        kwargs = {"pool_pre_ping": True}
        if not url.startswith("sqlite"):
            # Azure SQL: recycle connections before the platform's idle
            # timeout kills them (pre_ping catches the corpse, but only
            # after a failed round trip), and give the pool enough headroom
            # for FastAPI's sync-endpoint threadpool so bursts queue on
            # the DB, not on checkout. pool_timeout 10 (was the default
            # 30): when the pool IS exhausted - the 2026-09-19 outage -
            # a fast failure beats 30s hangs that pile more threads on.
            kwargs.update(
                pool_recycle=1500, pool_size=10, max_overflow=20,
                pool_timeout=10, pool_use_lifo=True,
            )
            global _pool_capacity
            _pool_capacity = 10 + 20
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, autocommit=False
        )
    return _engine


def get_session():
    """FastAPI dependency: yields a session, always closes it.
    Raises a clean error (surfaced as HTTP 503 by the app) when no database
    is configured yet, instead of a raw 500."""
    if not config.DATABASE_URL:
        raise DatabaseNotConfigured()
    if _SessionLocal is None:
        get_engine()
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


class DatabaseNotConfigured(RuntimeError):
    """Raised when a DB-backed route is hit before DATABASE_URL is set."""


# Columns added to tables that ALREADY exist in a database (create_all
# only creates missing TABLES). Each entry: (table, column, DDL type) -
# nullable only, so old rows and old code both stay valid. Applied
# idempotently at startup on every engine (Azure SQL prod, the dev
# twin's sqlite, test sqlite) - this replaces the one-off ALTER scripts
# for these columns.
_COLUMN_UPGRADES = [
    ("rfid_sold_ledger", "source", "VARCHAR(16) NULL"),
    ("rfid_sold_ledger", "ss_order_id", "VARCHAR(32) NULL"),
    ("rfid_sold_ledger", "ss_shipments", "VARCHAR(2000) NULL"),
    ("rfid_sold_ledger", "ss_line_qty", "INTEGER NULL"),
    ("rfid_label_names", "barcode_mode", "VARCHAR(10) NULL"),
    ("rfid_label_names", "bin_text", "VARCHAR(100) NULL"),
    ("rfid_label_names", "barcode_text", "VARCHAR(64) NULL"),
]


def _apply_column_upgrades(engine) -> None:
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        for table, column, ddl in _COLUMN_UPGRADES:
            if table not in inspector.get_table_names():
                continue  # create_all just made it, columns included
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column in existing:
                continue
            # Two gunicorn workers can race this ALTER; the loser's
            # duplicate-column error is harmless.
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD {column} {ddl}"
                    ))
            except Exception:  # noqa: BLE001 — lost the race, column exists
                pass
    except Exception:  # noqa: BLE001 — never block startup on an upgrade
        import logging
        logging.getLogger("rfid.db").exception("column upgrade failed")


def init_db() -> None:
    """Create tables if they don't exist, then add any new columns this
    build expects on pre-existing tables. Fine for now; move to Alembic
    migrations once the schema churns harder."""
    from app import models  # noqa: F401  (register models on Base)

    Base.metadata.create_all(bind=get_engine())
    _apply_column_upgrades(get_engine())


def database_configured() -> bool:
    return bool(config.DATABASE_URL)


def start_pool_watchdog(logger) -> None:
    """Self-heal for the failure that killed the site 2026-09-19 -> 23:
    every pooled connection was checked out and never returned (the
    database itself sat idle), so each request waited out pool_timeout
    and died - for DAYS, because nothing inside a worker can reclaim a
    connection some thread still holds. This daemon watches checkout
    saturation; if the pool stays completely maxed for four minutes
    straight, it dumps EVERY thread's stack to the log (the who-held-
    what evidence the outage never left behind) and hard-exits the
    worker. Gunicorn respawns it in seconds with a fresh pool - a blip
    instead of a dead weekend. Inert on sqlite (no pool to watch)."""
    import faulthandler
    import os
    import sys
    import threading
    import time

    def _run():
        bad_since = None
        while True:
            time.sleep(15)
            try:
                if _engine is None or _pool_capacity is None:
                    continue
                maxed = _engine.pool.checkedout() >= _pool_capacity
            except Exception:  # noqa: BLE001 - the watchdog never dies
                continue
            if not maxed:
                bad_since = None
                continue
            if bad_since is None:
                bad_since = time.time()
                continue
            held = time.time() - bad_since
            if held < 240:
                continue
            try:
                logger.error(
                    "POOL WATCHDOG: all %d connections checked out for "
                    "%.0fs straight - dumping thread stacks, then "
                    "restarting this worker for a fresh pool.",
                    _pool_capacity, held,
                )
                faulthandler.dump_traceback(file=sys.stderr)
                sys.stderr.flush()
            finally:
                os._exit(3)

    threading.Thread(target=_run, daemon=True).start()
