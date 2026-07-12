"""Tests for statistics.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
import pytest

from custom_components.zaptec.statistics import (
    ZaptecStatisticsCoordinator,
    _floor_hour,
    bucket_sessions_hourly,
)
from custom_components.zaptec.zaptec import Charger
from custom_components.zaptec.zaptec.exceptions import RequestError


def _session(
    session_id: str,
    points: list[tuple[str, float]],
    *,
    end: str | None = None,
    energy: float = 0.0,
) -> dict:
    """Build a raw archived-session dict with the given energyDetails points."""
    return {
        "id": session_id,
        "endDateTime": end,
        "energy": energy,
        "energyDetails": [{"timestamp": ts, "energy": e} for ts, e in points],
    }


def test_single_session_within_one_hour() -> None:
    """A session with all points inside one hour produces a single bucket.

    Each point's `energy` is already the delta for that interval (confirmed
    live against OCMF-signed meter readings), so same-hour points sum
    directly rather than differencing against the previous point.
    """
    session = _session(
        "s1", [("2026-01-01T10:10:00+00:00", 1.0), ("2026-01-01T10:40:00+00:00", 2.5)]
    )

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 10, tzinfo=timezone.utc)  # noqa: UP017
    assert result[0]["state"] == 3.5  # noqa: PLR2004
    assert result[0]["sum"] == 3.5  # noqa: PLR2004


def test_session_spanning_two_hours_creates_two_buckets() -> None:
    """Successive points landing in different hours produce separate buckets.

    For back-to-back hourly reports (no gap), "the hour before this point"
    and "the previous point's hour" are the same hour, so this is the
    baseline case both attribution rules agree on.
    """
    session = _session(
        "s1",
        [
            ("2026-01-01T10:05:00+00:00", 0.0),
            ("2026-01-01T11:00:00+00:00", 1.0),
            ("2026-01-01T12:00:00+00:00", 1.6),
        ],
    )

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert [r["start"] for r in result] == [
        datetime(2026, 1, 1, 10, tzinfo=timezone.utc),  # noqa: UP017
        datetime(2026, 1, 1, 11, tzinfo=timezone.utc),  # noqa: UP017
    ]
    assert result[0]["state"] == 1.0
    assert result[0]["sum"] == 1.0
    assert result[1]["state"] == 1.6  # noqa: PLR2004
    assert result[1]["sum"] == 2.6  # noqa: PLR2004


def test_delta_after_a_multi_hour_gap_is_attributed_to_the_hour_before_the_report() -> None:
    """A report following a multi-hour gap attributes to the hour before it.

    Live-confirmed (2026-07-12, cross-checked against the charger's power
    sensor): a report at 14:00 following a gap since 10:00 actually described
    charging that happened around 13:00, not 10:00 (the gap's start hour).
    """
    session = _session(
        "s1",
        [
            ("2026-01-01T09:00:00+00:00", 0.0),
            ("2026-01-01T10:00:00+00:00", 0.184),
            ("2026-01-01T14:00:00+00:00", 0.212),  # 4-hour gap since the last report
        ],
    )

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert [r["start"] for r in result] == [
        datetime(2026, 1, 1, 9, tzinfo=timezone.utc),  # noqa: UP017
        datetime(2026, 1, 1, 13, tzinfo=timezone.utc),  # noqa: UP017
    ]
    assert result[0]["state"] == 0.184  # noqa: PLR2004
    assert result[0]["sum"] == 0.184  # noqa: PLR2004
    assert result[1]["state"] == 0.212  # noqa: PLR2004
    assert result[1]["sum"] == 0.396  # noqa: PLR2004


def test_session_end_is_not_walked_back_past_the_previous_report() -> None:
    """A session's real end must use the previous report's hour, not its own.

    Unlike a report on a scheduled hourly tick, an irregular end timestamp
    isn't a tick to walk back an hour from.
    """
    session = _session(
        "s1",
        [
            ("2026-01-01T14:00:00+00:00", 0.835),
            ("2026-01-01T14:18:54+00:00", 0.279),
        ],
    )

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 14, tzinfo=timezone.utc)  # noqa: UP017
    assert result[0]["state"] == 0.835 + 0.279


def test_after_cutoff_excludes_already_imported_points() -> None:
    """A delta whose interval starts at or before `after` is skipped, avoiding double-counting."""
    session = _session(
        "s1",
        [
            ("2026-01-01T10:10:00+00:00", 0.0),
            ("2026-01-01T11:10:00+00:00", 1.0),
            ("2026-01-01T12:10:00+00:00", 2.0),
        ],
    )
    cutoff = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)  # noqa: UP017

    result = bucket_sessions_hourly([session], after=cutoff, running_sum=5.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 11, tzinfo=timezone.utc)  # noqa: UP017
    assert result[0]["state"] == 2.0  # noqa: PLR2004
    assert result[0]["sum"] == 7.0  # noqa: PLR2004


def test_after_cutoff_excludes_entire_last_imported_hour() -> None:
    """A delta whose raw timestamp is past `after` must still be excluded.

    This applies if its interval started within the already-imported hour
    (regression: was double-counting).
    """
    session = _session(
        "s1",
        [
            ("2026-01-01T11:50:00+00:00", 0.0),
            ("2026-01-01T12:50:00+00:00", 1.5),
            ("2026-01-01T13:50:00+00:00", 2.0),
        ],
    )
    after = datetime(2026, 1, 1, 11, tzinfo=timezone.utc)  # noqa: UP017

    result = bucket_sessions_hourly([session], after=after, running_sum=3.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)  # noqa: UP017
    assert result[0]["state"] == 2.0  # noqa: PLR2004
    assert result[0]["sum"] == 5.0  # noqa: PLR2004


def test_session_without_energy_details_falls_back_to_total() -> None:
    """A legacy session with no energyDetails uses its total energy at endDateTime."""
    session = {
        "id": "s1",
        "endDateTime": "2026-01-01T10:45:00+00:00",
        "energy": 3.0,
        "energyDetails": [],
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
    voided["voided"] = True
    aborted = _session("s2", [("2026-01-01T11:10:00+00:00", 2.0)])
    aborted["aborted"] = True
    real = _session("s3", [("2026-01-01T12:10:00+00:00", 3.0)])

    result = bucket_sessions_hourly([voided, aborted, real], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)  # noqa: UP017
    assert result[0]["state"] == 3.0  # noqa: PLR2004


def test_bucket_sessions_hourly_uses_camelcase_keys() -> None:
    """Regression test: /api/sessions/archived genuinely returns camelCase (confirmed live 2026-07-12), unlike every other Zaptec endpoint's PascalCase - this must keep working."""
    session = {
        "id": "b9b00000-0000-0000-0000-000000000000",
        "chargerId": "c1",
        "startDateTime": "2026-01-01T09:00:00+00:00",
        "endDateTime": "2026-01-01T10:00:00+00:00",
        "energy": 1.5,
        "energyDetails": [{"timestamp": "2026-01-01T09:30:00+00:00", "energy": 1.5}],
        "voided": False,
        "aborted": False,
    }

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["state"] == 1.5  # noqa: PLR2004


def test_floor_hour_snaps_reports_a_few_seconds_early_to_the_next_hour() -> None:
    """A report a few seconds early still floors to its intended hour.

    Zaptec reports land within ~200ms of the hour in practice, but nothing
    guarantees that across all firmware/devices.
    """
    noon = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)  # noqa: UP017
    eleven = datetime(2026, 1, 1, 11, tzinfo=timezone.utc)  # noqa: UP017

    # Comfortably inside the hour: floors normally, no snapping.
    assert _floor_hour(datetime(2026, 1, 1, 12, 0, 3, tzinfo=timezone.utc)) == noon  # noqa: UP017
    # A few seconds early: within tolerance, snaps up to the intended hour.
    assert _floor_hour(datetime(2026, 1, 1, 11, 59, 58, tzinfo=timezone.utc)) == noon  # noqa: UP017
    # Comfortably early (outside tolerance): floors down as normal.
    assert _floor_hour(datetime(2026, 1, 1, 11, 59, 50, tzinfo=timezone.utc)) == eleven  # noqa: UP017


