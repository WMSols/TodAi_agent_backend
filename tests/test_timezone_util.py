from datetime import timezone

from todai.database.utils.tz import get_timezone


def test_utc_without_zoneinfo_db():
    assert get_timezone("UTC") == timezone.utc
    assert get_timezone("utc") == timezone.utc


def test_unknown_tz_falls_back_to_utc():
    assert get_timezone("Not/A_Real_Zone_XYZ") == timezone.utc
