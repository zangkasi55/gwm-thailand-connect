"""Cached reads and bounded, read-only accepted-command polling."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AccountApi
from .const import DEFAULT_POLL_SECONDS, DOMAIN
from .gwm_client.errors import GwmApiError, GwmAuthenticationError, GwmClientError
from .journey_storage import journey_store

LOGGER = logging.getLogger(__name__)


class GwmCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: AccountApi) -> None:
        super().__init__(
            hass, LOGGER, name=DOMAIN, config_entry=entry,
            update_interval=timedelta(seconds=entry.options.get("poll_interval", DEFAULT_POLL_SECONDS)),
        )
        self.entry = entry
        self.api = api
        self.history = journey_store(hass, entry.unique_id or entry.entry_id)
        self.history_error: str | None = None
        self._pollers: dict[str, asyncio.Task[None]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.api.read()
        except GwmAuthenticationError as error:
            raise ConfigEntryAuthFailed("Reconnect through normal account verification; do not use the vehicle PIN") from error
        except GwmClientError as error:
            LOGGER.warning(
                "GWM cloud failure: type=%s operation=%s api_code=%s",
                type(error).__name__, error.operation, getattr(error, "api_code", None),
            )
            if isinstance(error, GwmApiError) and error.api_code == "607501":
                raise ConfigEntryAuthFailed(
                    "GWM reports a login on another device (607501). Owner verification is required; "
                    "a new Home Assistant login may replace the phone app session.",
                ) from error
            if isinstance(error, GwmApiError) and error.api_code == "570062":
                raise UpdateFailed(
                    "GWM reports updated agreements/protocols (570062). Review updates in the official app; "
                    "accept only if you agree. This is not a vehicle PIN error.",
                ) from error
            raise UpdateFailed(
                f"GWM cloud {error.category} during {error.operation}; API code {getattr(error, 'api_code', None)}",
            ) from error
        try:
            await self.history.record(
                data.get("vehicles", []) if self.entry.options.get("record_journeys") is True else [],
            )
            self.history_error = None
        except (OSError, ValueError) as error:
            self.history_error = "history_storage_failed"
            LOGGER.warning("Private journey history was not saved: type=%s", type(error).__name__)
        return data

    def vehicle(self, identifier: str) -> dict[str, Any] | None:
        return next((item for item in (self.data or {}).get("vehicles", []) if item["vin"] == identifier), None)

    def track(self, record: dict[str, Any]) -> None:
        sequence = record["sequence"]
        if sequence not in self._pollers and record["state"] == "pending":
            self._pollers[sequence] = self.hass.async_create_task(
                self._follow(record), "GWM accepted-command result",
            )

    async def _follow(self, record: dict[str, Any]) -> None:
        try:
            while True:
                state = await self.api.poll(record)
                if self.data:
                    for vehicle in self.data.get("vehicles", []):
                        if vehicle["vin"] == record["vehicle"]:
                            vehicle["command_status"] = self.api.command_status.get(record["vehicle"])
                    self.async_set_updated_data(self.data)
                if state != "pending":
                    await self.async_request_refresh()
                    return
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except GwmClientError as error:
            LOGGER.warning(
                "GWM result polling failed; no command resent: type=%s api_code=%s",
                type(error).__name__, getattr(error, "api_code", None),
            )
            self.api.command_status[record["vehicle"]] = "Command outcome not confirmed; check the vehicle before retrying"
            self.async_update_listeners()
        finally:
            self._pollers.pop(record["sequence"], None)

    async def close(self) -> None:
        tasks = list(self._pollers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.api.close()
