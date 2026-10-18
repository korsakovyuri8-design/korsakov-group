from datetime import datetime, timezone

from app.journey import date_resolver


NOW = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)


def test_upcoming_range_without_year_uses_current_year():
    out = date_resolver.resolve_date_range(
        "20–21 October",
        now_utc=NOW,
        timezone="Europe/Podgorica",
    )
    assert out == {
        "status": "resolved",
        "start_date": "2026-10-20",
        "end_date": "2026-10-21",
        "timezone": "Europe/Podgorica",
        "basis": "inferred_upcoming_date",
        "reference_date": "2026-10-17",
        "year_stated": False,
    }


def test_past_month_day_rolls_to_next_year():
    out = date_resolver.resolve_date_range(
        "10–11 October",
        now_utc=NOW,
        timezone="Europe/Podgorica",
    )
    assert out["start_date"] == "2027-10-10"
    assert out["end_date"] == "2027-10-11"
    assert out["basis"] == "inferred_upcoming_date"


def test_explicit_year_is_never_reinterpreted():
    out = date_resolver.resolve_date_range(
        "20–21 October 2028",
        now_utc=NOW,
        timezone="Europe/Podgorica",
    )
    assert out["start_date"] == "2028-10-20"
    assert out["end_date"] == "2028-10-21"
    assert out["basis"] == "stated_year"
    assert out["year_stated"] is True


def test_bad_date_is_not_guessed():
    assert date_resolver.resolve_date_range(
        "31 February",
        now_utc=NOW,
        timezone="Europe/Podgorica",
    ) is None
