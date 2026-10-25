"""Vehicle-bound entities; unknown cloud readings remain unknown."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GwmCoordinator


class GwmEntity(CoordinatorEntity[GwmCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: GwmCoordinator, identifier: str, signal: str) -> None:
        super().__init__(coordinator)
        self.identifier = identifier
        self.signal = signal
        self._attr_unique_id = identifier + "_" + signal

    @property
    def vehicle(self) -> dict[str, Any] | None:
        return self.coordinator.vehicle(self.identifier)

    @property
    def available(self) -> bool:
        return super().available and self.vehicle is not None

    @property
    def device_info(self) -> DeviceInfo:
        vehicle = self.vehicle or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self.identifier)},
            name=vehicle.get("name", "GWM vehicle"), manufacturer="GWM", model=vehicle.get("model"),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {"gwm_signal": self.signal}
        if self.signal == "soc" and self.vehicle is not None:
            attributes["gwm_controls"] = self.vehicle.get("thailand_controls")
            times = self.vehicle.get("timestamps", {})
            attributes["gwm_reported_at"] = times.get("update_time") or times.get("acquisition_time")
        return attributes
