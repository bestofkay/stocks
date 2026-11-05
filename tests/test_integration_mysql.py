"""MySQL integration tests — skipped unless portfolio_test is reachable.

Mark: integration
Requires DATABASE_URL pointing at an empty/test MySQL database and
alembic migrations applied.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def _mysql_available() -> bool:
    url = os.environ.get("DATABASE_URL", "")
    if "portfolio_test" not in url and os.environ.get("RUN_INTEGRATION") != "1":
        return False
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_mysql = pytest.mark.skipif(not _mysql_available(), reason="MySQL portfolio_test not available")


@requires_mysql
def test_db_ping():
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
