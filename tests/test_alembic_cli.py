"""Alembic CLI smoke tests (no database required for heads/current chain)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_heads_shows_latest_revision() -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "heads"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "a7f6e5d4c3b2" in r.stdout


def test_alembic_upgrade_head_requires_sync_url() -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ALEMBIC_SYNC_URL", "PYTHONPATH")
    }
    env["PYTHONPATH"] = str(ROOT / "src")
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0
    assert "ALEMBIC_SYNC_URL" in (r.stderr + r.stdout)


@pytest.mark.integration
def test_alembic_upgrade_head_on_live_postgres() -> None:
    """Runs ``alembic upgrade head`` against ``ALEMBIC_SYNC_URL`` — use a local/disposable DB only."""
    url = os.environ.get("ALEMBIC_SYNC_URL", "").strip()
    if not url:
        pytest.skip("ALEMBIC_SYNC_URL not set (integration)")

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "ALEMBIC_SYNC_URL": url}
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
