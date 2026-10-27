"""All coordinates and states here are synthetic, never an owner's journey."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.gwm_thailand_connect.journey import Observation, ObservationError, summarize

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def sample(seconds, *, longitude=100.0, latitude=12.0, soc=55, odo=None, doors=None, locked=False, engine=None, power_off=None):
    return Observation.from_snapshot({
        "timestamps": {"acquisition_time": (NOW + timedelta(seconds=seconds)).isoformat()},
        "location": {"latitude": latitude, "longitude": longitude},
        "values": {
            "soc": soc, "locked": locked, "odometer_km": odo, "engine_state_code": engine,
            "vehicle_power_off": power_off,
            "door_front_driver_open": (doors or {}).get("driver", False),
            "door_front_passenger_open": (doors or {}).get("passenger", False),
            "door_rear_driver_side_open": False, "door_rear_passenger_side_open": False, "trunk_open": False,
        },
    }, NOW + timedelta(seconds=seconds))


def summary(samples):
    return summarize(samples, samples[-1].time)


def test_five_minute_stop_without_verified_power_is_unconfirmed_not_parking():
    result = summary([sample(seconds) for seconds in range(0, 301, 60)])
    assert result["stops"][0]["kind"] == "unconfirmed_stop"
    assert result["stops"][0]["observed_duration_seconds"] == 300
    assert result["stops"][0]["arrival_observed"] is False


def test_parking_uses_ordered_shutdown_and_driver_cycle_without_waiting_five_minutes_or_locking():
    samples = [
        sample(0, power_off=False), sample(60, power_off=True),
        sample(120, power_off=True, doors={"driver": True}),
    ]
    assert summary(samples)["stops"][0]["kind"] == "unconfirmed_stop"
    after = summary([*samples, sample(180, power_off=True)])
    assert after["stops"][0]["kind"] == "parking"
    assert after["stops"][0]["parking_sequence"] == {
        "power_off_at": (NOW + timedelta(seconds=60)).isoformat(),
        "driver_open_at": (NOW + timedelta(seconds=120)).isoformat(),
        "driver_closed_at": (NOW + timedelta(seconds=180)).isoformat(),
    }
    assert after["stops"][0]["classification_is_estimate"] is True
    assert after["doors"][0]["door"] == "door_front_driver_open"
    assert after["doors"][0]["estimated_duration_seconds"] == 60
    assert after["doors"][0]["stop_id"] == after["stops"][0]["id"]


def test_duration_alone_never_confirms_real_parking():
    samples = [sample(seconds) for seconds in range(0, 900, 60)]
    assert summary([*samples, sample(899)])["stops"][0]["kind"] == "unconfirmed_stop"
    stop = summary([*samples, sample(900)])["stops"][0]
    assert stop["kind"] == "unconfirmed_stop"
    assert stop["parking_sequence"] is None


def test_short_stops_require_at_least_two_samples_over_a_minute():
    assert not summary([sample(0), sample(59)])["stops"]
    assert summary([sample(0, power_off=False), sample(60, power_off=False)])["stops"][0]["kind"] == "temporary_stop"


@pytest.mark.parametrize("signal", [None, False])
def test_door_and_lock_events_do_not_replace_vehicle_power_off(signal):
    result = summary([
        sample(0, power_off=signal), sample(60, power_off=signal, doors={"driver": True}),
        sample(120, power_off=signal, locked=True),
    ])
    assert result["stops"][0]["kind"] != "parking"


def test_passenger_door_cycle_is_not_driver_exit():
    result = summary([
        sample(0, power_off=False), sample(60, power_off=True),
        sample(120, power_off=True, doors={"passenger": True}), sample(180, power_off=True),
    ])
    assert result["stops"][0]["kind"] == "unconfirmed_stop"


def test_opening_before_shutdown_or_simultaneous_shutdown_opening_is_not_a_verified_sequence():
    for data in (
        [sample(0, power_off=False), sample(60, power_off=False, doors={"driver": True}), sample(120, power_off=True)],
        [sample(0, power_off=False), sample(60, power_off=True, doors={"driver": True}), sample(120, power_off=True)],
    ):
        assert summary(data)["stops"][0]["parking_sequence"] is None


def test_unknown_or_restarted_vehicle_power_during_driver_cycle_prevents_confirmation():
    for power in (None, False):
        result = summary([
            sample(0, power_off=False), sample(60, power_off=True),
            sample(120, power_off=True, doors={"driver": True}), sample(180, power_off=power),
        ])
        assert result["stops"][0]["parking_sequence"] is None


def test_combustion_engine_off_is_not_vehicle_ready_off():
    result = summary([
        sample(0, engine=1), sample(60, engine=0),
        sample(120, engine=0, doors={"driver": True}), sample(180, engine=0, locked=True),
    ])
    assert result["stops"][0]["kind"] == "unconfirmed_stop"
    assert result["vehicle_power_signal_available"] is False


def test_vehicle_restart_does_not_reuse_an_old_exit_sequence_as_current_parking():
    result = summary([
        sample(0, power_off=False), sample(60, power_off=True),
        sample(120, power_off=True, doors={"driver": True}), sample(180, power_off=True),
        sample(240, power_off=False), sample(300, power_off=True),
    ])
    stop = result["stops"][0]
    assert stop["kind"] == "parking"
    assert stop["parking_sequence_current"] is False


def test_older_private_sample_schema_restores_with_unknown_vehicle_power():
    document = sample(0).document()
    document.pop("vehicle_power_off")
    assert Observation.from_document(document).vehicle_power_off is None


def test_starting_already_off_or_missing_driver_evidence_cannot_fabricate_an_exit_sequence():
    for data in (
        [sample(0, power_off=True), sample(60, power_off=True, doors={"driver": True}), sample(120, power_off=True)],
        [sample(0, power_off=False), sample(60, power_off=True), sample(120, power_off=True, doors={"driver": True}),
         sample(180, power_off=True, doors={"driver": None}), sample(240, power_off=True)],
        [sample(0, power_off=False), sample(60, power_off=True), sample(120, power_off=True, doors={"driver": True}),
         sample(900, power_off=True)],
    ):
        assert all(stop["parking_sequence"] is None for stop in summary(data)["stops"])


def test_new_exit_cycle_uses_new_shutdown_evidence_and_unknown_power_breaks_current_confidence():
    first = [
        sample(0, power_off=False), sample(60, power_off=True),
        sample(120, power_off=True, doors={"driver": True}), sample(180, power_off=True),
    ]
    interrupted = summary([*first, sample(240, power_off=None), sample(300, power_off=True)])
    assert interrupted["stops"][0]["parking_sequence_current"] is None
    renewed = summary([
        *first, sample(240, power_off=False), sample(300, power_off=True),
        sample(360, power_off=True, doors={"driver": True}), sample(420, power_off=True),
    ])
    assert renewed["stops"][0]["parking_sequence"]["power_off_at"] == (NOW + timedelta(seconds=300)).isoformat()
    assert renewed["stops"][0]["parking_sequence_current"] is True


def test_gps_noise_and_a_single_outlier_do_not_become_a_trip():
    data = [sample(0), sample(60, longitude=100.0001), sample(120, longitude=100.0012), sample(180)]
    result = summary(data)
    assert len(result["stops"]) == 1
    assert result["stops"][0]["departure_observed"] is None
    assert result["motion"] == "stopped"
    assert all(point["longitude"] != 100.0012 for segment in result["segments"] for point in segment)


def test_door_event_at_a_displaced_position_is_not_assigned_to_the_stop_by_time_alone():
    result = summary([
        sample(0), sample(60), sample(120, longitude=100.0012, doors={"driver": True}), sample(180),
    ])
    assert result["doors"][0]["stop_id"] is None


def test_confirmed_movement_closes_stop_and_records_each_stop_battery():
    result = summary([
        sample(0, soc=60), sample(60, soc=59), sample(120, longitude=100.002, soc=58),
        sample(180, longitude=100.003, soc=57), sample(240, longitude=100.003, soc=56),
    ])
    assert len(result["stops"]) == 2
    first, second = result["stops"]
    assert first["battery_arrival"] == 60
    assert first["battery_departure"] == 59
    assert first["departure_observed"] == (NOW + timedelta(seconds=120)).isoformat()
    assert second["battery_arrival"] == 57
    assert second["battery_latest"] == 56
    assert second["arrival_observed"] is True
    assert [event["kind"] for event in result["movement_events"]] == ["movement_started", "movement_stopped"]
    assert result["movement_events"][0]["battery"] == 58


def test_odometer_evidence_prevents_parking_while_creeping_inside_gps_radius():
    data = [sample(seconds, odo=10 + seconds / 600) for seconds in range(0, 961, 60)]
    result = summary(data)
    assert not result["stops"]
    assert result["motion"] in {"moving", "movement_pending"}


def test_tracking_gap_splits_map_and_never_counts_missing_time_as_dwell_or_open_door():
    result = summary([
        sample(0, doors={"driver": True}), sample(60, doors={"driver": True}),
        sample(900, doors={"driver": True}), sample(960),
    ])
    assert len(result["segments"]) == 2
    assert [stop["observed_duration_seconds"] for stop in result["stops"]] == [60, 60]
    interrupted, closed = result["doors"]
    assert interrupted["status"] == "tracking_gap"
    assert interrupted["estimated_duration_seconds"] is None
    assert interrupted["observed_open_span_seconds"] == 60
    assert closed["estimated_duration_seconds"] == 60
    assert closed["start_uncertain"] is True


def test_missing_door_state_and_initially_open_do_not_fabricate_a_closing_event():
    result = summary([
        sample(0, doors={"driver": True}), sample(60, doors={"driver": None}), sample(120),
    ])
    interval = result["doors"][0]
    assert interval["status"] == "state_not_reported"
    assert interval["end"] is None and interval["start_uncertain"] is True


def test_durations_freeze_when_no_fresh_samples_arrive():
    samples = [sample(0), sample(60)]
    result = summarize(samples, NOW + timedelta(hours=2))
    assert result["stale"] is True and result["motion"] == "unknown"
    assert result["stops"][0]["observed_duration_seconds"] == 60


def test_unknown_battery_is_not_zero_and_engine_codes_are_not_decoded_as_ignition():
    result = summary([sample(0, soc=None, engine=1), sample(60, soc=None, engine=0)])
    assert result["stops"][0]["battery_arrival"] is None
    assert result["stops"][0]["battery_latest"] is None
    assert result["engine_ignition_supported"] is False
    assert result["engine_code_changes"][0]["before"] == 1


def test_impossible_gps_jump_does_not_draw_a_teleport_or_false_parking_link():
    result = summary([sample(0), sample(60), sample(120, longitude=110), sample(180), sample(240)])
    assert result["gps_outliers"] == 1
    assert len(result["segments"]) == 2
    assert not any(point["longitude"] == 110 for segment in result["segments"] for point in segment)


def test_no_gps_still_records_doors_but_never_invents_a_map_position():
    result = summary([sample(0, longitude=None, latitude=None, doors={"driver": True}), sample(60, longitude=None)])
    assert not result["segments"] and not result["stops"]
    assert result["doors"][0]["position"] is None


@pytest.mark.parametrize("time,reason", [(None, "timestamp_missing"), ("broken", "timestamp_invalid"), ("2026-10-25T00:00:00", "timestamp_invalid")])
def test_cloud_timestamp_is_required(time, reason):
    with pytest.raises(ObservationError, match=reason):
        Observation.from_snapshot({"timestamps": {"acquisition_time": time}}, NOW)


def test_cloud_acquisition_time_wins_over_a_newer_backend_update_time():
    with pytest.raises(ObservationError, match="stale_sample"):
        Observation.from_snapshot({"timestamps": {
            "acquisition_time": (NOW - timedelta(hours=1)).isoformat(),
            "update_time": NOW.isoformat(),
        }}, NOW)
