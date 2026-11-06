"""Phone-number setup selects SMS OTP without real credentials or TLS work."""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from voluptuous import Invalid

from custom_components.gwm_thailand_connect import config_flow
from custom_components.gwm_thailand_connect.config_flow import GwmThailandConfigFlow, credentials_schema
from custom_components.gwm_thailand_connect.gwm_client._protocol import _Deadline
from custom_components.gwm_thailand_connect.gwm_client.thailand_client import (
    ThailandAuthenticationMethod,
    ThailandAuthState,
    ThailandClient,
    ThailandCredentials,
    ThailandVerificationRequired,
)

MOBILE = "0812345678"


@pytest.mark.parametrize("account", [MOBILE, "+66812345678", "66 81 234 5678", " 081 234 5678 "])
def test_mobile_schema_has_only_contact_and_normalizes_spaces(account):
    schema = credentials_schema(ThailandAuthenticationMethod.VERIFICATION_CODE, mobile_only=True)
    assert {field.schema for field in schema.schema} == {"account"}
    assert schema({"account": account}) == {"account": account.strip().replace(" ", "")}


@pytest.mark.parametrize("account", ["synthetic@example.invalid", "", "phone", True, 812345678])
def test_mobile_schema_rejects_non_phone_input_explicitly(account):
    with pytest.raises(Invalid):
        credentials_schema(ThailandAuthenticationMethod.VERIFICATION_CODE, mobile_only=True)({"account": account})


@pytest.mark.asyncio
async def test_selecting_mobile_or_existing_phone_password_step_never_requests_otp():
    flow = GwmThailandConfigFlow()
    flow.flow_id = "synthetic-flow"
    flow.context = {"source": "user"}
    flow._authenticate = AsyncMock(side_effect=AssertionError("Selecting a form must not request a code"))
    shown = await flow.async_step_mobile_login()
    assert shown["type"] is FlowResultType.FORM and shown["step_id"] == "mobile_login"
    assert {field.schema for field in shown["data_schema"].schema} == {"account"}
    flow._default_account = MOBILE
    redirected = await flow.async_step_password_login()
    assert redirected["step_id"] == "mobile_login"
    assert flow._method is ThailandAuthenticationMethod.VERIFICATION_CODE
    flow._authenticate.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_mobile_form_submission_reports_error_before_any_authentication():
    flow = GwmThailandConfigFlow()
    flow.flow_id = "synthetic-flow"
    flow.context = {"source": "user"}
    flow._authenticate = AsyncMock(side_effect=AssertionError("Invalid contact must not request a code"))
    result = await flow.async_step_mobile_login({"account": "synthetic@example.invalid"})
    assert result["errors"] == {"base": "invalid_account"}
    flow._authenticate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("via_password", [False, True])
async def test_submitted_mobile_uses_code_credentials_and_discards_an_unneeded_password(monkeypatch, via_password):
    flow = GwmThailandConfigFlow()
    flow.flow_id = "synthetic-flow"
    flow.context = {"source": "user"}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    flow._authenticate = AsyncMock(return_value={"type": "synthetic-consent"})
    flow.hass = Mock()
    store = Mock()
    store.auth = AsyncMock(return_value=None)
    monkeypatch.setattr(config_flow, "account_store", lambda *args: store)
    submitted = {"account": MOBILE, "password": "synthetic-must-not-be-used"}
    handler = flow.async_step_password_login if via_password else flow.async_step_mobile_login
    result = await handler(submitted)
    assert result == {"type": "synthetic-consent"}
    assert flow._credentials.authentication_method is ThailandAuthenticationMethod.VERIFICATION_CODE
    assert flow._credentials.password is None and flow._step == "mobile_login"
    assert flow._consent is False
    flow._authenticate.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_email_password_and_email_code_forms_keep_their_existing_behavior():
    flow = GwmThailandConfigFlow()
    flow.flow_id = "synthetic-flow"
    flow.context = {"source": "user"}
    flow._default_account = "synthetic@example.invalid"
    password = await flow.async_step_password_login()
    assert {field.schema for field in password["data_schema"].schema} == {"account", "password"}
    code = await flow.async_step_code_login()
    assert {field.schema for field in code["data_schema"].schema} == {"account"}
    assert flow._mobile_only is False


@pytest.mark.asyncio
async def test_type_three_sms_request_and_login_never_send_password_or_precheck():
    credentials = ThailandCredentials(
        MOBILE, None, "0" * 32, authentication_method=ThailandAuthenticationMethod.VERIFICATION_CODE,
    )
    state = ThailandAuthState.for_credentials(credentials)
    client = object.__new__(ThailandClient)
    request = AsyncMock(return_value={"synthetic": True})
    install = AsyncMock(return_value="synthetic-login-boundary")
    deadline = _Deadline(time.monotonic() + 5)
    with patch.object(client, "_request", request), patch.object(client, "_install_login_response", install):
        challenge = await client._authenticate_with_code(credentials, state, code=None, deadline=deadline)
        assert isinstance(challenge, ThailandVerificationRequired) and challenge.code_requested
        first = request.call_args
        assert first.args[1].endswith("/userAuth/getVerifyCode")
        assert first.kwargs["body"]["type"] == "3" and first.kwargs["body"]["accountType"] == "1"
        assert "password" not in first.kwargs["body"]
        await client._authenticate_with_code(credentials, challenge.state, code=None, deadline=deadline)
        assert request.await_count == 1
        await client._authenticate_with_code(credentials, challenge.state, code="123456", deadline=deadline)
        final = request.call_args
        assert final.args[1].endswith("/userAuth/loginWithVerifyCode")
        assert "password" not in final.kwargs["body"]
        assert all("checkVerifyCode" not in call.args[1] for call in request.await_args_list)
