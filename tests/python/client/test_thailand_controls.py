"""Synthetic contract tests: no real account, PIN or vehicle commands."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from custom_components.gwm_thailand_connect.gwm_client import (
    GwmApiError,
    GwmConfigurationError,
    GwmDeadlineExceededError,
    GwmRoutePolicyError,
)
from custom_components.gwm_thailand_connect.gwm_client.models import VehicleIdentifier
from custom_components.gwm_thailand_connect.gwm_client.thailand_client import (
    ThailandAuthenticated,
    ThailandClient,
    ThailandCredentials,
)
from custom_components.gwm_thailand_connect.gwm_client.thailand_controls import (
    THAILAND_CONTROLS,
    ThailandControlAction,
    ThailandControlCommand,
    parse_thailand_vehicle_features,
)
from tests.python.client.test_thailand_identity import (
    DEVICE,
    ScriptedThaiTransport,
    SyntheticIdentity,
    login_response,
)
from tests.python.client.test_thailand_identity import app_material as app_material
from tests.python.client.test_thailand_identity import synthetic_identity as synthetic_identity

VIN = "LGWTEST00XX000001"
PIN_HASH = "a" * 32
CODES = frozenset(spec.capability for spec in THAILAND_CONTROLS.values()) | {
    "1-1-9-1-1", "1-1-9-1-2", "1-1-10-1-3", "1-1-10-1-5",
}


def feature_response(codes: frozenset[str] = CODES) -> dict[str, object]:
    return {"code": "000000", "data": {"treeVO": [{"functionCode": code} for code in sorted(codes)]}}


async def connected_client(material, identity: SyntheticIdentity):
    transport = ScriptedThaiTransport(identity, [
        login_response(),
        {"code": "000000", "data": [{"vin": VIN, "ownership": 0}]},
    ])
    client = ThailandClient(material, transport=transport)
    authenticated = await client.authenticate(
        ThailandCredentials("synthetic@example.invalid", "synthetic-password", DEVICE),
        allow_session_reclaim=True,
    )
    assert isinstance(authenticated, ThailandAuthenticated)
    await client.acquire_vehicles()
    transport.requests.clear()
    return client, transport


def test_only_advertised_functions_are_eligible_and_no_engine_action_is_invented() -> None:
    features = parse_thailand_vehicle_features(feature_response()["data"])
    assert set(features.actions) == set(THAILAND_CONTROLS)
    with pytest.raises(ValueError):
        ThailandControlAction("engine_start")
    with pytest.raises(ValueError):
        ThailandControlAction("engine_stop")
    assert parse_thailand_vehicle_features({"treeVO": []}).actions == ()
    blocked = parse_thailand_vehicle_features({
        "treeVO": [{"functionCode": "1", "checked": False, "children": [{"functionCode": "1-1-2-1"}]}],
    })
    assert ThailandControlAction.UNLOCK not in blocked.actions
    incomplete = parse_thailand_vehicle_features({
        "treeVO": [{"functionCode": code} for code in ("1-1-9-1", "1-1-10-1", "1-1-10-2")],
    })
    assert incomplete.actions == ()


@pytest.mark.parametrize("action", list(ThailandControlAction))
def test_every_advertised_action_has_one_fixed_instruction(action: ThailandControlAction) -> None:
    command = ThailandControlCommand(VehicleIdentifier(VIN), action)
    instructions = command.instructions(parse_thailand_vehicle_features(feature_response()["data"]))
    assert list(instructions) == [THAILAND_CONTROLS[action].command_code]
    with pytest.raises(ValueError, match="not_supported"):
        command.instructions(parse_thailand_vehicle_features({"treeVO": []}))
    assert VIN not in repr(command)


def test_native_switch_codes_units_and_right_hand_drive_front_seats() -> None:
    features = parse_thailand_vehicle_features(feature_response()["data"])
    def make(action: ThailandControlAction) -> dict[str, object]:
        return ThailandControlCommand(
            VehicleIdentifier(VIN), action, temperature=26, duration_minutes=15,
            front_left_level=1, front_right_level=3,
        ).instructions(features)
    assert make(ThailandControlAction.LOCK) == {"0x05": {"switchOrder": "2", "operationTime": "0"}}
    assert make(ThailandControlAction.UNLOCK) == {"0x05": {"switchOrder": "1", "operationTime": "0"}}
    assert make(ThailandControlAction.CLIMATE_OFF) == {
        "0x04": {"airConditioner": {"switchOrder": "2", "operationTime": "0"}},
    }
    assert make(ThailandControlAction.CLIMATE_ON) == {
        "0x04": {"airConditioner": {"switchOrder": "1", "operationTime": "15", "temperature": "26"}},
    }
    assert make(ThailandControlAction.SEAT_VENTILATION) == {
        "0x0A": {"seat": {
            "operationMode": "2", "switchOrder": "1", "operationTime": "15",
            "rightFront": "3", "leftFront": "1",
        }},
    }
    assert make(ThailandControlAction.LOCATION_OFF) == {"0xCF": {"switchOrder": "0"}}
    assert make(ThailandControlAction.SUNROOF_CLOSE) == {"0x08": {"window": {"skyLight": 0}}}


@pytest.mark.parametrize("changes", [
    {"temperature": True}, {"temperature": 33}, {"duration_minutes": 0}, {"duration_minutes": 31},
    {"front_right_level": 4}, {"front_left_level": -1}, {"action": "lock"},
])
def test_invalid_control_settings_are_not_coerced(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(ThailandControlCommand(VehicleIdentifier(VIN), ThailandControlAction.LOCK), **changes)


@pytest.mark.asyncio
async def test_command_checks_fresh_capabilities_then_pin_and_sends_exactly_once(
    app_material, synthetic_identity: SyntheticIdentity,
) -> None:
    client, transport = await connected_client(app_material, synthetic_identity)
    transport.responses.extend([feature_response(), {"code": "000000"}, {"code": "000000"}])
    accepted = await client.send_control(
        ThailandControlCommand(VehicleIdentifier(VIN), ThailandControlAction.UNLOCK),
        security_password_hash=PIN_HASH,
    )
    assert len(accepted.command_id) == 36 and accepted.command_id.endswith("1234")
    assert [request.operation for request in transport.requests] == [
        "get_vehicle_features", "check_security_password", "send_thailand_control",
    ]
    check = json.loads(transport.requests[1].body)
    assert check == {"type": "2", "securityPassword": PIN_HASH}
    sent = transport.requests[2]
    assert sent.headers["vin"] == VIN
    body = json.loads(sent.body)
    assert body == {
        "vin": VIN, "seqNo": accepted.command_id, "remoteType": "0", "type": 2,
        "securityPassword": PIN_HASH, "instructions": {"0x05": {"switchOrder": "1", "operationTime": "0"}},
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_revoked_capability_prevents_even_pin_submission(app_material, synthetic_identity: SyntheticIdentity) -> None:
    client, transport = await connected_client(app_material, synthetic_identity)
    transport.responses.append(feature_response())
    await client.get_vehicle_features(VehicleIdentifier(VIN))
    transport.requests.clear()
    transport.responses.append(feature_response(frozenset()))
    with pytest.raises(GwmRoutePolicyError):
        await client.send_control(
            ThailandControlCommand(VehicleIdentifier(VIN), ThailandControlAction.UNLOCK),
            security_password_hash=PIN_HASH,
        )
    assert [r.operation for r in transport.requests] == ["get_vehicle_features"]


@pytest.mark.asyncio
async def test_rejected_pin_never_sends_vehicle_command(app_material, synthetic_identity: SyntheticIdentity) -> None:
    client, transport = await connected_client(app_material, synthetic_identity)
    transport.responses.extend([feature_response(), {"code": "308001"}])
    with pytest.raises(GwmApiError):
        await client.send_control(
            ThailandControlCommand(VehicleIdentifier(VIN), ThailandControlAction.LOCK),
            security_password_hash=PIN_HASH,
        )
    assert [r.operation for r in transport.requests] == ["get_vehicle_features", "check_security_password"]


@pytest.mark.asyncio
async def test_undiscovered_vehicle_or_invalid_pin_hash_makes_no_http_request(
    app_material, synthetic_identity: SyntheticIdentity,
) -> None:
    client, transport = await connected_client(app_material, synthetic_identity)
    for identifier, pin in ((VIN, "plaintext"), ("LGWTEST00XX000002", PIN_HASH)):
        with pytest.raises(GwmConfigurationError):
            await client.send_control(
                ThailandControlCommand(VehicleIdentifier(identifier), ThailandControlAction.LOCK),
                security_password_hash=pin,
            )
    assert not transport.requests


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "state"), [
    ("0", "completed"), ("6", "completed"), ("1000", "pending"), ("2000", "pending"), ("21", "failed"),
])
async def test_results_use_native_numeric_codes_not_untrusted_success_text(
    app_material, synthetic_identity: SyntheticIdentity, code: str, state: str,
) -> None:
    client, transport = await connected_client(app_material, synthetic_identity)
    sequence = "b" * 32 + "1234"
    transport.responses.append({"code": "000000", "data": [{
        "remoteType": "0x05", "resultCode": code, "resultMsg": "success",
    }]})
    result = await client.get_control_result(VehicleIdentifier(VIN), sequence, ThailandControlAction.LOCK)
    assert result is not None and result.state == state
    assert result.result_message is None
    assert all(request.method == "GET" for request in transport.requests)


@pytest.mark.asyncio
async def test_unrelated_result_is_not_reported_as_success(app_material, synthetic_identity: SyntheticIdentity) -> None:
    client, transport = await connected_client(app_material, synthetic_identity)
    transport.responses.append({"code": "000000", "data": [{"remoteType": "0x04", "resultCode": "0"}]})
    assert await client.get_control_result(
        VehicleIdentifier(VIN), "b" * 32 + "1234", ThailandControlAction.LOCK,
    ) is None


@pytest.mark.asyncio
async def test_uncertain_write_timeout_is_not_retried(
    app_material, synthetic_identity: SyntheticIdentity, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, transport = await connected_client(app_material, synthetic_identity)
    transport.responses.extend([feature_response(), {"code": "000000"}])
    original = transport.execute
    sends = 0

    async def execute(request, **kwargs):
        nonlocal sends
        if request.operation == "send_thailand_control":
            sends += 1
            raise GwmDeadlineExceededError(operation=request.operation)
        return await original(request, **kwargs)

    monkeypatch.setattr(transport, "execute", execute)
    with pytest.raises(GwmDeadlineExceededError):
        await client.send_control(
            ThailandControlCommand(VehicleIdentifier(VIN), ThailandControlAction.LOCK),
            security_password_hash=PIN_HASH,
        )
    assert sends == 1
