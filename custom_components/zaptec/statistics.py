"""Import Zaptec charge history into Home Assistant's long-term statistics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from http import HTTPStatus
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import StatisticsMeta
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN, ZAPTEC_STATISTICS_BACKFILL_DAYS, ZAPTEC_STATISTICS_POLL_INTERVAL
from .zaptec import Charger, RequestError, ZaptecApiError

if TYPE_CHECKING:
    from .manager import ZaptecConfigEntry

_LOGGER = logging.getLogger(__name__)

RESUME_MARGIN = timedelta(hours=26)
"""Extra lookback window (beyond the last imported hour) when querying the API.

/api/sessions/archived filters by session *end* time, not start time,
so a session that closes slightly before our nominal `From` cutoff - but
still has energy points after it - would otherwise be missed. Correctness is
enforced by bucket_sessions_hourly()'s own `after` filter regardless; this is
purely a fetch-window optimization to avoid re-scanning all of history."""

_SUPPORTS_UNIT_CLASS = hasattr(StatisticsMeta, "unit_class")
"""Whether the installed HA core's StatisticsMeta accepts unit_class - absent
on HA < ~2026, a required kwarg (with a deprecation warning if omitted) as of
2026.4.3, moving to a hard requirement in HA 2026.11. Feature-detected rather
than version-compared since the exact introduction version isn't pinned down."""


_HOUR_BOUNDARY_TOLERANCE = timedelta(seconds=5)
"""Snap window applied before flooring to an hour, to absorb clock/reporting
jitter right at an hour boundary (e.g. a report meant to land at HH:00:00
arriving at HH-1:59:58 or HH:00:03). Chosen well under Zaptec's observed
reporting granularity (reports land within ~200ms of the hour in practice),
so it can't bleed into a neighboring, genuinely different report."""


def _floor_hour(value: datetime) -> datetime:
    """Floor a datetime down to the start of its hour, in UTC.

    Rounds to the nearest hour first if `value` is within
    `_HOUR_BOUNDARY_TOLERANCE` of one, so a report timestamped a few seconds
    early or late doesn't land in the wrong hour bucket.
    """
    value = dt_util.as_utc(value)
    floor = value.replace(minute=0, second=0, microsecond=0)
    if value - floor >= timedelta(hours=1) - _HOUR_BOUNDARY_TOLERANCE:
        floor += timedelta(hours=1)
    return floor


def bucket_sessions_hourly(
    sessions: list[dict[str, Any]],
    *,
    after: datetime | None,
    running_sum: float,
) -> list[StatisticData]:
    """Convert archived charge sessions into hourly external-statistics points.

    Each session's `energyDetails` is a list of `{timestamp, energy}` points
    where `energy` is *already* the incremental delta since the previous
    point, not a cumulative running total (confirmed live 2026-07-12 by
    diffing a session's energyDetails against its own OCMF-signed
    sessionSignature meter readings - the two matched exactly once the OCMF
    cumulative values were themselves differenced).

    Zaptec reports on a fixed hourly schedule while a session has metering
    activity, but silently skips ticks while paused/idle - so the gap
    between two points can be much longer than an hour. Live evidence
    (2026-07-12: real energyDetails cross-checked against the charger's own
    power sensor) showed that whenever a report follows such a gap, the
    energy it carries almost always happened in the hour immediately
    *before* that report, not spread across the whole gap and not at the
    gap's start - e.g. a report at 14:00 following the previous report at
    10:00 (a 4-hour gap) carried energy that the power sensor confirmed was
    drawn around 13:00-13:24, not 10:00. So each point's delta is bucketed
    by the hour immediately before its *own* timestamp - except a session's
    final, irregularly-timed point (its real end, not a scheduled tick) must
    not be walked back past the previous point's own hour, since that would
    misattribute a normal end-of-session interval backwards. Both rules
    collapse to the same answer for back-to-back hourly reports (no gap),
    so this is a refinement of - not a reversal of - that case. The first
    point in a session carries no delta to attribute (its `energy` is
    always 0, marking session start) and has no previous point, so it's
    bucketed by its own timestamp - harmless either way since it contributes
    nothing. This is still an approximation for intervals that don't land on
    a clean hour boundary (Zaptec's default meter-reporting interval is 30
    minutes): the whole delta is attributed to a single hour rather than
    split proportionally. This is still a large accuracy improvement over
    the live sensor, which can lag by 1+ hour (upstream issue #300).

    Sessions without `energyDetails` (e.g. pre-3.2 firmware) fall back to a
    single point at `endDateTime` using the session's total `energy`. Sessions
    marked `voided` or `aborted` are skipped entirely - per the API docs,
    "voided sessions have no meaningful duration or energy" (they exist when
    replaced by a corrected session, or when charging never actually started).

    `sessions` must be sorted oldest-first (the archived-sessions API
    guarantees this). `after` is the start of the last hour already imported
    by a previous run (its `sum` already reflects that whole hour) - so any
    point whose *floored hour* is at or before `after` is skipped, not just
    points with a raw timestamp at or before `after`. Skipping by raw
    timestamp would under-skip: a point at, say, 11:10 has a later raw
    timestamp than `after=11:00` and would slip through, getting re-added to
    an hour whose total was already stored - corrupting the external
    statistic with compounding phantom energy on every subsequent poll,
    since `/sessions/archived`'s `From` filter keeps returning the same
    session until a newer one supersedes it as the resume point. `running_sum`
    is the total energy (kWh) imported so far; the returned points chain onto
    it so the statistic's `sum` keeps increasing monotonically.
    """
    hourly_deltas: dict[datetime, float] = defaultdict(float)

    for session in sessions:
        if session.get("voided") or session.get("aborted"):
            continue

        details = session.get("energyDetails") or []
        if not details:
            end = session.get("endDateTime")
            energy = session.get("energy") or 0.0
            if end and energy:
                details = [{"timestamp": end, "energy": energy}]

        prev_timestamp: datetime | None = None
        for point in details:
            timestamp = dt_util.parse_datetime(point["timestamp"])
            if timestamp is None:
                continue
            delta = point["energy"]
            if prev_timestamp is None:
                hour = _floor_hour(timestamp)
            else:
                hour = max(
                    _floor_hour(timestamp) - timedelta(hours=1), _floor_hour(prev_timestamp)
                )
            prev_timestamp = timestamp
            if after is not None and hour <= after:
                continue
            hourly_deltas[hour] += delta

    statistics: list[StatisticData] = []
    for hour in sorted(hourly_deltas):
        running_sum += hourly_deltas[hour]
        statistics.append(StatisticData(start=hour, state=hourly_deltas[hour], sum=running_sum))
    return statistics


