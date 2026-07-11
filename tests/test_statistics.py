"""Tests for statistics.py."""

from __future__ import annotations

from datetime import datetime, timezone

from custom_components.zaptec.statistics import bucket_sessions_hourly


def _session(
    session_id: str,
    points: list[tuple[str, float]],
    *,
    end: str | None = None,
    energy: float = 0.0,
) -> dict:
    """Build a raw archived-session dict with the given EnergyDetails points."""
    return {
        "Id": session_id,
        "EndDateTime": end,
        "Energy": energy,
        "EnergyDetails": [{"Timestamp": ts, "Energy": e} for ts, e in points],
    }


def test_single_session_within_one_hour() -> None:
    """A session with all points inside one hour produces a single bucket."""
    session = _session(
        "s1", [("2026-01-01T10:10:00+00:00", 1.0), ("2026-01-01T10:40:00+00:00", 2.5)]
    )

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 10, tzinfo=timezone.utc)  # noqa: UP017
    assert result[0]["state"] == 2.5  # noqa: PLR2004
    assert result[0]["sum"] == 2.5  # noqa: PLR2004


def test_session_spanning_two_hours_creates_two_buckets() -> None:
    """Points either side of an hour boundary land in different buckets."""
    session = _session(
        "s1",
        [
            ("2026-01-01T10:50:00+00:00", 1.0),
            ("2026-01-01T11:20:00+00:00", 1.6),
        ],
    )

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert [r["start"] for r in result] == [
        datetime(2026, 1, 1, 10, tzinfo=timezone.utc),  # noqa: UP017
        datetime(2026, 1, 1, 11, tzinfo=timezone.utc),  # noqa: UP017
    ]
    assert result[0]["state"] == 1.0
    assert result[0]["sum"] == 1.0
    assert result[1]["state"] == 0.6000000000000001  # noqa: PLR2004
    assert result[1]["sum"] == 1.6  # noqa: PLR2004


def test_after_cutoff_excludes_already_imported_points() -> None:
    """Points at or before `after` are skipped, avoiding double-counting."""
    session = _session(
        "s1",
        [
            ("2026-01-01T10:10:00+00:00", 1.0),
            ("2026-01-01T11:10:00+00:00", 2.0),
        ],
    )
    cutoff = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)  # noqa: UP017

    result = bucket_sessions_hourly([session], after=cutoff, running_sum=5.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 11, tzinfo=timezone.utc)  # noqa: UP017
    assert result[0]["state"] == 1.0
    assert result[0]["sum"] == 6.0  # noqa: PLR2004


def test_session_without_energy_details_falls_back_to_total() -> None:
    """A legacy session with no EnergyDetails uses its total Energy at EndDateTime."""
    session = {
        "Id": "s1",
        "EndDateTime": "2026-01-01T10:45:00+00:00",
        "Energy": 3.0,
        "EnergyDetails": [],
    }

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 10, tzinfo=timezone.utc)  # noqa: UP017
    assert result[0]["state"] == 3.0  # noqa: PLR2004


def test_running_sum_carries_across_sessions() -> None:
    """The running sum accumulates across multiple sessions, oldest first."""
    session1 = _session("s1", [("2026-01-01T10:10:00+00:00", 1.0)])
    session2 = _session("s2", [("2026-01-01T12:10:00+00:00", 2.0)])

    result = bucket_sessions_hourly([session1, session2], after=None, running_sum=10.0)

    assert [r["sum"] for r in result] == [11.0, 13.0]


def test_voided_and_aborted_sessions_are_skipped() -> None:
    """Voided/aborted sessions have no meaningful energy and must not be counted."""
    voided = _session("s1", [("2026-01-01T10:10:00+00:00", 1.0)])
    voided["Voided"] = True
    aborted = _session("s2", [("2026-01-01T11:10:00+00:00", 2.0)])
    aborted["Aborted"] = True
    real = _session("s3", [("2026-01-01T12:10:00+00:00", 3.0)])

    result = bucket_sessions_hourly([voided, aborted, real], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)  # noqa: UP017
    assert result[0]["state"] == 3.0  # noqa: PLR2004