def _make_charger(charger_id: str = "charger-1") -> MagicMock:
    """A fake Charger exposing only what the coordinator touches."""
    charger = MagicMock(spec=Charger)
    charger.id = charger_id
    charger.name = "My Charger"
    charger.qual_id = f"Charger[{charger_id}]"
    return charger


@pytest.mark.asyncio
async def test_statistic_id_derived_from_charger_id(hass: MagicMock, config_entry: Any) -> None:
    """The statistic_id is stable and derived from the charger's id."""
    charger = _make_charger("abc-123")
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)
    assert coordinator.statistic_id == "zaptec:energy_abc123"


@pytest.mark.asyncio
async def test_first_run_backfills_from_zero_sum(hass: MagicMock, config_entry: Any) -> None:
    """With no prior statistics, the coordinator starts from sum=0 and pages through results."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        return_value={
            "sessions": [
                {
                    "id": "s1",
                    "energyDetails": [{"timestamp": "2026-01-01T10:10:00+00:00", "energy": 2.0}],
                }
            ],
            "cursor": None,
            "hasMore": False,
        }
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
        patch("custom_components.zaptec.statistics.async_add_external_statistics") as mock_add,
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    assert mock_add.call_count == 1
    _hass_arg, metadata, statistics = mock_add.call_args[0]
    # StatisticMetaData and StatisticData are both TypedDicts (plain dicts at
    # runtime) - use dict-key access, not attribute access.
    assert metadata["statistic_id"] == "zaptec:energy_charger1"
    assert metadata["name"] == "My Charger Energy"
    assert len(statistics) == 1
    assert statistics[0]["sum"] == 2.0  # noqa: PLR2004
    charger.get_archived_sessions.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_new_sessions_does_not_call_add_statistics(
    hass: MagicMock, config_entry: Any
) -> None:
    """If there's nothing new to import, async_add_external_statistics is not called."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        return_value={"sessions": [], "cursor": None, "hasMore": False}
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
        patch("custom_components.zaptec.statistics.async_add_external_statistics") as mock_add,
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_resumes_from_last_statistics(hass: MagicMock, config_entry: Any) -> None:
    """A prior statistics entry sets the resume point and running sum."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        return_value={"sessions": [], "cursor": None, "hasMore": False}
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)
    last_start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch(
            "custom_components.zaptec.statistics.get_last_statistics",
            return_value={
                coordinator.statistic_id: [{"start": last_start.timestamp(), "sum": 42.0}]
            },
        ),
        patch("custom_components.zaptec.statistics.async_add_external_statistics"),
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    call_kwargs = charger.get_archived_sessions.call_args.kwargs
    assert call_kwargs["from_time"] == last_start - timedelta(hours=26)
    # to_time is "now" at call time - assert it's recent rather than exact.
    assert (dt_util.utcnow() - call_kwargs["to_time"]) < timedelta(seconds=5)


@pytest.mark.asyncio
async def test_forbidden_error_is_logged_not_raised(hass: MagicMock, config_entry: Any) -> None:
    """A 403 (non-Owner account) is logged and skipped, not raised as UpdateFailed.

    /api/sessions/archived requires the Owner role - many Zaptec accounts
    won't have it on every charger, and that shouldn't repeatedly fail the
    coordinator/spam the log with UpdateFailed errors on every poll.
    """
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        side_effect=RequestError("forbidden", HTTPStatus.FORBIDDEN)
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
        patch("custom_components.zaptec.statistics.async_add_external_statistics") as mock_add,
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_other_request_errors_raise_update_failed(
    hass: MagicMock, config_entry: Any
) -> None:
    """A non-403 RequestError still raises UpdateFailed, so HA surfaces it normally."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        side_effect=RequestError("server error", HTTPStatus.INTERNAL_SERVER_ERROR)
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()  # noqa: SLF001


