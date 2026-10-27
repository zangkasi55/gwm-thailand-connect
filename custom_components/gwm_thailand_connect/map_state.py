"""A current map marker must come from a recent manufacturer fix, not poll time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .journey import DOORS, MAX_GAP_SECONDS, Observation, ObservationError


def recent_vehicle(
    snapshot: dict[str, Any] | None, *, now: datetime | None = None,
) -> dict[str, Any]:
    if snapshot is None:
        return {"availability": "cloud_unavailable", "position": None}
    try:
        sample = Observation.from_snapshot(snapshot, now or datetime.now(UTC))
    except ObservationError as error:
        return {"availability": str(error), "position": None}
    if sample.position is None:
        return {"availability": "location_not_reported", "position": None}
    values = snapshot.get("values") or {}
    ac = values.get("ac_active")
    return {
        "availability": "available",
        "position": sample.position,
        "reported_at": sample.time.isoformat(),
        "expires_at": (sample.time + timedelta(seconds=MAX_GAP_SECONDS)).isoformat(),
        "battery": sample.soc,
        "locked": sample.locked,
        "doors": dict(zip(DOORS, sample.doors, strict=True)),
        "ac_active": ac if type(ac) is bool else None,
        "vehicle_power_off": sample.vehicle_power_off,
    }
