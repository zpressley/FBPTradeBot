"""migrate is idempotent; import is fast and repeatable (Spec 01 acceptance criteria)."""

import time

import psycopg

SKIP_TABLES = {"import_runs", "schema_migrations"}


def table_counts(url):
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select table_name from information_schema.tables "
                    "where table_schema = 'public' and table_type = 'BASE TABLE' order by 1")
        names = [r[0] for r in cur.fetchall() if r[0] not in SKIP_TABLES]
        counts = {}
        for n in names:
            cur.execute(f'select count(*) from "{n}"')
            counts[n] = cur.fetchone()[0]
    return counts


def test_migrate_is_idempotent(run_script):
    first = run_script("migrate.py")
    assert first.returncode == 0, first.stderr
    second = run_script("migrate.py")
    assert second.returncode == 0, second.stderr
    assert "nothing to apply" in second.stdout


def test_import_is_fast_and_repeatable(run_script, db_url):
    assert run_script("migrate.py").returncode == 0
    t0 = time.time()
    first = run_script("import_json.py")
    elapsed = time.time() - t0
    assert first.returncode == 0, first.stderr
    assert elapsed < 120, f"import took {elapsed:.0f}s"
    before = table_counts(db_url)
    assert before["players"] > 0 and before["wb_ledger"] > 0

    second = run_script("import_json.py")
    assert second.returncode == 0, second.stderr
    assert table_counts(db_url) == before
