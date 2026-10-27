"""The native hybrid power field is distinct from a combustion-engine code."""

from datetime import timedelta

import pytest

from custom_components.gwm_thailand_connect.gwm_client.models import (
    CloudVehicle,
    CloudVehicleBasics,
    VehicleIdentifier,
    parse_cloud_vehicle_status,
)
from custom_components.gwm_thailand_connect.gwm_client.snapshots import map_vehicle_snapshot
from custom_components.gwm_thailand_connect.journey import Observation, summarize
from custom_components.gwm_thailand_connect.map_state import recent_vehicle
from tests.python.test_journey import NOW


def snapshot(seconds, power, driver=False, *, engine=None, gear=None, raw_power=None, **root):
    instant = NOW + timedelta(seconds=seconds)
    status = parse_cloud_vehicle_status({
        "acquisitionTime": int(instant.timestamp() * 1000),
        "latitude": 12.0, "longitude": 100.0,
        "items": [
            {"code": "2013021", "value": "55"},
            {"code": "2206002", "value": "1" if driver else "0"},
            *[
                {"code": code, "value": value}
                for code, value in (("2016001", engine), ("2012576", gear), ("2002032", raw_power))
                if value is not None
            ],
        ],
        **root,
        **({"hyEngSts": power} if power is not None else {}),
    }, allow_stringified_numbers=True, allow_numbers_for_strings=False)
    return map_vehicle_snapshot(
        CloudVehicle(identifier=VehicleIdentifier("SYNTHETICVIN"), model_name="Synthetic PHEV"),
        status, CloudVehicleBasics(), refreshed_at=instant, remote_commands_available=False,
    ).as_dict()


@pytest.mark.parametrize(("wire", "expected"), [("0", True), ("1", False), (0, True), (1, False)])
def test_native_hybrid_power_is_explicit_only_and_ignores_contradictory_engine_code(wire, expected):
    data = snapshot(0, wire, engine="1" if expected else "0")
    assert data["values"]["vehicle_power_off"] is expected
    assert recent_vehicle(data, now=NOW)["vehicle_power_off"] is expected
    assert data["values"]["engine_state_code"] == (1 if expected else 0)


@pytest.mark.parametrize("wire", [None, "", "2", 2, "-1", -1, "off", "false", "true", "00", "1.0", " 0"])
def test_missing_unknown_or_noncanonical_hybrid_values_never_become_power_off(wire):
    assert snapshot(0, wire)["values"]["vehicle_power_off"] is None


@pytest.mark.parametrize("wire", [True, False, [], {}, 0.0, 1.0])
def test_invalid_hybrid_schema_is_rejected_explicitly(wire):
    with pytest.raises(ValueError):
        snapshot(0, wire)


@pytest.mark.parametrize("engine", ["0", "1"])
@pytest.mark.parametrize("gear", ["0", "5"])
@pytest.mark.parametrize("raw_power", ["0", "1"])
def test_no_power_off_is_inferred_from_service_expiry_gear_or_raw_engine_state(engine, gear, raw_power):
    data = snapshot(0, None, serviceStatus="0", engine=engine, gear=gear, raw_power=raw_power)
    assert data["values"]["vehicle_power_off"] is None
    assert data["values"]["engine_state_code"] == int(engine)


def test_service_expiry_does_not_override_an_explicit_power_on_field():
    assert snapshot(0, "1", serviceStatus="0")["values"]["vehicle_power_off"] is False


def test_native_parser_to_snapshot_to_journey_detects_the_ordered_driver_exit():
    snapshots = [
        snapshot(0, "1"), snapshot(60, "0"), snapshot(120, "0", driver=True), snapshot(180, "0"),
    ]
    samples = [
        Observation.from_snapshot(data, NOW + timedelta(seconds=index * 60))
        for index, data in enumerate(snapshots)
    ]
    result = summarize(samples, NOW + timedelta(seconds=180))
    stop = result["stops"][0]
    assert stop["kind"] == "parking"
    assert stop["parking_sequence"]["power_off_at"] == (NOW + timedelta(seconds=60)).isoformat()
    assert stop["parking_sequence"]["driver_open_at"] == (NOW + timedelta(seconds=120)).isoformat()
    assert stop["parking_sequence"]["driver_closed_at"] == (NOW + timedelta(seconds=180)).isoformat()
    assert result["vehicle_power_signal_available"] is True


def test_power_field_disappearing_during_a_cycle_stays_unconfirmed():
    samples = [
        Observation.from_snapshot(snapshot(0, "1"), NOW),
        Observation.from_snapshot(snapshot(60, "0"), NOW + timedelta(seconds=60)),
        Observation.from_snapshot(snapshot(120, None, driver=True), NOW + timedelta(seconds=120)),
        Observation.from_snapshot(snapshot(180, "0"), NOW + timedelta(seconds=180)),
    ]
    assert summarize(samples, NOW + timedelta(seconds=180))["stops"][0]["kind"] == "unconfirmed_stop"


def test_stale_hybrid_power_sample_cannot_be_a_current_map_status():
    assert recent_vehicle(snapshot(0, "0"), now=NOW + timedelta(seconds=301))["position"] is None
