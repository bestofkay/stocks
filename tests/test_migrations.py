"""Migration sanity checks (no live MySQL required)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]


def test_alembic_has_linear_revisions():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    revisions = list(script.walk_revisions())
    assert len(revisions) >= 1
    heads = script.get_heads()
    assert len(heads) == 1
    assert "001_initial" in heads[0] or heads[0] == "001_initial"


def test_initial_migration_defines_required_tables():
    path = ROOT / "migrations" / "versions" / "001_initial_schema.py"
    text = path.read_text(encoding="utf-8")
    for table in [
        "users",
        "providers",
        "broker_connections",
        "broker_accounts",
        "holdings",
        "portfolio_snapshots",
        "transactions",
        "sync_runs",
        "sync_errors",
        "audit_logs",
        "sync_schedules",
        "user_sessions",
    ]:
        assert f'"{table}"' in text or f"'{table}'" in text
