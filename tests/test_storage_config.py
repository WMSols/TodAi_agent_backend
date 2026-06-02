"""Storage backend flag parsing."""

import os

from todai.database.config import use_local_storage
from todai.database.repositories.supabase.helpers import resolve_db_user_id


def test_use_local_default_true(monkeypatch):
    monkeypatch.delenv("LOCAL", raising=False)
    monkeypatch.delenv("TODAI_LOCAL", raising=False)
    assert use_local_storage() is True


def test_use_local_false(monkeypatch):
    monkeypatch.setenv("LOCAL", "false")
    assert use_local_storage() is False


def test_resolve_db_user_default():
    assert resolve_db_user_id("default") == "00000000-0000-0000-0000-000000000001"
