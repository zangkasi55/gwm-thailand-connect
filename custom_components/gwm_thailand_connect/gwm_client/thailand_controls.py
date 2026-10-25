"""Capability-bound controls recovered from the official Thai Android app."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from .models import VehicleIdentifier


class ThailandControlAction(StrEnum):
    LOCK = "lock"
    UNLOCK = "unlock"
    FLASH_LIGHTS = "flash_lights"
    HORN = "horn"
    HORN_AND_LIGHTS = "horn_and_lights"
    TAILGATE_OPEN = "tailgate_open"
    TAILGATE_CLOSE = "tailgate_close"
    WINDOWS_CLOSE = "windows_close"
    SUNROOF_CLOSE = "sunroof_close"
    CLIMATE_ON = "climate_on"
    CLIMATE_OFF = "climate_off"
    PURIFIER_ON = "purifier_on"
    PURIFIER_OFF = "purifier_off"
    SEAT_VENTILATION = "seat_ventilation"
    SEATS_OFF = "seats_off"
    LOCATION_ON = "location_on"
    LOCATION_OFF = "location_off"


@dataclass(frozen=True, slots=True)
class ThailandControlSpec:
    label: str
    capability: str
    command_code: str
    group: str


THAILAND_CONTROLS: Mapping[ThailandControlAction, ThailandControlSpec] = MappingProxyType({
    ThailandControlAction.LOCK: ThailandControlSpec("Lock doors", "1-1-2-2", "0x05", "locks"),
    ThailandControlAction.UNLOCK: ThailandControlSpec("Unlock doors", "1-1-2-1", "0x05", "locks"),
    ThailandControlAction.FLASH_LIGHTS: ThailandControlSpec("Flash lights", "1-1-3-1", "0x06", "find"),
    ThailandControlAction.HORN: ThailandControlSpec("Sound horn", "1-1-3-2", "0x06", "find"),
    ThailandControlAction.HORN_AND_LIGHTS: ThailandControlSpec("Horn and lights", "1-1-3-3", "0x06", "find"),
    ThailandControlAction.TAILGATE_OPEN: ThailandControlSpec("Open tailgate", "1-1-4-1", "0x09", "tailgate"),
    ThailandControlAction.TAILGATE_CLOSE: ThailandControlSpec("Close tailgate", "1-1-4-2", "0x09", "tailgate"),
    ThailandControlAction.WINDOWS_CLOSE: ThailandControlSpec("Close all windows", "1-1-6-2", "0x08", "windows"),
    ThailandControlAction.SUNROOF_CLOSE: ThailandControlSpec("Close sunroof", "1-1-7-2", "0x08", "sunroof"),
    ThailandControlAction.CLIMATE_ON: ThailandControlSpec("A/C on", "1-1-9-1", "0x04", "climate"),
    ThailandControlAction.CLIMATE_OFF: ThailandControlSpec("A/C off", "1-1-9-2", "0x04", "climate"),
    ThailandControlAction.PURIFIER_ON: ThailandControlSpec("Air purifier on", "1-1-9-1-5", "0x0C", "climate"),
    ThailandControlAction.PURIFIER_OFF: ThailandControlSpec("Air purifier off", "1-1-9-1-5", "0x0C", "climate"),
    ThailandControlAction.SEAT_VENTILATION: ThailandControlSpec(
        "Set front-seat ventilation", "1-1-10-1", "0x0A", "seats",
    ),
    ThailandControlAction.SEATS_OFF: ThailandControlSpec("Front-seat ventilation off", "1-1-10-2", "0x0A", "seats"),
    ThailandControlAction.LOCATION_ON: ThailandControlSpec("Location reporting on", "1-1-15", "0xCF", "location"),
    ThailandControlAction.LOCATION_OFF: ThailandControlSpec("Location reporting off", "1-1-15", "0xCF", "location"),
})


@dataclass(frozen=True, slots=True)
class ThailandVehicleFeatures:
    codes: frozenset[str] = field(repr=False)

    @property
    def actions(self) -> tuple[ThailandControlAction, ...]:
        actions = [action for action, spec in THAILAND_CONTROLS.items() if spec.capability in self.codes]
        if not {"1-1-9-1-1", "1-1-9-1-2"}.issubset(self.codes):
            actions = [action for action in actions if action is not ThailandControlAction.CLIMATE_ON]
        if not {"1-1-10-1-3", "1-1-10-1-5"} & self.codes:
            actions = [
                action for action in actions
                if action not in {ThailandControlAction.SEAT_VENTILATION, ThailandControlAction.SEATS_OFF}
            ]
        return tuple(actions)


def parse_thailand_vehicle_features(data: object) -> ThailandVehicleFeatures:
    """Missing/false capabilities never authorize a command."""

    if not isinstance(data, Mapping) or not isinstance(data.get("treeVO"), list):
        raise ValueError("thailand_features_invalid")
    codes: set[str] = set()
    remaining = 1000

    def visit(nodes: list[object], depth: int, allowed: bool) -> None:
        nonlocal remaining
        if depth > 8 or len(nodes) > 100:
            raise ValueError("thailand_features_invalid")
        for node in nodes:
            remaining -= 1
            if remaining < 0 or not isinstance(node, Mapping):
                raise ValueError("thailand_features_invalid")
            code, checked, children = node.get("functionCode"), node.get("checked"), node.get("children")
            if (
                not isinstance(code, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", code) is None
                or checked is not None and type(checked) is not bool
                or children is not None and not isinstance(children, list)
            ):
                raise ValueError("thailand_features_invalid")
            enabled = allowed and checked is not False
            if enabled:
                codes.add(code)
            if isinstance(children, list) and children:
                visit(children, depth + 1, enabled)

    visit(data["treeVO"], 0, True)
    return ThailandVehicleFeatures(frozenset(codes))


@dataclass(frozen=True, slots=True, repr=False)
class ThailandControlCommand:
    identifier: VehicleIdentifier = field(repr=False)
    action: ThailandControlAction
    temperature: int = 24
    duration_minutes: int = 10
    front_left_level: int = 0
    front_right_level: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.identifier) is not VehicleIdentifier or type(self.action) is not ThailandControlAction
            or type(self.temperature) is not int or not 16 <= self.temperature <= 32
            or type(self.duration_minutes) is not int or not 5 <= self.duration_minutes <= 30
            or type(self.front_left_level) is not int or not 0 <= self.front_left_level <= 3
            or type(self.front_right_level) is not int or not 0 <= self.front_right_level <= 3
        ):
            raise ValueError("thailand_control_invalid")

    def instructions(self, features: ThailandVehicleFeatures) -> dict[str, object]:
        if type(features) is not ThailandVehicleFeatures or self.action not in features.actions:
            raise ValueError("thailand_control_not_supported")
        action = self.action
        body: dict[str, object]
        if action in {ThailandControlAction.LOCK, ThailandControlAction.UNLOCK}:
            body = {"switchOrder": "2" if action is ThailandControlAction.LOCK else "1", "operationTime": "0"}
        elif action in {
            ThailandControlAction.FLASH_LIGHTS, ThailandControlAction.HORN, ThailandControlAction.HORN_AND_LIGHTS,
        }:
            body = {"search": {
                "whistle": "0" if action is ThailandControlAction.FLASH_LIGHTS else "1",
                "flashing": "0" if action is ThailandControlAction.HORN else "1",
            }}
        elif action in {ThailandControlAction.TAILGATE_OPEN, ThailandControlAction.TAILGATE_CLOSE}:
            body = {
                "switchOrder": "1" if action is ThailandControlAction.TAILGATE_OPEN else "2",
                "operationTime": "0",
            }
        elif action is ThailandControlAction.WINDOWS_CLOSE:
            body = {"window": {"leftFront": 0, "leftBack": 0, "rightFront": 0, "rightBack": 0}}
        elif action is ThailandControlAction.SUNROOF_CLOSE:
            body = {"window": {"skyLight": 0}}
        elif action in {ThailandControlAction.CLIMATE_ON, ThailandControlAction.CLIMATE_OFF}:
            air: dict[str, object] = {
                "switchOrder": "1" if action is ThailandControlAction.CLIMATE_ON else "2",
                "operationTime": str(self.duration_minutes) if action is ThailandControlAction.CLIMATE_ON else "0",
            }
            if action is ThailandControlAction.CLIMATE_ON:
                if not {"1-1-9-1-1", "1-1-9-1-2"}.issubset(features.codes):
                    raise ValueError("thailand_control_not_supported")
                air["temperature"] = str(self.temperature)
            body = {"airConditioner": air}
        elif action in {ThailandControlAction.PURIFIER_ON, ThailandControlAction.PURIFIER_OFF}:
            body = {"switchOrder": "1" if action is ThailandControlAction.PURIFIER_ON else "2"}
            if action is ThailandControlAction.PURIFIER_ON:
                body["operationTime"] = str(self.duration_minutes)
        elif action in {ThailandControlAction.SEAT_VENTILATION, ThailandControlAction.SEATS_OFF}:
            if action is ThailandControlAction.SEATS_OFF:
                return {"0x0A": {"seat": {
                    "operationMode": "2", "switchOrder": "2", "operationTime": "0",
                    "leftFront": "0", "rightFront": "0", "leftBack": "0", "rightBack": "0",
                }}}
            seat: dict[str, object] = {
                "operationMode": "2", "switchOrder": "1",
                "operationTime": str(self.duration_minutes),
            }
            for code, name, level in (
                ("1-1-10-1-3", "rightFront", self.front_right_level),
                ("1-1-10-1-5", "leftFront", self.front_left_level),
            ):
                if code in features.codes:
                    seat[name] = str(level)
                elif level:
                    raise ValueError("thailand_control_not_supported")
            if not {"leftFront", "rightFront"} & seat.keys():
                raise ValueError("thailand_control_not_supported")
            body = {"seat": seat}
        elif action in {ThailandControlAction.LOCATION_ON, ThailandControlAction.LOCATION_OFF}:
            body = {"switchOrder": "1" if action is ThailandControlAction.LOCATION_ON else "0"}
        else:
            raise ValueError("thailand_control_not_supported")
        return {THAILAND_CONTROLS[action].command_code: body}
