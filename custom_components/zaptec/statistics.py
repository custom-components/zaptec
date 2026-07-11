"""Import Zaptec charge history into Home Assistant's long-term statistics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from homeassistant.components.recorder.models import StatisticData
from homeassistant.util import dt as dt_util


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
