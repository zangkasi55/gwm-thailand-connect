import inspect
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.gwm_thailand_connect import journey_storage, journey_ws
from custom_components.gwm_thailand_connect.const import DOMAIN
from custom_components.gwm_thailand_connect.coordinator import GwmCoordinator
from custom_components.gwm_thailand_connect.journey import ObservationError
from custom_components.gwm_thailand_connect.journey_storage import JourneyStore, journey_store
from tests.python.test_integration import entry
from tests.python.test_integration import hass as hass
from tests.python.test_journey import NOW, sample


def snapshot(seconds=0, **changes):
    observation = sample(seconds, **changes)
    return {
        "vin": "SYNTHETICVIN",
        "timestamps": {"acquisition_time": observation.time.isoformat()},
        "location": observation.position,
        "values": {
            "soc": observation.soc, "locked": observation.locked,
            "door_front_driver_open": observation.doors[0],
        },
    }


async def test_cached_stale_and_out_of_order_samples_never_extend_a_stop(hass):
    store = JourneyStore(hass, "synthetic-binding")
    await store.record([snapshot(0)], now=NOW)
    await store.record([snapshot(60)], now=NOW + timedelta(seconds=60))
    await store.record([snapshot(60)], now=NOW + timedelta(seconds=120))
    await store.record([snapshot(0)], now=NOW + timedelta(seconds=120))
    await store.record([snapshot(60)], now=NOW + timedelta(seconds=900))
    result = await store.history("SYNTHETICVIN", 24, now=NOW + timedelta(seconds=900))
    assert result["sample_count"] == 2
    assert result["stops"][0]["observed_duration_seconds"] == 60
    assert result["status"] == "stale_sample" and result["stale"] is True


async def test_history_survives_restart_without_account_tokens_or_public_entity_attributes(hass):
    store = JourneyStore(hass, "synthetic-binding")
    await store.record([snapshot(0), snapshot(60)], now=NOW + timedelta(seconds=60))
    restored = JourneyStore(hass, "synthetic-binding")
    result = await restored.history("SYNTHETICVIN", 24, now=NOW + timedelta(seconds=60))
    assert result["sample_count"] == 2
    assert result["stops"][0]["battery_arrival"] == 55
    assert "vin" not in result and "SYNTHETICVIN" not in str(result)
    assert not hass.states.async_entity_ids()


async def test_one_account_shares_a_lock_and_uses_a_filesystem_safe_private_key(hass):
    import asyncio
    from pathlib import Path

    first = journey_store(hass, "cloud:tha:synthetic-binding")
    second = journey_store(hass, "cloud:tha:synthetic-binding")
    assert first is second
    assert ":" not in Path(first._store.path).name
    assert "synthetic-binding" not in Path(first._store.path).name
    await asyncio.gather(
        first.record([snapshot(0)], now=NOW),
        second.record([snapshot(60)], now=NOW + timedelta(seconds=60)),
    )
    assert len((await first._load())["SYNTHETICVIN"]) == 2


async def test_pruning_and_sample_cap_are_enforced_on_disk(hass, monkeypatch):
    monkeypatch.setattr(journey_storage, "MAX_SAMPLES", 3)
    store = JourneyStore(hass, "synthetic-binding")
    await store.record([snapshot(seconds) for seconds in (0, 60, 120, 180)], now=NOW + timedelta(seconds=180))
    assert len((await store._load())["SYNTHETICVIN"]) == 3
    await store.history("SYNTHETICVIN", 24, now=NOW + timedelta(days=8))
    restored = JourneyStore(hass, "synthetic-binding")
    assert not (await restored._load())["SYNTHETICVIN"]


async def test_disk_failure_is_not_a_success_shaped_history_save(hass, monkeypatch):
    store = JourneyStore(hass, "synthetic-binding")
    monkeypatch.setattr(store._store, "async_save", AsyncMock(side_effect=OSError("synthetic disk failure")))
    with pytest.raises(OSError):
        await store.record([snapshot(0)], now=NOW)
    assert await store._load() == {}


async def test_corrupt_history_is_rejected_not_silently_replaced(hass, monkeypatch):
    store = JourneyStore(hass, "synthetic-binding")
    monkeypatch.setattr(store._store, "async_load", AsyncMock(return_value={"vehicles": {"SYNTHETICVIN": [123]}}))
    with pytest.raises(ObservationError, match="history_schema_invalid"):
        await store.history("SYNTHETICVIN", 24, now=NOW)


async def test_recording_is_off_by_default_and_storage_failure_does_not_break_telemetry(hass):
    api = SimpleNamespace(read=AsyncMock(return_value={"vehicles": [snapshot(0)]}))
    coordinator = GwmCoordinator(hass, entry(), api)
    coordinator.history.record = AsyncMock()
    data = await coordinator._async_update_data()
    coordinator.history.record.assert_awaited_once_with([])
    assert data["vehicles"][0]["values"]["soc"] == 55
    coordinator.history.record = AsyncMock(side_effect=OSError("synthetic disk failure"))
    assert await coordinator._async_update_data() == data
    assert coordinator.history_error == "history_storage_failed"


