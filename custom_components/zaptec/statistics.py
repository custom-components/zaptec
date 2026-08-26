"""Import Zaptec charge history into Home Assistant's long-term statistics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from http import HTTPStatus
import logging
from typing import TYPE_CHECKING, Any

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
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN, ZAPTEC_STATISTICS_BACKFILL_DAYS, ZAPTEC_STATISTICS_POLL_INTERVAL
from .zaptec import Charger, RequestError, ZaptecApiError

if TYPE_CHECKING:
    from .manager import ZaptecConfigEntry

_LOGGER = logging.getLogger(__name__)

RESUME_MARGIN = timedelta(hours=26)
"""Lookback added to the fetch window on resume - not a session-length limit.

The API filters by session *end* time, which can lag a session's final meter
timestamp slightly; querying from `last_start - RESUME_MARGIN` keeps such a
session in the window. No-double-count is guaranteed by bucket_sessions_hourly's
`after` filter regardless - this only bounds how far back we re-scan."""

_SUPPORTS_UNIT_CLASS = hasattr(StatisticsMeta, "unit_class")
"""HA core gained the statistics `unit_class` field around 2026.4 - feature-detect
it rather than pin a version."""


_HOUR_BOUNDARY_TOLERANCE = timedelta(seconds=5)
"""Snap tolerance before flooring, so a scheduled on-the-hour report landing a
few seconds either side of the boundary still buckets into the intended hour.

5s is safely between the two relevant scales: an order of magnitude above the
sub-second jitter observed on real on-the-hour reports (so it reliably catches
them), yet far below the minutes-scale gap between distinct reports (meter
interval is 30 min or hourly), so it can never merge two different reports."""


def _floor_hour(value: datetime) -> datetime:
    """Floor a datetime to the start of its UTC hour.

    Snaps up first if `value` is within `_HOUR_BOUNDARY_TOLERANCE` of the next
    hour, so a report timestamped a few seconds early/late buckets correctly.
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

    Each `energyDetails` point's `energy` is the incremental delta since the
    previous point (not a cumulative total; verified against the session's
    OCMF-signed meter readings). Reports arrive roughly hourly while metering
    but are skipped while idle, so gaps can span many hours. A delta is bucketed
    to the hour immediately *before* its own timestamp - where the energy was
    actually drawn, confirmed against the charger's power sensor - except a
    session's final, irregular point, which is not walked back past the previous
    point's hour. For back-to-back hourly reports both rules agree. A delta
    straddling an hour boundary lands wholly in one hour rather than being split,
    but this still fixes the live sensor's hour lag.

    Sessions without `energyDetails` (pre-3.2 firmware) fall back to a single
    point at `endDateTime` with the session's total `energy`; `voided`/`aborted`
    sessions are skipped (no meaningful energy).

    `sessions` must be oldest-first (guaranteed by the API). `after` is the start
    of the last hour already imported; points are skipped when their *floored
    hour* is <= `after`, not by raw timestamp - otherwise a mid-hour point like
    11:10 would be re-added to the already-stored 11:00 bucket and compound on
    every poll. `running_sum` is the kWh imported so far; returned points chain
    onto it so `sum` stays monotonic.
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
    """Imports one charger's archived sessions into HA long-term statistics.

    Independent of the live-state coordinators (coordinator.py), so a failure
    here (e.g. a non-Owner 403) degrades only the Energy Dashboard feed,
    not the whole integration.
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
        rows = last_stats.get(self.statistic_id) if last_stats else None
        last = rows[0] if rows else None
        if last is not None and (start_ts := last.get("start")) is not None:
            last_start = dt_util.utc_from_timestamp(start_ts)
            running_sum = last.get("sum") or 0.0
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
                # Owner-only endpoint: warn rather than fail the coordinator
                # every poll for non-Owner accounts.
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
