"""Shared fixtures for the database tests (need a local Postgres via DATABASE_URL)."""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def db_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    host = urlparse(url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1") and not os.environ.get("FBP_TEST_ALLOW_REMOTE"):
        # the import truncates every imported table: never do that to a shared database by accident
        pytest.skip(f"refusing to run import tests against non-local database {host!r} "
                    "(set FBP_TEST_ALLOW_REMOTE=1 to override)")
    return url


@pytest.fixture(scope="session")
def run_script(db_url):
    def run(script, *args, timeout=300):
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL_DIRECT"}
        env["DATABASE_URL"] = db_url
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "db" / script), *args],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=timeout,
        )

    return run
