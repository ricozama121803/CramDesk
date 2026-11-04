import os
from datetime import date, datetime

import pytest


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets its own empty crm home (config, data, cache)."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "120")
    yield tmp_path


def dt(s: str) -> datetime:
    """'2026-10-14 09:30' -> datetime"""
    return datetime.fromisoformat(s)


# A Monday-to-Sunday week safely in the past, used across tests.
MON = date(2026, 10, 12)
