"""GWM Thailand Connect: local private provisioning, cloud reads, guarded writes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import AccountApi
from .const import CONTROL_SERVICE, DOMAIN, FRONTEND_PATH
from .coordinator import GwmCoordinator
from .gwm_client.errors import GwmAuthenticationError, GwmClientError
from .gwm_client.thailand_controls import THAILAND_CONTROLS
from .gwm_client.thailand_identity import ThailandIdentityError
from .gwm_client.thailand_protocol import ThailandProtocolError
from .journey_storage import journey_store
from .journey_ws import register_journey_api
from .storage import account_store

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
type GwmConfigEntry = ConfigEntry[GwmCoordinator]


def _confirmation(value: object) -> bool:
    if value is not True:
        raise vol.Invalid("Explicit owner confirmation is required")
    return True


SERVICE_SCHEMA = vol.Schema({
    vol.Required("device_id"): cv.string,
    vol.Required("action"): vol.In([action.value for action in THAILAND_CONTROLS]),
    vol.Required("confirmed"): _confirmation,
    vol.Optional("temperature", default=24): vol.All(int, vol.Range(min=16, max=32)),
    vol.Optional("duration_minutes", default=10): vol.All(int, vol.Range(min=5, max=30)),
    vol.Optional("front_left_level", default=0): vol.All(int, vol.Range(min=0, max=3)),
    vol.Optional("front_right_level", default=0): vol.All(int, vol.Range(min=0, max=3)),
})


def _register_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, CONTROL_SERVICE):
        return

    async def control(call: ServiceCall) -> None:
        device = dr.async_get(hass).async_get(call.data["device_id"])
        if device is None:
            raise HomeAssistantError("Select a connected GWM Thailand device")
        if call.context.user_id is not None:
            user = await hass.auth.async_get_user(call.context.user_id)
            entities = er.async_entries_for_device(er.async_get(hass), device.id)
            if user is None or not user.is_active or not any(
                user.permissions.check_entity(entity.entity_id, POLICY_CONTROL) for entity in entities
            ):
                raise HomeAssistantError("You do not have permission to control this vehicle")
        targets = [
            (entry.runtime_data, identifier)
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            for domain, identifier in device.identifiers
            if domain == DOMAIN and entry.runtime_data.vehicle(identifier) is not None
        ]
        if len(targets) != 1:
            raise HomeAssistantError("Select exactly one available GWM Thailand vehicle")
        coordinator, identifier = targets[0]
        try:
            record = await coordinator.api.send(
                identifier, call.data["action"], confirmed=call.data["confirmed"],
                temperature=call.data["temperature"], duration_minutes=call.data["duration_minutes"],
                front_left_level=call.data["front_left_level"], front_right_level=call.data["front_right_level"],
            )
        except GwmClientError as error:
            raise HomeAssistantError(
                "GWM did not confirm the command. Check the vehicle and command status before retrying. "
                f"API code: {getattr(error, 'api_code', None)}",
            ) from error
        coordinator.track(record)

    hass.services.async_register(DOMAIN, CONTROL_SERVICE, control, schema=SERVICE_SCHEMA)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    await _register_frontend(hass)
    register_journey_api(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        await journey_store(hass, entry.unique_id or entry.entry_id).start()
    return True


async def _register_frontend(hass: HomeAssistant) -> None:
    lock = hass.data.setdefault(DOMAIN + "_frontend_lock", asyncio.Lock())
    async with lock:
        if not hass.data.get(DOMAIN + "_frontend_ready"):
            await hass.http.async_register_static_paths([
                StaticPathConfig(FRONTEND_PATH, str(Path(__file__).parent / "frontend"), cache_headers=False),
            ])
            hass.data[DOMAIN + "_frontend_ready"] = True


async def async_setup_entry(hass: HomeAssistant, entry: GwmConfigEntry) -> bool:
    try:
        api = await AccountApi.create(hass, entry)
    except GwmAuthenticationError as error:
        raise ConfigEntryAuthFailed("Complete normal account verification; do not enter the vehicle PIN") from error
    except (ThailandProtocolError, ThailandIdentityError) as error:
        raise ConfigEntryNotReady("Import valid private app material through reconfiguration") from error
    except GwmClientError as error:
        raise ConfigEntryNotReady("GWM account initialization is unavailable") from error
    coordinator = GwmCoordinator(hass, entry, api)
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await _register_frontend(hass)
        register_journey_api(hass)
        _register_service(hass)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        for command in await api.store.commands():
            coordinator.track(command)
    except BaseException:
        await coordinator.close()
        raise
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: GwmConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GwmConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.close()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if entry.unique_id is not None:
        await account_store(hass, entry.unique_id).remove()
    await journey_store(hass, entry.unique_id or entry.entry_id).remove()
