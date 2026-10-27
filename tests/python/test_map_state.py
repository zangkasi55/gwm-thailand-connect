from datetime import timedelta

import pytest

from custom_components.gwm_thailand_connect.map_state import recent_vehicle
from tests.python.test_journey import NOW
from tests.python.test_journey_storage import snapshot


def test_recent_map_marker_and_status_use_the_same_actual_source_sample():
    vehicle = snapshot(0)
    vehicle["values"]["ac_active"] = False
    result = recent_vehicle(vehicle, now=NOW + timedelta(seconds=30))
    assert result["availability"] == "available"
    assert result["position"] == vehicle["location"]
    assert result["reported_at"] == NOW.isoformat()
    assert result["battery"] == 55
    assert result["locked"] is False
    assert result["doors"]["door_front_driver_open"] is False
    assert result["ac_active"] is False
    assert "vin" not in result
    assert result["expires_at"] == (NOW + timedelta(seconds=300)).isoformat()


@pytest.mark.parametrize("seconds", [301, 3600, 86400])
def test_old_cloud_fix_is_blank_even_when_backend_received_or_updated_it_now(seconds):
    vehicle = snapshot(0)
    vehicle["timestamps"]["update_time"] = (NOW + timedelta(seconds=seconds)).isoformat()
    result = recent_vehicle(vehicle, now=NOW + timedelta(seconds=seconds))
    assert result == {"availability": "stale_sample", "position": None}


@pytest.mark.parametrize("location", [
    None, {"latitude": None, "longitude": None}, {"latitude": 0, "longitude": 0},
    {"latitude": 999, "longitude": 100}, {"latitude": 12, "longitude": float("nan")},
])
def test_missing_or_invalid_fix_never_falls_back_to_home_or_an_arbitrary_city(location):
    vehicle = snapshot(0)
    vehicle["location"] = location
    assert recent_vehicle(vehicle, now=NOW)["position"] is None


def test_cloud_unavailable_and_missing_timestamp_are_explicit_blank_states():
    assert recent_vehicle(None, now=NOW) == {"availability": "cloud_unavailable", "position": None}
    vehicle = snapshot(0)
    vehicle["timestamps"] = {}
    assert recent_vehicle(vehicle, now=NOW) == {"availability": "timestamp_missing", "position": None}


def test_unknown_status_is_not_rendered_as_zero_locked_or_off():
    vehicle = snapshot(0, soc=None, locked=None)
    vehicle["values"]["door_front_driver_open"] = None
    vehicle["values"]["ac_active"] = None
    result = recent_vehicle(vehicle, now=NOW)
    assert result["battery"] is None
    assert result["locked"] is None
    assert result["doors"]["door_front_driver_open"] is None
    assert result["ac_active"] is None


def test_future_fix_beyond_clock_skew_allowance_is_blank():
    assert recent_vehicle(snapshot(60), now=NOW) == {"availability": "future_sample", "position": None}
