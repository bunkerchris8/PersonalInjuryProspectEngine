from __future__ import annotations

from dataclasses import replace

import pytest

from src.config import load_settings
from src.database import connect_database, initialize_schema


@pytest.fixture
def settings(tmp_path):
    return replace(load_settings(), database_path=tmp_path / "test.db")


@pytest.fixture
def connection(settings):
    database = connect_database(settings.database_path)
    initialize_schema(database)
    yield database
    database.close()

