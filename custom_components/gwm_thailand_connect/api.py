"""Thai account lifecycle and guarded commands, separate from the account PIN."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError

from .const import APP_STORAGE
from .gwm_client.errors import GwmClientError, is_overseas_session_expired
from .gwm_client.models import CloudVehicleBasics, VehicleIdentifier
from .gwm_client.snapshots import map_vehicle_snapshot
from .gwm_client.thailand_client import (
    ThailandAuthenticated,
    ThailandClient,
    ThailandCredentials,
    ThailandInitializationRequired,
)
from .gwm_client.thailand_controls import THAILAND_CONTROLS, ThailandControlAction, ThailandControlCommand
from .gwm_client.thailand_protocol import load_thailand_app_material_file
from .storage import AccountStore, account_store


class AccountApi:
    def __init__(
        self, entry: ConfigEntry, credentials: ThailandCredentials,
        client: ThailandClient, store: AccountStore,
    ) -> None:
        self.entry = entry
        self.credentials = credentials
        self.client = client
        self.store = store
        self._command_lock = asyncio.Lock()
        self._renew_lock = asyncio.Lock()
        self.command_status: dict[str, str] = {}

    @classmethod
    async def create(cls, hass: HomeAssistant, entry: ConfigEntry) -> AccountApi:
        material = await hass.async_add_executor_job(
            load_thailand_app_material_file, Path(hass.config.path(".storage", APP_STORAGE)),
        )
        probe = ThailandCredentials(
            entry.data["account"], entry.data.get("password"), "0" * 32,
            authentication_method=entry.data["authentication_method"],
        )
        store = account_store(hass, probe.account_binding)
        state = await store.auth()
        if state is None:
            raise ConfigEntryAuthFailed("Complete the GWM account verification in Home Assistant")
        credentials = replace(probe, device_id=state.device_id)
        client = ThailandClient(material)
        try:
            result = await client.authenticate(
                credentials, state=state, allow_session_reclaim=False, allow_password_login=False,
            )
            if isinstance(result, ThailandInitializationRequired):
                await store.save_auth(result.state)
                raise ConfigEntryNotReady("Finish private GWM certificate setup: " + result.reason)
            if not isinstance(result, ThailandAuthenticated):
                raise ConfigEntryAuthFailed("Owner account verification is required")
            await store.save_auth(result.state)
        except BaseException:
            await client.aclose()
            raise
        return cls(entry, credentials, client, store)

    async def read(self) -> dict[str, Any]:
        try:
            return await self._read_once()
        except GwmClientError as error:
            if not is_overseas_session_expired(error):
                raise
        async with self._renew_lock:
            state = await self.store.auth()
            if state is None:
                raise ConfigEntryAuthFailed("Owner account verification is required")
            renewed = await self.client.refresh_thailand_session(self.credentials, state)
            await self.store.save_auth(renewed.state)
        return await self._read_once()

    async def _read_once(self) -> dict[str, Any]:
        snapshots = []
        for vehicle in await self.client.acquire_vehicles():
            status = await self.client.get_last_status(vehicle.identifier)
            features = await self.client.get_vehicle_features(vehicle.identifier)
            snapshot = map_vehicle_snapshot(
                vehicle, status, CloudVehicleBasics(), refreshed_at=datetime.now(UTC),
                remote_commands_available=False,
            ).as_dict()
            snapshot["thailand_controls"] = {
                "status": "advertised",
                "enabled": self.entry.options.get("enable_remote_commands") is True
                and bool(self.entry.options.get("security_pin")),
                "actions": [
                    {"action": action.value, "label": THAILAND_CONTROLS[action].label,
                     "group": THAILAND_CONTROLS[action].group}
                    for action in features.actions
                ],
                "capability_codes": sorted(features.codes),
            }
            if vehicle.identifier.value in self.command_status:
                snapshot["command_status"] = self.command_status[vehicle.identifier.value]
            snapshots.append(snapshot)
        return {"region": "tha", "vehicles": snapshots}

    async def send(
        self, identifier: str, action: str, *, confirmed: bool, temperature: int = 24,
        duration_minutes: int = 10, front_left_level: int = 0, front_right_level: int = 0,
    ) -> dict[str, Any]:
        if confirmed is not True or self.entry.options.get("enable_remote_commands") is not True:
            raise HomeAssistantError("Explicit control opt-in and confirmation are required")
        pin = self.entry.options.get("security_pin")
        if not isinstance(pin, str) or not pin.strip():
            raise HomeAssistantError("Configure the vehicle PIN privately in integration options")
        try:
            selected = ThailandControlAction(action)
            command = ThailandControlCommand(
                VehicleIdentifier(identifier), selected, temperature, duration_minutes,
                front_left_level, front_right_level,
            )
        except (TypeError, ValueError):
            raise HomeAssistantError("Invalid or unsupported vehicle control") from None
        async with self._command_lock:
            now = datetime.now(UTC)
            if any(
                item["vehicle"] == identifier and item["state"] == "pending"
                and (now - datetime.fromisoformat(item["created"])).total_seconds() < 90
                for item in await self.store.commands()
            ):
                raise HomeAssistantError("Wait for the pending vehicle command before sending another")
            pin_hash = hashlib.md5(pin.strip().encode("utf-8"), usedforsecurity=False).hexdigest()
            accepted = await self.client.send_control(command, security_password_hash=pin_hash)
            record = {
                "vehicle": identifier, "action": selected.value, "sequence": accepted.command_id,
                "created": now.isoformat(), "state": "pending",
            }
            saving = asyncio.create_task(self.store.save_command(record))
            try:
                await asyncio.shield(saving)
            except asyncio.CancelledError:
                await saving
                raise
            except (OSError, ValueError) as error:
                raise HomeAssistantError(
                    "GWM accepted the command, but its journal could not be saved. Do not retry.",
                ) from error
            self.command_status[identifier] = THAILAND_CONTROLS[selected].label + ": accepted; waiting for vehicle"
            return record

    async def poll(self, record: dict[str, Any]) -> str:
        label = THAILAND_CONTROLS[ThailandControlAction(record["action"])].label
        if (datetime.now(UTC) - datetime.fromisoformat(record["created"])).total_seconds() >= 90:
            record = {**record, "state": "timeout"}
        else:
            result = await self.client.get_control_result(
                VehicleIdentifier(record["vehicle"]), record["sequence"],
                ThailandControlAction(record["action"]),
            )
            if result is None or result.state == "pending":
                return "pending"
            record = {**record, "state": result.state}
        await self.store.save_command(record)
        self.command_status[record["vehicle"]] = label + ": " + record["state"]
        final_state: str = record["state"]
        return final_state

    async def close(self) -> None:
        await self.client.aclose()
