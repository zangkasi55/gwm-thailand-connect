"""Observation-based history: cloud samples are not continuous GPS or ignition."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DOORS = (
    "door_front_driver_open", "door_front_passenger_open",
    "door_rear_driver_side_open", "door_rear_passenger_side_open", "trunk_open",
)
RADIUS_M = 50
STOP_SECONDS = 60
MAX_GAP_SECONDS = 300
MAX_SPEED_MPS = 200 / 3.6
RETENTION_DAYS = 7
MAX_SAMPLES = RETENTION_DAYS * 24 * 60 * 2 + 1


class ObservationError(ValueError):
    """A bounded reason, safe to report without logging a snapshot."""


def timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ObservationError("timestamp_missing")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise ObservationError("timestamp_invalid") from None
    if result.tzinfo is None:
        raise ObservationError("timestamp_invalid")
    return result.astimezone(UTC)


def number(value: object, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ObservationError("reading_invalid")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ObservationError("reading_invalid")
    return result


@dataclass(frozen=True, slots=True, repr=False)
class Observation:
    time: datetime
    latitude: float | None
    longitude: float | None
    soc: float | None
    odometer: float | None
    locked: bool | None
    doors: tuple[bool | None, ...]
    engine_code: int | None
    vehicle_power_off: bool | None = None

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any], now: datetime) -> Observation:
        times = snapshot.get("timestamps") or {}
        if not isinstance(times, dict):
            raise ObservationError("timestamp_invalid")
        instant = timestamp(times.get("acquisition_time") or times.get("update_time"))
        age = (now - instant).total_seconds()
        if age > MAX_GAP_SECONDS:
            raise ObservationError("stale_sample")
        if age < -30:
            raise ObservationError("future_sample")
        location, values = snapshot.get("location") or {}, snapshot.get("values") or {}
        if not isinstance(location, dict) or not isinstance(values, dict):
            raise ObservationError("reading_invalid")
        return cls.from_document({
            "time": instant.isoformat(), "latitude": location.get("latitude"),
            "longitude": location.get("longitude"), "soc": values.get("soc"),
            "odometer": values.get("odometer_km"), "locked": values.get("locked"),
            "doors": [values.get(key) for key in DOORS],
            "engine_code": values.get("engine_state_code"),
            # This is reserved for a verified ignition/READY adapter, not raw engine code 2016001.
            "vehicle_power_off": values.get("vehicle_power_off"),
        })

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Observation:
        expected = {"time", "latitude", "longitude", "soc", "odometer", "locked", "doors", "engine_code"}
        if set(document) not in (expected, expected | {"vehicle_power_off"}):
            raise ObservationError("history_schema_invalid")
        doors, locked, engine = document["doors"], document["locked"], document["engine_code"]
        power_off = document.get("vehicle_power_off")
        if (
            not isinstance(doors, list) or len(doors) != len(DOORS)
            or any(value is not None and type(value) is not bool for value in [*doors, locked])
            or engine is not None and type(engine) is not int
            or power_off is not None and type(power_off) is not bool
        ):
            raise ObservationError("history_schema_invalid")
        lat = number(document["latitude"], -90, 90)
        lon = number(document["longitude"], -180, 180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            lat = lon = None
        return cls(
            timestamp(document["time"]), lat, lon, number(document["soc"], 0, 100),
            number(document["odometer"], 0, 10_000_000), locked, tuple(doors), engine, power_off,
        )

    def document(self) -> dict[str, Any]:
        return {
            "time": self.time.isoformat(), "latitude": self.latitude, "longitude": self.longitude,
            "soc": self.soc, "odometer": self.odometer, "locked": self.locked,
            "doors": list(self.doors), "engine_code": self.engine_code, "vehicle_power_off": self.vehicle_power_off,
        }

    @property
    def position(self) -> dict[str, float] | None:
        if self.latitude is None or self.longitude is None:
            return None
        return {"latitude": self.latitude, "longitude": self.longitude}


def distance(first: Observation, second: Observation) -> float:
    first_position, second_position = first.position, second.position
    if first_position is None or second_position is None:
        raise ValueError("Coordinates required for a distance")
    return position_distance(first_position, second_position)


def position_distance(first: dict[str, float], second: dict[str, float]) -> float:
    dlat = math.radians(second["latitude"] - first["latitude"])
    dlon = math.radians(second["longitude"] - first["longitude"])
    chord = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(first["latitude"])) * math.cos(math.radians(second["latitude"])) * math.sin(dlon / 2) ** 2
    )
    return 12_742_000 * math.asin(math.sqrt(min(1.0, max(0.0, chord))))


def _outside(anchor: Observation, sample: Observation) -> bool:
    odometer_changed = (
        anchor.odometer is not None and sample.odometer is not None
        and sample.odometer - anchor.odometer >= 0.05 - 1e-9
    )
    return distance(anchor, sample) > RADIUS_M or odometer_changed


def _stop(samples: list[Observation], *, arrived: bool, departed: datetime | None, reason: str) -> dict[str, Any]:
    first, last = samples[0], samples[-1]
    duration = (last.time - first.time).total_seconds()
    shutdown_at: datetime | None = None
    driver_opened_at: datetime | None = None
    sequence: dict[str, str] | None = None
    previous: Observation | None = None
    sequence_current: bool | None = False
    for sample in samples:
        if sample.vehicle_power_off is not True:
            shutdown_at = driver_opened_at = None
            sequence_current = False if sample.vehicle_power_off is False else None
        else:
            if previous is not None and previous.vehicle_power_off is False:
                shutdown_at = sample.time
                driver_opened_at = None
            driver = sample.doors[0]
            if driver is None:
                driver_opened_at = None
            elif (
                driver is True and previous is not None and previous.doors[0] is False
                and shutdown_at is not None and shutdown_at < sample.time
            ):
                driver_opened_at = sample.time
            elif (
                driver is False and driver_opened_at is not None and shutdown_at is not None
                and driver_opened_at < sample.time
            ):
                sequence = {
                    "power_off_at": shutdown_at.isoformat(),
                    "driver_open_at": driver_opened_at.isoformat(),
                    "driver_closed_at": sample.time.isoformat(),
                }
                sequence_current = True
                driver_opened_at = None
        previous = sample
    kind = "parking" if sequence is not None else (
        "temporary_stop" if last.vehicle_power_off is False else "unconfirmed_stop"
    )
    battery = next((sample for sample in reversed(samples) if sample.soc is not None), None)
    return {
        "id": first.time.isoformat(), "position": first.position,
        "start": first.time.isoformat(), "last_observed": last.time.isoformat(),
        "departure_observed": departed.isoformat() if departed else None,
        "observed_duration_seconds": duration, "kind": kind, "end_reason": reason,
        "arrival_observed": arrived, "classification_is_estimate": True,
        "parking_sequence": sequence,
        "parking_sequence_current": False if departed is not None else (
            sequence_current if reason == "current" and last.vehicle_power_off is not None else None
        ),
        "vehicle_power_off": last.vehicle_power_off,
        "battery_arrival": first.soc, "battery_latest": battery.soc if battery else None,
        "battery_latest_at": battery.time.isoformat() if battery else None,
        "battery_departure": last.soc if departed else None,
    }


def _door_intervals(samples: list[Observation]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    intervals: list[dict[str, Any]] = []
    engine_changes: list[dict[str, Any]] = []
    active: dict[int, dict[str, Any]] = {}
    previous: Observation | None = None

    def interrupt(index: int, reason: str) -> None:
        interval = active.pop(index)
        interval["status"] = reason
        interval["estimated_duration_seconds"] = None
        intervals.append(interval)

    for sample in samples:
        if previous is not None and (sample.time - previous.time).total_seconds() > MAX_GAP_SECONDS:
            for index in list(active):
                interrupt(index, "tracking_gap")
            previous = None
        if (
            previous is not None and sample.engine_code is not None and previous.engine_code is not None
            and sample.engine_code != previous.engine_code
        ):
            engine_changes.append({
                "time": sample.time.isoformat(), "before": previous.engine_code,
                "after": sample.engine_code, "position": sample.position,
            })
        for index, state in enumerate(sample.doors):
            if state is None:
                if index in active:
                    interrupt(index, "state_not_reported")
                continue
            if state is True:
                if index not in active:
                    active[index] = {
                        "door": DOORS[index], "start": sample.time.isoformat(), "end": None,
                        "last_open_observed": sample.time.isoformat(), "status": "open",
                        "start_uncertain": previous is None or previous.doors[index] is not False,
                        "position": sample.position, "battery": sample.soc,
                        "observed_open_span_seconds": 0.0, "estimated_duration_seconds": None,
                    }
                interval = active[index]
                interval["last_open_observed"] = sample.time.isoformat()
                interval["observed_open_span_seconds"] = (sample.time - timestamp(interval["start"])).total_seconds()
            elif index in active:
                interval = active.pop(index)
                interval["end"] = sample.time.isoformat()
                interval["status"] = "closed_observed"
                interval["estimated_duration_seconds"] = (sample.time - timestamp(interval["start"])).total_seconds()
                intervals.append(interval)
        previous = sample
    intervals.extend(active.values())
    return sorted(intervals, key=lambda item: item["start"]), engine_changes


def summarize(samples: list[Observation], now: datetime) -> dict[str, Any]:
    """Classify bounded observation clusters; never fill unobserved time or road paths."""

    stops: list[dict[str, Any]] = []
    segments: list[list[dict[str, Any]]] = []
    segment: list[dict[str, Any]] = []
    clean: list[Observation] = []
    cluster: list[Observation] = []
    pending: Observation | None = None
    previous: Observation | None = None
    last_gps: Observation | None = None
    moving = False
    arrived = False
    rejected_gps = 0
    movement_events: list[dict[str, Any]] = []

    def movement(kind: str, at: Observation, detected: Observation) -> None:
        movement_events.append({
            "kind": kind, "time": at.time.isoformat(), "detected_at": detected.time.isoformat(),
            "position": at.position, "battery": at.soc, "timing_is_estimate": True,
        })

    def point(sample: Observation) -> None:
        if not segment or segment[-1]["time"] != sample.time.isoformat():
            segment.append({
                **(sample.position or {}), "time": sample.time.isoformat(), "battery": sample.soc,
            })

    def finish(reason: str, departure: datetime | None = None) -> None:
        if cluster and (cluster[-1].time - cluster[0].time).total_seconds() >= STOP_SECONDS:
            stops.append(_stop(cluster, arrived=arrived, departed=departure, reason=reason))

    for original in samples:
        sample = original
        gap = previous is not None and (sample.time - previous.time).total_seconds() > MAX_GAP_SECONDS
        if gap:
            finish("tracking_gap")
            if segment:
                segments.append(segment)
            segment, cluster, pending = [], [], None
            last_gps = None
            moving = arrived = False
        if sample.position is not None and last_gps is not None:
            seconds = (sample.time - last_gps.time).total_seconds()
            if 0 < seconds <= MAX_GAP_SECONDS and distance(last_gps, sample) / seconds > MAX_SPEED_MPS:
                sample = Observation(
                    sample.time, None, None, sample.soc, sample.odometer,
                    sample.locked, sample.doors, sample.engine_code, sample.vehicle_power_off,
                )
                rejected_gps += 1
        clean.append(sample)
        previous = sample
        if sample.position is None:
            finish("location_not_reported")
            if segment:
                segments.append(segment)
            segment, cluster, pending = [], [], None
            last_gps = None
            moving = arrived = False
            continue
        last_gps = sample
        if not cluster:
            cluster = [sample]
            point(sample)
            continue
        if not _outside(cluster[0], sample):
            pending = None
            cluster.append(sample)
            if (sample.time - cluster[0].time).total_seconds() >= STOP_SECONDS:
                if moving:
                    movement("movement_stopped", cluster[0], sample)
                moving = False
            continue
        if pending is None:
            pending = sample
            continue
        finish("movement_observed", pending.time)
        if not moving:
            movement("movement_started", pending, sample)
        point(cluster[-1])
        point(pending)
        point(sample)
        cluster = [sample] if _outside(pending, sample) else [pending, sample]
        pending = None
        moving = True
        arrived = True
        if len(cluster) > 1 and (cluster[-1].time - cluster[0].time).total_seconds() >= STOP_SECONDS:
            movement("movement_stopped", cluster[0], sample)
            moving = False
    finish("current")
    if cluster:
        point(cluster[-1])
    if segment:
        segments.append(segment)
    intervals, engine_changes = _door_intervals(clean)
    for interval in intervals:
        interval["stop_id"] = next((
            stop["id"] for stop in stops
            if stop["start"] <= interval["start"] <= stop["last_observed"]
            and interval["position"] is not None and stop["position"] is not None
            and position_distance(interval["position"], stop["position"]) <= RADIUS_M
        ), None)
    motion = "unknown"
    if cluster:
        motion = "movement_pending" if pending else "moving" if moving else (
            "stopped" if (cluster[-1].time - cluster[0].time).total_seconds() >= STOP_SECONDS else "insufficient_samples"
        )
    last = samples[-1] if samples else None
    stale = last is None or (now - last.time).total_seconds() > MAX_GAP_SECONDS
    if stale:
        for stop in stops:
            if stop["end_reason"] == "current":
                stop["parking_sequence_current"] = None
    return {
        "segments": segments, "stops": stops[-500:], "doors": intervals[-1000:],
        "engine_code_changes": engine_changes[-500:], "engine_ignition_supported": False,
        "vehicle_power_signal_available": any(sample.vehicle_power_off is not None for sample in samples),
        "movement_events": movement_events[-1000:],
        "motion": "unknown" if stale else motion, "stale": stale,
        "last_sample_at": last.time.isoformat() if last else None,
        "sample_count": len(samples), "gps_outliers": rejected_gps,
        "rules": {
            "radius_m": RADIUS_M, "temporary_stop_seconds": STOP_SECONDS,
            "parking_rule": "stopped_then_verified_vehicle_power_off_then_driver_door_open_then_closed",
            "max_gap_seconds": MAX_GAP_SECONDS,
        },
    }