@pytest.mark.asyncio
async def test_pages_through_multiple_results(hass: MagicMock, config_entry: Any) -> None:
    """Cursor from page 1 is threaded into page 2's request; both pages' sessions are imported."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        side_effect=[
            {
                "sessions": [
                    {
                        "id": "s1",
                        "energyDetails": [
                            {"timestamp": "2026-01-01T10:10:00+00:00", "energy": 1.0}
                        ],
                    }
                ],
                "cursor": "page2-cursor",
                "hasMore": True,
            },
            {
                "sessions": [
                    {
                        "id": "s2",
                        "energyDetails": [
                            {"timestamp": "2026-01-01T12:10:00+00:00", "energy": 2.0}
                        ],
                    }
                ],
                "cursor": None,
                "hasMore": False,
            },
        ]
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
        patch("custom_components.zaptec.statistics.async_add_external_statistics") as mock_add,
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    assert charger.get_archived_sessions.await_count == 2  # noqa: PLR2004
    first_call, second_call = charger.get_archived_sessions.await_args_list
    assert "cursor" not in first_call.kwargs or first_call.kwargs["cursor"] is None
    assert second_call.kwargs["cursor"] == "page2-cursor"

    _hass_arg, _metadata, statistics = mock_add.call_args[0]
    assert len(statistics) == 2  # noqa: PLR2004
    assert [s["sum"] for s in statistics] == [1.0, 3.0]


@pytest.mark.asyncio
async def test_metadata_includes_unit_class_when_supported(
    hass: MagicMock, config_entry: Any
) -> None:
    """unit_class is included in the metadata when the installed HA core supports it."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        return_value={
            "sessions": [
                {
                    "id": "s1",
                    "energyDetails": [{"timestamp": "2026-01-01T10:10:00+00:00", "energy": 2.0}],
                }
            ],
            "cursor": None,
            "hasMore": False,
        }
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
        patch("custom_components.zaptec.statistics.async_add_external_statistics") as mock_add,
        patch("custom_components.zaptec.statistics._SUPPORTS_UNIT_CLASS", new=True),
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    _hass_arg, metadata, _statistics = mock_add.call_args[0]
    assert "unit_class" in metadata


@pytest.mark.asyncio
async def test_metadata_omits_unit_class_when_unsupported(
    hass: MagicMock, config_entry: Any
) -> None:
    """unit_class is omitted entirely on older HA cores whose StatisticsMeta lacks the column."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        return_value={
            "sessions": [
                {
                    "id": "s1",
                    "energyDetails": [{"timestamp": "2026-01-01T10:10:00+00:00", "energy": 2.0}],
                }
            ],
            "cursor": None,
            "hasMore": False,
        }
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
        patch("custom_components.zaptec.statistics.async_add_external_statistics") as mock_add,
        patch("custom_components.zaptec.statistics._SUPPORTS_UNIT_CLASS", new=False),
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    _hass_arg, metadata, _statistics = mock_add.call_args[0]
    assert "unit_class" not in metadata
