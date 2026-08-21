import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scraper'))

# The scraper/dashboard modules pick their backend from DATABASE_URL at import
# time. Tests always run against a throwaway SQLite file, never a live database.
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('EQUIPMENT_DB_PATH', '/tmp/plate-magnet-tests/equipment.db')

import pytest  # noqa: E402


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    """A fresh, migrated SQLite database."""
    import db as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', tmp_path / 'test.db')
    conn = db_module.connect()
    db_module.init_schema(conn)
    yield conn
    conn.close()


PASSWORD = 'correct-horse-battery-staple'
