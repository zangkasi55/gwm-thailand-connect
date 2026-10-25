"""Reported values only; no assumed measurements for absent fields."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GwmCoordinator
from .entity import GwmEntity

DESCRIPTIONS: dict[str, tuple[str, str | None, SensorDeviceClass | None]] = {
    "soc": ("Battery level", "%", SensorDeviceClass.BATTERY),
    "range_km": ("Reported range", "km", SensorDeviceClass.DISTANCE),
    "odometer_km": ("Odometer", "km", SensorDeviceClass.DISTANCE),
    "interior_temperature_c": ("Cabin temperature", "°C", SensorDeviceClass.TEMPERATURE),
    "remaining_charging_time_min": ("Remaining charging time", "min", SensorDeviceClass.DURATION),
    "charging_status": ("Charging status", None, None),
    "command_status": ("Command status", None, None),
}
for position in ("front_left", "front_right", "rear_left", "rear_right"):
    DESCRIPTIONS["tire_pressure_" + position + "_kpa"] = (
        "Tyre pressure " + position.replace("_", " "), "kPa", SensorDeviceClass.PRESSURE,
    )
    DESCRIPTIONS["tire_temperature_" + position + "_c"] = (
        "Tyre temperature " + position.replace("_", " "), "°C", SensorDeviceClass.TEMPERATURE,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    def discover() -> None:
        entities: list[GwmSensor] = []
        for vehicle in (coordinator.data or {}).get("vehicles", []):
            if vehicle["vin"] not in known:
                known.add(vehicle["vin"])
                entities.extend(GwmSensor(coordinator, vehicle["vin"], signal) for signal in DESCRIPTIONS)
        if entities:
            add(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class GwmSensor(GwmEntity, SensorEntity):
    def __init__(self, coordinator: GwmCoordinator, identifier: str, signal: str) -> None:
        super().__init__(coordinator, identifier, signal)
        name, unit, device_class = DESCRIPTIONS[signal]
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if unit is not None:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> Any:
        vehicle = self.vehicle or {}
        if self.signal == "command_status":
            return self.coordinator.api.command_status.get(self.identifier, vehicle.get("command_status"))
        return vehicle.get("values", {}).get(self.signal)