class ZaptecStatisticsCoordinator(DataUpdateCoordinator[None]):
    """Coordinator that imports one charger's archived sessions into HA statistics.

    Runs independently of the live-state coordinators in coordinator.py: it
    backdates hourly energy consumption from `/api/sessions/archived` into
    HA's external-statistics store, fixing the Energy Dashboard's
    misattributed-hour problem (upstream issue #300) caused by the live
    Energy Meter sensor's polling cadence.
    """

    config_entry: ZaptecConfigEntry

    def __init__(
        self, hass: HomeAssistant, *, entry: ZaptecConfigEntry, charger: Charger
    ) -> None:
        """Initialize the statistics coordinator for one charger."""
        self.charger = charger
        self.statistic_id = f"{DOMAIN}:energy_{charger.id.replace('-', '')}"
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}-statistics-{charger.qual_id}",
            update_interval=timedelta(seconds=ZAPTEC_STATISTICS_POLL_INTERVAL),
        )

    async def _async_update_data(self) -> None:
        """Fetch new archived sessions and import them as external statistics."""
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics,
            self.hass,
            1,
            self.statistic_id,
            True,  # noqa: FBT003
            {"sum"},
        )
        if last_stats:
            last = last_stats[self.statistic_id][0]
            last_start = dt_util.utc_from_timestamp(last["start"])
            running_sum = last["sum"] or 0.0
        else:
            last_start = dt_util.utcnow() - timedelta(days=ZAPTEC_STATISTICS_BACKFILL_DAYS)
            running_sum = 0.0

        sessions: list[dict[str, Any]] = []
        cursor: str | None = None
        try:
            while True:
                page = await self.charger.get_archived_sessions(
                    from_time=last_start - RESUME_MARGIN,
                    to_time=dt_util.utcnow(),
                    cursor=cursor,
                )
                sessions.extend(page.get("sessions") or [])
                if not page.get("hasMore"):
                    break
                cursor = page.get("cursor")
        except RequestError as err:
            if err.error_code == HTTPStatus.FORBIDDEN:
                # /api/sessions/archived requires the Owner role. Many Zaptec
                # accounts won't have it on every charger - log once per poll
                # rather than failing the coordinator every hour.
                _LOGGER.warning(
                    "No permission to read charge history for %s (requires Owner role), "
                    "skipping energy statistics import",
                    self.charger.qual_id,
                )
                return
            raise UpdateFailed(err) from err
        except ZaptecApiError as err:
            raise UpdateFailed(err) from err

        statistics = bucket_sessions_hourly(sessions, after=last_start, running_sum=running_sum)
        if not statistics:
            return

        metadata_kwargs: dict[str, Any] = {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": f"{self.charger.name} Energy",
            "source": DOMAIN,
            "statistic_id": self.statistic_id,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        }
        if _SUPPORTS_UNIT_CLASS:
            metadata_kwargs["unit_class"] = EnergyConverter.UNIT_CLASS
        metadata = StatisticMetaData(**metadata_kwargs)
        async_add_external_statistics(self.hass, metadata, statistics)
