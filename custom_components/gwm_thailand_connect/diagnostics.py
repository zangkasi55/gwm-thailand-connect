"""Diagnostic summaries intentionally contain no account or vehicle identifiers."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import GwmConfigEntry
from .const import VERSION


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GwmConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    vehicles = (coordinator.data or {}).get("vehicles", [])
    return {
        "version": VERSION,
        "entry_state": entry.state.value,
        "last_update_success": coordinator.last_update_success,
        "vehicle_count": len(vehicles),
        "remote_controls_opted_in": entry.options.get("enable_remote_commands") is True,
        "reported_signal_names": sorted({
            key for vehicle in vehicles for key, value in vehicle.get("values", {}).items() if value is not None
        }),
        "credentials_tokens_pin_keys_vins_locations_omitted": True,
    }
