"""Bounded private history, separate from credentials and HA entity attributes."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .journey import MAX_SAMPLES, RETENTION_DAYS, Observation, ObservationError, summarize

LOGGER = logging.getLogger(__name__)


class JourneyStore:
    def __init__(self, hass: HomeAssistant, binding: str) -> None:
        self.hass = hass
        private_key = hashlib.sha256(binding.encode("utf-8")).hexdigest()
        self._store = Store[dict[str, Any]](
            hass, 1, DOMAIN + ".journeys." + private_key, private=True, atomic_writes=True,
        )
        self._samples: dict[str, list[Observation]] | None = None
        self._quality: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._cancel_expiry: Callable[[], None] | None = None
        self.next_expiry: datetime | None = None
        self.retention_error = False
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _: self._cancel_timer())

    def _cancel_timer(self) -> None:
        if self._cancel_expiry is not None:
            self._cancel_expiry()
        self._cancel_expiry = None
        self.next_expiry = None

    def _schedule_expiry(self) -> None:
        expiry = min((
            records[0].time + timedelta(days=RETENTION_DAYS)
            for records in (self._samples or {}).values() if records
        ), default=None)
        if expiry == self.next_expiry:
            return
        self._cancel_timer()
        if expiry is not None:
            self.next_expiry = expiry
            self._cancel_expiry = async_track_point_in_utc_time(self.hass, self._expire, expiry)

    async def _expire(self, now: datetime) -> None:
        self._cancel_timer()
        try:
            await self.record([], now=now)
        except (OSError, ValueError) as error:
            self.retention_error = True
            LOGGER.error("Seven-day journey expiry failed; local retry scheduled: type=%s", type(error).__name__)
            self.next_expiry = now + timedelta(seconds=60)
            self._cancel_expiry = async_track_point_in_utc_time(self.hass, self._expire, self.next_expiry)

    async def start(self) -> None:
        await self._expire(datetime.now(UTC))

    async def _load(self) -> dict[str, list[Observation]]:
        if self._samples is not None:
            return self._samples
        document = await self._store.async_load()
        samples: dict[str, list[Observation]] = {}
        if document is not None:
            if not isinstance(document, dict) or set(document) != {"vehicles"} or not isinstance(document["vehicles"], dict):
                raise ObservationError("history_schema_invalid")
            if len(document["vehicles"]) > 10:
                raise ObservationError("history_schema_invalid")
            for identifier, records in document["vehicles"].items():
                if not isinstance(identifier, str) or not isinstance(records, list) or len(records) > MAX_SAMPLES:
                    raise ObservationError("history_schema_invalid")
                if any(not isinstance(record, dict) for record in records):
                    raise ObservationError("history_schema_invalid")
                samples[identifier] = [Observation.from_document(record) for record in records]
                if any(first.time >= second.time for first, second in zip(samples[identifier], samples[identifier][1:], strict=False)):
                    raise ObservationError("history_schema_invalid")
        self._samples = samples
        return samples

    async def record(
        self, vehicles: list[dict[str, Any]], *, now: datetime | None = None,
    ) -> None:
        instant = now or datetime.now(UTC)
        cutoff = instant - timedelta(days=RETENTION_DAYS)
        async with self._lock:
            previous = await self._load()
            updated = {
                identifier: [sample for sample in samples if sample.time > cutoff]
                for identifier, samples in previous.items()
            }
            for vehicle in vehicles:
                identifier = vehicle["vin"]
                if not isinstance(identifier, str) or not identifier:
                    raise ObservationError("vehicle_identifier_invalid")
                if identifier not in updated and len(updated) >= 10:
                    raise ObservationError("history_vehicle_limit")
                records = updated.setdefault(identifier, [])
                try:
                    sample = Observation.from_snapshot(vehicle, instant)
                except ObservationError as error:
                    self._quality[identifier] = str(error)
                    continue
                if records and sample.time <= records[-1].time:
                    self._quality[identifier] = "cached_sample" if sample.time == records[-1].time else "out_of_order"
                    continue
                self._quality[identifier] = "recording" if sample.position is not None else "location_not_reported"
                records.append(sample)
                updated[identifier] = records[-MAX_SAMPLES:]
            if updated != previous:
                await self._store.async_save({
                    "vehicles": {
                        identifier: [sample.document() for sample in records]
                        for identifier, records in updated.items()
                    },
                })
                self._samples = updated
            self.retention_error = False
            self._schedule_expiry()

    async def history(
        self, identifier: str, hours: int, *, now: datetime | None = None,
    ) -> dict[str, Any]:
        instant = now or datetime.now(UTC)
        cutoff = instant - timedelta(hours=min(hours, RETENTION_DAYS * 24))
        await self.record([], now=instant)
        async with self._lock:
            samples = [sample for sample in (await self._load()).get(identifier, []) if sample.time >= cutoff]
        summary = await self.hass.async_add_executor_job(summarize, samples, instant)
        return {
            **summary, "status": self._quality.get(
                identifier, "location_not_reported" if samples and samples[-1].position is None
                else "ready" if samples else "waiting_for_samples",
            ),
            "retention_days": RETENTION_DAYS, "sample_limit": MAX_SAMPLES,
        }

    async def clear(self, identifier: str) -> None:
        async with self._lock:
            updated = {key: records for key, records in (await self._load()).items() if key != identifier}
            await self._store.async_save({
                "vehicles": {key: [sample.document() for sample in records] for key, records in updated.items()},
            })
            self._samples = updated
            self._quality.pop(identifier, None)
            self._schedule_expiry()

    async def remove(self) -> None:
        async with self._lock:
            await self._store.async_remove()
            self._samples = None
            self._quality.clear()
            self._cancel_timer()


def journey_store(hass: HomeAssistant, binding: str) -> JourneyStore:
    stores: dict[str, JourneyStore] = hass.data.setdefault(DOMAIN + "_journey_stores", {})
    if binding not in stores:
        stores[binding] = JourneyStore(hass, binding)
    return stores[binding]
