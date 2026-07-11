"""Import Zaptec charge history into Home Assistant's long-term statistics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from http import HTTPStatus
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
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


def _floor_hour(value: datetime) -> datetime:
    """Floor a datetime down to the start of its hour, in UTC."""
    return dt_util.as_utc(value).replace(minute=0, second=0, microsecond=0)


def bucket_sessions_hourly(
    sessions: list[dict[str, Any]],
    *,
    after: datetime | None,
    running_sum: float,
) -> list[StatisticData]:
    """Convert archived charge sessions into hourly external-statistics points.

    Each session's `EnergyDetails` is a list of `{Timestamp, Energy}` points
    where `Energy` is cumulative *within that session* (starts near 0). This
    turns those into per-hour consumption deltas, bucketed by the hour
    containing each point's timestamp. That's an approximation: a delta
    between two points less than an hour apart can span an hour boundary
    (Zaptec's default meter-reporting interval is 30 minutes), so a small
    amount of energy can be attributed to the following hour rather than
    split proportionally. This is still a large accuracy improvement over the
    live sensor, which can lag by 1+ hour (upstream issue #300).

    Sessions without `EnergyDetails` (e.g. pre-3.2 firmware) fall back to a
    single point at `EndDateTime` using the session's total `Energy`. Sessions
    marked `Voided` or `Aborted` are skipped entirely - per the API docs,
    "voided sessions have no meaningful duration or energy" (they exist when
    replaced by a corrected session, or when charging never actually started).

    `sessions` must be sorted oldest-first (the archived-sessions API
    guarantees this). Points at or before `after` are skipped, to avoid
    double-counting data already imported by a previous run. `running_sum` is
    the total energy (kWh) imported so far; the returned points chain onto it
    so the statistic's `sum` keeps increasing monotonically.
    """
    hourly_deltas: dict[datetime, float] = defaultdict(float)

    for session in sessions:
        if session.get("Voided") or session.get("Aborted"):
            continue

        details = session.get("EnergyDetails") or []
        if not details:
            end = session.get("EndDateTime")
            energy = session.get("Energy") or 0.0
            if end and energy:
                details = [{"Timestamp": end, "Energy": energy}]

        prev_energy = 0.0
        for point in details:
            timestamp = dt_util.parse_datetime(point["Timestamp"])
            if timestamp is None:
                continue
            energy = point["Energy"]
            delta = energy - prev_energy
            prev_energy = energy
            if after is not None and timestamp <= after:
                continue
            hourly_deltas[_floor_hour(timestamp)] += delta

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
                sessions.extend(page["Sessions"])
                if not page.get("HasMore"):
                    break
                cursor = page["Cursor"]
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

        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{self.charger.name} Energy",
            source=DOMAIN,
            statistic_id=self.statistic_id,
            unit_class=EnergyConverter.UNIT_CLASS,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
        async_add_external_statistics(self.hass, metadata, statistics)
