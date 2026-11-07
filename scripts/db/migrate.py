"""Apply db/migrations/*.sql in order and record them in schema_migrations.

Re-running is a no-op: a migration is applied once, keyed by its file name.
Each migration runs in its own transaction, so a failing file leaves the
database at the previous migration.

Usage:
    python scripts/db/migrate.py [--database-url URL] [--dry-run]

Uses DATABASE_URL_DIRECT if set, else DATABASE_URL (migrations should use
the direct, non-pooled Neon string; see db/README.md).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.db.common import connect, direct_database_url  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"


def pending_migrations(conn) -> list[Path]:
    with conn.cursor() as cur:
        cur.execute(
            """
            create table if not exists schema_migrations (
              version    text primary key,
              applied_at timestamptz not null default now()
            )
            """
        )
        cur.execute("select version from schema_migrations")
        applied = {row[0] for row in cur.fetchall()}
    conn.commit()
    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())
    return [p for p in files if p.name not in applied]


def apply_migration(conn, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "insert into schema_migrations (version) values (%s)", (path.name,)
            )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--dry-run", action="store_true", help="list pending migrations, apply nothing")
    args = ap.parse_args(argv)

    url = direct_database_url(args.database_url)
    with connect(url) as conn:
        todo = pending_migrations(conn)
        if not todo:
            print("schema_migrations is up to date; nothing to apply.")
            return 0
        for path in todo:
            if args.dry_run:
                print(f"would apply {path.name}")
                continue
            apply_migration(conn, path)
            print(f"applied {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