def permission_context(hass, monkeypatch, *, admin=True, active=True, permission=True):
    configured = entry({"record_journeys": True})
    device = SimpleNamespace(
        id="synthetic-device", config_entries={configured.entry_id}, identifiers={(DOMAIN, "SYNTHETICVIN")},
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(
            is_admin=admin, is_active=active, permissions=SimpleNamespace(check_entity=Mock(return_value=permission)),
        ),
        send_result=Mock(), send_error=Mock(),
    )
    monkeypatch.setattr(journey_ws.dr, "async_get", lambda hass: SimpleNamespace(async_get=lambda device_id: device))
    monkeypatch.setattr(journey_ws.er, "async_get", lambda hass: object())
    monkeypatch.setattr(journey_ws.er, "async_entries_for_device", lambda registry, device_id: [
        SimpleNamespace(platform=DOMAIN, entity_id="sensor.synthetic_soc"),
    ])
    monkeypatch.setattr(hass, "config_entries", SimpleNamespace(async_entries=lambda domain: [configured]))
    return configured, connection


@pytest.mark.parametrize("options", [{"admin": False}, {"active": False}, {"permission": False}])
async def test_precise_location_history_requires_active_admin_and_device_permission(hass, monkeypatch, options):
    configured, connection = permission_context(hass, monkeypatch, **options)
    with pytest.raises(ValueError, match="not_authorized"):
        journey_ws.resolve_coordinator(hass, connection, "synthetic-device")
    assert not hasattr(configured, "runtime_data")


async def test_history_is_accessible_after_cloud_entry_unloads_without_new_login(hass, monkeypatch):
    configured, connection = permission_context(hass, monkeypatch)
    assert not hasattr(configured, "runtime_data")
    access = journey_ws.resolve_coordinator(hass, connection, "synthetic-device")
    assert access.coordinator is None
    assert access.identifier == "SYNTHETICVIN"
    assert access.entry is configured


async def test_websocket_read_never_writes_and_clear_requires_literal_confirmation(hass, monkeypatch):
    configured, connection = permission_context(hass, monkeypatch)
    handlers = []
    monkeypatch.setattr(journey_ws.websocket_api, "async_register_command", lambda hass, handler: handlers.append(handler))
    journey_ws.register_journey_api(hass)
    assert len(handlers) == 2
    from voluptuous import Invalid

    with pytest.raises(Invalid):
        handlers[0]._ws_schema({"id": 9, "type": DOMAIN + "/journeys", "device_id": "synthetic-device", "hours": 169})
    record = AsyncMock()
    clear = AsyncMock()
    store = SimpleNamespace(history=AsyncMock(return_value={"sample_count": 0}), clear=clear, retention_error=False)
    access = journey_ws.JourneyAccess(configured, store, "SYNTHETICVIN", None)
    monkeypatch.setattr(journey_ws, "resolve_coordinator", lambda *args, **kwargs: access)
    await inspect.unwrap(handlers[0])(hass, connection, {"id": 1, "device_id": "synthetic-device", "hours": 24})
    clear.assert_not_called()
    for value in (False, 1, "true", None):
        await inspect.unwrap(handlers[1])(hass, connection, {"id": 2, "device_id": "synthetic-device", "confirmed": value})
    clear.assert_not_called()
    await inspect.unwrap(handlers[1])(hass, connection, {"id": 3, "device_id": "synthetic-device", "confirmed": True})
    clear.assert_awaited_once_with("SYNTHETICVIN")
    record.assert_not_called()


async def test_exact_seven_day_expiry_runs_without_a_cloud_poll_or_dashboard_read(hass):
    store = JourneyStore(hass, "synthetic-binding")
    await store.record([snapshot(0), snapshot(60)], now=NOW + timedelta(seconds=60))
    assert store.next_expiry == NOW + timedelta(days=7)
    await store._expire(NOW + timedelta(days=7))
    assert len((await store._store.async_load())["vehicles"]["SYNTHETICVIN"]) == 1
    assert store.next_expiry == NOW + timedelta(days=7, seconds=60)
    await store._expire(NOW + timedelta(days=7, seconds=60))
    assert not (await store._store.async_load())["vehicles"]["SYNTHETICVIN"]
    assert store.next_expiry is None


async def test_expiry_failure_is_visible_and_retries_only_local_pruning(hass, monkeypatch):
    store = JourneyStore(hass, "synthetic-binding")
    await store.record([snapshot(0)], now=NOW)
    original = store._store.async_save
    monkeypatch.setattr(store._store, "async_save", AsyncMock(side_effect=OSError("synthetic disk failure")))
    await store._expire(NOW + timedelta(days=7))
    assert store.retention_error is True
    assert store.next_expiry == NOW + timedelta(days=7, seconds=60)
    monkeypatch.setattr(store._store, "async_save", original)
    await store._expire(NOW + timedelta(days=7, seconds=60))
    assert store.retention_error is False
    assert not (await store._load())["SYNTHETICVIN"]


async def test_startup_expires_overdue_files_even_without_a_cloud_connection(hass):
    from datetime import UTC, datetime

    store = JourneyStore(hass, "synthetic-binding")
    old = sample(0).document()
    old["time"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    await store._store.async_save({"vehicles": {"SYNTHETICVIN": [old]}})
    await store.start()
    assert not (await store._store.async_load())["vehicles"]["SYNTHETICVIN"]
    assert store.next_expiry is None
