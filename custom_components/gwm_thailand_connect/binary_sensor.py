"""Raw on/off meanings are explicit; unavailable is never rendered closed."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GwmCoordinator
from .entity import GwmEntity

DESCRIPTIONS = {
    "lock_open": ("Lock open", BinarySensorDeviceClass.LOCK),
    "door_front_driver_open": ("Front driver door", BinarySensorDeviceClass.DOOR),
    "door_front_passenger_open": ("Front passenger door", BinarySensorDeviceClass.DOOR),
    "door_rear_driver_side_open": ("Rear driver-side door", BinarySensorDeviceClass.DOOR),
    "door_rear_passenger_side_open": ("Rear passenger-side door", BinarySensorDeviceClass.DOOR),
    "trunk_open": ("Tailgate", BinarySensorDeviceClass.DOOR),
    "ac_active": ("Air conditioning", BinarySensorDeviceClass.RUNNING),
    "charge_plug_connected": ("Charge plug", BinarySensorDeviceClass.PLUG),
    "charging_active": ("Charging active", BinarySensorDeviceClass.BATTERY_CHARGING),
}
for position in ("front_left", "front_right", "rear_left", "rear_right"):
    DESCRIPTIONS["window_" + position + "_open"] = (
        "Window " + position.replace("_", " "), BinarySensorDeviceClass.WINDOW,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    def discover() -> None:
        entities: list[GwmBinary] = []
        for vehicle in (coordinator.data or {}).get("vehicles", []):
            if vehicle["vin"] not in known:
                known.add(vehicle["vin"])
                entities.extend(GwmBinary(coordinator, vehicle["vin"], signal) for signal in DESCRIPTIONS)
        if entities:
            add(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class GwmBinary(GwmEntity, BinarySensorEntity):
    def __init__(self, coordinator: GwmCoordinator, identifier: str, signal: str) -> None:
        super().__init__(coordinator, identifier, signal)
        self._attr_name, self._attr_device_class = DESCRIPTIONS[signal]

    @property
    def is_on(self) -> bool | None:
        values = (self.vehicle or {}).get("values", {})
        if self.signal == "lock_open":
            locked = values.get("locked")
            return not locked if type(locked) is bool else None
        value = values.get(self.signal)
        return value if type(value) is bool else None
