#!/usr/bin/env python3
"""
Migration runner for Bootball.

Usage:
    python scripts/migrate.py            # apply all pending migrations
    python scripts/migrate.py --status   # show applied / pending
    python scripts/migrate.py --mark-applied
        # mark all existing migrations as applied without running them
        # use this once on a DB that was bootstrapped from schema.sql

Migrations live in migrations/ and are named NNN_description.sql.
Applied migrations are tracked in the schema_migrations table.
"""
import argparse
import hashlib
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from src.storage.db import get_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER NOT NULL PRIMARY KEY,
    name        TEXT    NOT NULL,
    applied_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checksum    TEXT    NOT NULL
);
"""


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:16]


def _ensure_tracking_table(conn):
    conn.execute(text(TRACKING_DDL))
    conn.commit()


def _applied_versions(conn) -> set[int]:
    rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {r[0] for r in rows}


def _collect_migrations() -> list[tuple[int, str, Path]]:
    """Return sorted list of (version, name, path) for all NNN_*.sql files."""
    entries = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        stem = path.stem
        parts = stem.split("_", 1)
        if not parts[0].isdigit():
            continue
        version = int(parts[0])
        name = parts[1] if len(parts) > 1 else stem
        entries.append((version, name, path))
    return sorted(entries, key=lambda x: x[0])


def cmd_status():
    engine = get_engine()
    with engine.connect() as conn:
        _ensure_tracking_table(conn)
        applied = _applied_versions(conn)

    migrations = _collect_migrations()
    print(f"\n{'VER':>4}  {'STATUS':<10}  NAME")
    print("-" * 50)
    for version, name, _ in migrations:
        status = "applied" if version in applied else "PENDING"
        marker = "  " if version in applied else "* "
        print(f"{marker}{version:>3}  {status:<10}  {name}")
    print()

    pending = [m for m in migrations if m[0] not in applied]
    if pending:
        print(f"{len(pending)} migration(s) pending.")
    else:
        print("All migrations applied.")


def cmd_apply(dry_run: bool = False):
    engine = get_engine()
    migrations = _collect_migrations()

    with engine.connect() as conn:
        _ensure_tracking_table(conn)
        applied = _applied_versions(conn)

        pending = [(v, n, p) for v, n, p in migrations if v not in applied]
        if not pending:
            logger.info("No pending migrations.")
            return

        for version, name, path in pending:
            sql = path.read_text()
            chk = _checksum(sql)
            logger.info("Applying migration %03d: %s", version, name)

            if dry_run:
                logger.info("  [dry-run] would execute %s", path.name)
                continue

            try:
                for statement in sql.split(";"):
                    stmt = statement.strip()
                    if stmt:
                        conn.execute(text(stmt))

                conn.execute(
                    text(
                        "INSERT INTO schema_migrations (version, name, applied_at, checksum) "
                        "VALUES (:version, :name, :applied_at, :checksum)"
                    ),
                    {"version": version, "name": name,
                     "applied_at": datetime.utcnow().isoformat(), "checksum": chk},
                )
                conn.commit()
                logger.info("  Applied %03d: %s [%s]", version, name, chk)
            except Exception:
                logger.exception("  FAILED on migration %03d: %s", version, name)
                logger.error("  Aborting — fix the migration and retry.")
                sys.exit(1)

    if not dry_run:
        logger.info("All pending migrations applied.")


def cmd_mark_applied():
    """Record all migrations as applied without executing SQL.

    Use this once on a database bootstrapped from schema.sql so the
    runner knows everything is already in place.
    """
    engine = get_engine()
    migrations = _collect_migrations()

    with engine.connect() as conn:
        _ensure_tracking_table(conn)
        applied = _applied_versions(conn)

        marked = 0
        for version, name, path in migrations:
            if version in applied:
                logger.info("Skip %03d %s (already recorded)", version, name)
                continue
            sql = path.read_text()
            chk = _checksum(sql)
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, name, applied_at, checksum) "
                    "VALUES (:version, :name, :applied_at, :checksum)"
                ),
                {"version": version, "name": name,
                 "applied_at": datetime.utcnow().isoformat(), "checksum": chk},
            )
            conn.commit()
            logger.info("Marked %03d: %s [%s]", version, name, chk)
            marked += 1

    logger.info("Marked %d migration(s) as applied.", marked)


def main():
    parser = argparse.ArgumentParser(description="Bootball migration runner")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="Show applied / pending migrations")
    group.add_argument(
        "--mark-applied",
        action="store_true",
        help="Mark all migrations as applied without executing (use after schema.sql bootstrap)",
    )
    group.add_argument("--dry-run", action="store_true", help="Show what would be applied")
    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.mark_applied:
        cmd_mark_applied()
    elif args.dry_run:
        cmd_apply(dry_run=True)
    else:
        cmd_apply()


if __name__ == "__main__":
    main()
