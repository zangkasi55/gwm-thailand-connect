"""Authenticated per-device history access. Locations never enter public assets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import async_response, websocket_command
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import GwmCoordinator
from .journey_storage import JourneyStore, journey_store
from .map_state import recent_vehicle


@dataclass(frozen=True)
class JourneyAccess:
    entry: ConfigEntry
    history: JourneyStore
    identifier: str
    coordinator: GwmCoordinator | None


def resolve_coordinator(
    hass: HomeAssistant, connection: ActiveConnection, device_id: str, *, write: bool = False,
) -> JourneyAccess:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ValueError("vehicle_not_found")
    entities = er.async_entries_for_device(er.async_get(hass), device.id)
    policy = POLICY_CONTROL if write else POLICY_READ
    if not connection.user.is_active or not connection.user.is_admin or not any(
        entity.platform == DOMAIN and connection.user.permissions.check_entity(entity.entity_id, policy)
        for entity in entities
    ):
        raise ValueError("not_authorized")
    candidates = [
        (entry, identifier)
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id in device.config_entries
        for domain, identifier in device.identifiers if domain == DOMAIN
    ]
    if len(candidates) != 1:
        raise ValueError("vehicle_not_found")
    entry, identifier = candidates[0]
    runtime = getattr(entry, "runtime_data", None)
    coordinator = runtime if isinstance(runtime, GwmCoordinator) else None
    store = coordinator.history if coordinator is not None else journey_store(hass, entry.unique_id or entry.entry_id)
    return JourneyAccess(entry, store, identifier, coordinator)


def register_journey_api(hass: HomeAssistant) -> None:
    if hass.data.get(DOMAIN + "_journey_api"):
        return

    @websocket_command({
        vol.Required("type"): DOMAIN + "/journeys",
        vol.Required("device_id"): str,
        vol.Optional("hours", default=24): vol.All(int, vol.Range(min=1, max=168)),
    })
    @async_response
    async def history(
        hass: HomeAssistant, connection: ActiveConnection, message: dict[str, Any],
    ) -> None:
        try:
            access = resolve_coordinator(hass, connection, message["device_id"])
        except ValueError as error:
            connection.send_error(message["id"], str(error), "Vehicle history is not accessible")
            return
        current = recent_vehicle(
            access.coordinator.vehicle(access.identifier) if access.coordinator is not None else None,
        )
        if access.entry.options.get("record_journeys") is not True:
            connection.send_result(message["id"], {
                "status": "disabled", "stops": [], "segments": [], "doors": [], "current": current,
            })
            return
        try:
            result = await access.history.history(access.identifier, message["hours"])
        except (OSError, ValueError):
            connection.send_error(message["id"], "history_storage_failed", "Private journey history could not be read")
            return
        result["storage_warning"] = access.history.retention_error or (
            access.coordinator is not None and access.coordinator.history_error is not None
        )
        result["current"] = current
        connection.send_result(message["id"], result)

    @websocket_command({
        vol.Required("type"): DOMAIN + "/clear_journeys",
        vol.Required("device_id"): str,
        vol.Required("confirmed"): vol.In([True]),
    })
    @async_response
    async def clear(
        hass: HomeAssistant, connection: ActiveConnection, message: dict[str, Any],
    ) -> None:
        if message["confirmed"] is not True:
            connection.send_error(message["id"], "confirmation_required", "Explicit history deletion confirmation is required")
            return
        try:
            access = resolve_coordinator(hass, connection, message["device_id"], write=True)
        except ValueError as error:
            connection.send_error(message["id"], str(error), "Vehicle history is not accessible")
            return
        try:
            await access.history.clear(access.identifier)
        except (OSError, ValueError):
            connection.send_error(message["id"], "history_storage_failed", "Private journey history could not be cleared")
            return
        if access.coordinator is not None:
            access.coordinator.history_error = None
        connection.send_result(message["id"], {"cleared": True})

    websocket_api.async_register_command(hass, history)
    websocket_api.async_register_command(hass, clear)
    hass.data[DOMAIN + "_journey_api"] = True
