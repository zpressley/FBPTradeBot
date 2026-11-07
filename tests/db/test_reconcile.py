"""reconcile passes on a clean import and fails when the data and database disagree."""

import json
import os

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_reconcile_passes_after_import(run_script):
    assert run_script("migrate.py").returncode == 0
    imp = run_script("import_json.py")
    assert imp.returncode == 0, imp.stderr
    rec = run_script("reconcile.py", "--seed", "1")
    assert rec.returncode == 0, rec.stdout + rec.stderr
    for n in range(1, 8):
        assert any(line.startswith("PASS") and f" {n}. " in line for line in rec.stdout.splitlines()), \
            f"check {n} did not PASS:\n{rec.stdout}"
    assert "Known data issues" in rec.stdout


def test_reconcile_fails_when_json_disagrees(run_script, tmp_path):
    assert run_script("migrate.py").returncode == 0
    assert run_script("import_json.py").returncode == 0
    # temp repo root: everything symlinked to the real repo except one edited wizbucks.json
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    for p in (REPO_ROOT / "data").iterdir():
        if p.name != "wizbucks.json":
            os.symlink(p, root / "data" / p.name)
    os.symlink(REPO_ROOT / "config", root / "config")
    wiz = json.loads((REPO_ROOT / "data" / "wizbucks.json").read_text())
    team = next(iter(wiz))
    wiz[team] += 7
    (root / "data" / "wizbucks.json").write_text(json.dumps(wiz))

    rec = run_script("reconcile.py", "--seed", "1", "--repo-root", str(root))
    assert rec.returncode == 1, rec.stdout
    assert any(line.startswith("FAIL") and " 2. " in line for line in rec.stdout.splitlines()), rec.stdout
    assert any(line.startswith("PASS") and " 1. " in line for line in rec.stdout.splitlines())
