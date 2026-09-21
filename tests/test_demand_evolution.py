# tests/test_demand_evolution.py
#
# Unit tests for the pure bucketing logic in
# app/services/demand_evolution.py. The DB-facing calculate() function
# needs a live session and is covered by manual/integration verification;
# _bucket_by_month is pure and fully testable here.

from datetime import datetime, timezone

from app.services.demand_evolution import _bucket_by_month


def test_bucket_by_month_returns_oldest_to_newest():
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    buckets = _bucket_by_month([], now, months=3)
    assert [b["month"] for b in buckets] == ["2026-04", "2026-05", "2026-06"]
    assert all(b["count"] == 0 for b in buckets)


def test_bucket_by_month_counts_timestamps_in_correct_month():
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    timestamps = [
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 10, tzinfo=timezone.utc),
        datetime(2026, 5, 20, tzinfo=timezone.utc),
    ]
    buckets = _bucket_by_month(timestamps, now, months=3)
    by_month = {b["month"]: b["count"] for b in buckets}
    assert by_month["2026-06"] == 2
    assert by_month["2026-05"] == 1
    assert by_month["2026-04"] == 0


def test_bucket_by_month_ignores_timestamps_outside_window():
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    timestamps = [datetime(2025, 1, 1, tzinfo=timezone.utc)]  # way outside a 3-month window
    buckets = _bucket_by_month(timestamps, now, months=3)
    assert sum(b["count"] for b in buckets) == 0


def test_bucket_by_month_handles_year_boundary():
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    buckets = _bucket_by_month([], now, months=3)
    assert [b["month"] for b in buckets] == ["2025-11", "2025-12", "2026-01"]
