from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_database(path: str | Path) -> sqlite3.Connection:
    database_path = Path(path)
    if str(database_path) != ":memory:":
        database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    if str(database_path) != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
    return connection
