"""Real HA types with synthetic identities and scripted HTTP, never an owner account."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.gwm_thailand_connect import SERVICE_SCHEMA
from custom_components.gwm_thailand_connect.api import AccountApi
from custom_components.gwm_thailand_connect.binary_sensor import GwmBinary
from custom_components.gwm_thailand_connect.config_flow import GwmThailandConfigFlow, credentials_schema
from custom_components.gwm_thailand_connect.const import APP_STORAGE, DOMAIN
from custom_components.gwm_thailand_connect.coordinator import GwmCoordinator
from custom_components.gwm_thailand_connect.gwm_client.errors import GwmApiError
from custom_components.gwm_thailand_connect.gwm_client.thailand_client import (
    ThailandAuthenticated,
    ThailandAuthenticationMethod,
    ThailandClient,
    ThailandCredentials,
    ThailandSessionConsentRequired,
    ThailandVerificationRequired,
)
from custom_components.gwm_thailand_connect.gwm_client.thailand_protocol import (
    load_thailand_app_material_file,
    serialize_thailand_app_material,
)
from custom_components.gwm_thailand_connect.storage import account_store, decode_state, encode_state
from tests.python.client.test_thailand_controls import feature_response
from tests.python.client.test_thailand_identity import DEVICE, ScriptedThaiTransport, login_response
from tests.python.client.test_thailand_identity import app_material as app_material
from tests.python.client.test_thailand_identity import synthetic_identity as synthetic_identity


@pytest_asyncio.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    yield instance
    await instance.async_block_till_done()
    await instance.async_stop(force=True)


def entry(options=None):
    return ConfigEntry(
        data={"account": "owner@example.invalid", "authentication_method": "password"},
        domain=DOMAIN, title="Synthetic GWM", version=1, minor_version=1,
        source="user", unique_id="synthetic-account", options=options or {},
        discovery_keys=MappingProxyType({}), subentries_data=None,
    )


@pytest.mark.parametrize("method", list(ThailandAuthenticationMethod))
def test_password_is_rendered_and_required_only_for_password_login(method):
    from voluptuous import Invalid

    schema = credentials_schema(method)
    keys = {marker.schema for marker in schema.schema}
    if method is ThailandAuthenticationMethod.PASSWORD:
        assert "password" in keys
        with pytest.raises(Invalid):
            schema({"account": "owner@example.invalid"})
    else:
        assert keys == {"account"}
        assert schema({"account": "owner@example.invalid"}) == {"account": "owner@example.invalid"}


@pytest.mark.parametrize("confirm", [False, None, 1, "true"])
def test_control_service_requires_literal_owner_confirmation(confirm):
    from voluptuous import Invalid

    with pytest.raises(Invalid):
        SERVICE_SCHEMA({"device_id": "synthetic-device", "action": "lock", "confirmed": confirm})


async def test_selecting_a_method_does_not_authenticate_or_request_codes(hass, monkeypatch):
    flow = GwmThailandConfigFlow()
    flow.hass = hass
    flow.flow_id = "synthetic-method"
    flow.handler = DOMAIN
    flow.context = {"source": "user"}
    authenticate = AsyncMock(side_effect=AssertionError("Method choice must be local only"))
    monkeypatch.setattr(flow, "_authenticate", authenticate)
    code = await flow.async_step_code_login()
    password = await flow.async_step_password_login()
    assert "password" not in {marker.schema for marker in code["data_schema"].schema}
    assert "password" in {marker.schema for marker in password["data_schema"].schema}
    authenticate.assert_not_called()


async def test_private_state_round_trip_and_concurrent_journal_updates(
    hass, app_material, synthetic_identity,
):
    credentials = ThailandCredentials("owner@example.invalid", "synthetic-password", DEVICE)
    transport = ScriptedThaiTransport(synthetic_identity, [login_response()])
    async with ThailandClient(app_material, transport=transport) as client:
        accepted = await client.authenticate(credentials, allow_session_reclaim=True)
    assert isinstance(accepted, ThailandAuthenticated)
    assert decode_state(encode_state(accepted.state)) == accepted.state
    store = account_store(hass, credentials.account_binding)
    assert account_store(hass, credentials.account_binding) is store
    command = {
        "vehicle": "SYNTHETICVIN", "action": "lock", "sequence": "a" * 32 + "1234",
        "created": "2026-10-22T00:00:00+00:00", "state": "pending",
    }
    await asyncio.gather(store.save_auth(accepted.state), store.save_command(command))
    assert await store.auth() == accepted.state
    assert await store.commands() == [command]
    assert "synthetic-password" not in str(encode_state(accepted.state))


async def test_private_material_uses_this_integration_actual_ha_store_key(hass, app_material):
    document = json.loads(serialize_thailand_app_material(app_material))
    await Store(hass, 1, APP_STORAGE, private=True, atomic_writes=True).async_save(document)
    path = Path(hass.config.path(".storage", APP_STORAGE))
    assert load_thailand_app_material_file(path) == app_material


async def test_real_snapshot_mapping_preserves_soc_and_boolean_lock_meanings(
    hass, app_material, synthetic_identity,
):
    credentials = ThailandCredentials("owner@example.invalid", "synthetic-password", DEVICE)
    transport = ScriptedThaiTransport(synthetic_identity, [
        login_response(),
        {"code": "000000", "data": [{"vin": "SYNTHETICVIN", "modelName": "H6 PHEV"}]},
        {"code": "000000", "data": {"items": [
            {"code": "2013021", "value": 55}, {"code": "2203001", "value": 1},
        ]}},
        feature_response(),
    ])
    client = ThailandClient(app_material, transport=transport)
    authenticated = await client.authenticate(credentials, allow_session_reclaim=True)
    assert isinstance(authenticated, ThailandAuthenticated)
    store = account_store(hass, credentials.account_binding)
    await store.save_auth(authenticated.state)
    api = AccountApi(entry(), credentials, client, store)
    data = await api.read()
    assert data["vehicles"][0]["values"]["soc"] == 55
    assert data["vehicles"][0]["thailand_controls"]["enabled"] is False
    coordinator = GwmCoordinator(hass, entry(), api)
    coordinator.data = data
    sensor = GwmBinary(coordinator, "SYNTHETICVIN", "lock_open")
    data["vehicles"][0]["values"]["locked"] = True
    assert sensor.is_on is False
    data["vehicles"][0]["values"]["locked"] = False
    assert sensor.is_on is True
    data["vehicles"][0]["values"]["locked"] = None
    assert sensor.is_on is None
    await api.close()


@pytest.mark.parametrize(("code", "exception"), [("607501", ConfigEntryAuthFailed), ("570062", UpdateFailed)])
async def test_owner_action_errors_do_not_clear_private_state_or_reauthenticate(hass, code, exception):
    api = AsyncMock(spec=AccountApi)
    api.read.side_effect = GwmApiError(operation="acquire_vehicles", api_code=code)
    coordinator = GwmCoordinator(hass, entry(), api)
    with pytest.raises(exception):
        await coordinator._async_update_data()
    assert api.mock_calls == [("read", (), {})]


async def test_disabled_control_never_reads_or_submits_a_pin(hass, app_material, synthetic_identity):
    client = ThailandClient(app_material, transport=ScriptedThaiTransport(synthetic_identity, []))
    credentials = ThailandCredentials("owner@example.invalid", "synthetic-password", DEVICE)
    api = AccountApi(entry(), credentials, client, account_store(hass, credentials.account_binding))
    with pytest.raises(HomeAssistantError):
        await api.send("SYNTHETICVIN", "unlock", confirmed=True)
    assert client.authenticated is False
    await api.close()


async def test_forced_owner_reauthentication_keeps_the_issued_key_and_uses_no_old_tokens(
    app_material, synthetic_identity,
):
    credentials = ThailandCredentials(
        "owner@example.invalid", None, DEVICE,
        authentication_method=ThailandAuthenticationMethod.VERIFICATION_CODE,
    )
    initial = ScriptedThaiTransport(synthetic_identity, [{"code": "000000"}, login_response()])
    async with ThailandClient(app_material, transport=initial) as client:
        challenge = await client.authenticate(credentials, allow_session_reclaim=True)
        assert isinstance(challenge, ThailandVerificationRequired)
        accepted = await client.authenticate(
            credentials, state=challenge.state, verification_code="123456", allow_session_reclaim=True,
        )
    assert isinstance(accepted, ThailandAuthenticated)
    response = login_response()
    response["data"]["accessToken"] = "synthetic-new-access"
    transport = ScriptedThaiTransport(synthetic_identity, [{"code": "000000"}, response])
    async with ThailandClient(app_material, transport=transport) as client:
        consent = await client.authenticate(credentials, state=accepted.state, force_login=True)
        assert isinstance(consent, ThailandSessionConsentRequired)
        assert not transport.requests
        challenge = await client.authenticate(
            credentials, state=accepted.state, force_login=True, allow_session_reclaim=True,
        )
        assert isinstance(challenge, ThailandVerificationRequired)
        renewed = await client.authenticate(
            credentials, state=challenge.state, force_login=True, allow_session_reclaim=True,
            verification_code="123456",
        )
    assert isinstance(renewed, ThailandAuthenticated)
    assert renewed.state.issued_identity == accepted.state.issued_identity
    assert renewed.state.access_token == "synthetic-new-access"
    assert [request.operation for request in transport.requests] == ["request_verification", "login"]
    assert all("accessToken" not in request.headers for request in transport.requests)
