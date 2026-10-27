"""Native Thai sign-in forms. Selecting a method never submits credentials."""

from __future__ import annotations

import json
import secrets
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.helpers.storage import Store

from .const import APP_STORAGE, DEFAULT_POLL_SECONDS, DOMAIN, MAX_POLL_SECONDS, MIN_POLL_SECONDS, NAME
from .gwm_client.errors import GwmClientError, GwmRateLimitError
from .gwm_client.thailand_client import (
    ThailandAuthenticated,
    ThailandAuthenticationMethod,
    ThailandAuthState,
    ThailandClient,
    ThailandCredentials,
    ThailandInitializationRequired,
    ThailandSessionConsentRequired,
    ThailandVerificationRequired,
)
from .gwm_client.thailand_identity import ThailandIdentityError
from .gwm_client.thailand_protocol import (
    ThailandAppMaterial,
    ThailandProtocolError,
    load_thailand_app_material_file,
    serialize_thailand_app_material,
)
from .storage import AccountStore, account_store


def password_selector(*, one_time: bool = False) -> selector.TextSelector:
    return selector.TextSelector(selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD,
        autocomplete="one-time-code" if one_time else "current-password",
    ))


def credentials_schema(
    method: ThailandAuthenticationMethod, account: str = "",
) -> vol.Schema:
    fields: dict[vol.Marker, object] = {vol.Required("account", default=account): str}
    if method is ThailandAuthenticationMethod.PASSWORD:
        fields[vol.Required("password")] = password_selector()
    return vol.Schema(fields)


def _read_material(hass: HomeAssistant, file_id: str) -> dict[str, Any]:
    with process_uploaded_file(hass, file_id) as uploaded:
        document = json.loads(serialize_thailand_app_material(load_thailand_app_material_file(uploaded)))
        if not isinstance(document, dict):
            raise ValueError("Invalid private app material")
        return document


class GwmThailandConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._method = ThailandAuthenticationMethod.VERIFICATION_CODE
        self._credentials: ThailandCredentials | None = None
        self._state: ThailandAuthState | None = None
        self._store: AccountStore | None = None
        self._entry: ConfigEntry | None = None
        self._material: ThailandAppMaterial | None = None
        self._consent = False
        self._force_login = False
        self._step = "code_login"
        self._default_account = ""
        self._initialization_reason = ""

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> GwmThailandOptionsFlow:
        return GwmThailandOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_material()

    async def async_step_material(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        material_path = Path(self.hass.config.path(".storage", APP_STORAGE))
        try:
            if user_input is not None:
                document = await self.hass.async_add_executor_job(
                    _read_material, self.hass, user_input["app_material"],
                )
                await Store[dict[str, Any]](
                    self.hass, 1, APP_STORAGE, private=True, atomic_writes=True,
                ).async_save(document)
            self._material = await self.hass.async_add_executor_job(
                load_thailand_app_material_file, material_path,
            )
        except (ThailandProtocolError, ThailandIdentityError, ValueError):
            if user_input is not None or material_path.exists():
                errors["base"] = "invalid_material"
        except OSError:
            errors["base"] = "material_storage_failed"
        else:
            return self.async_show_menu(
                step_id="authentication", menu_options=["code_login", "password_login"],
            )
        return self.async_show_form(
            step_id="material",
            data_schema=vol.Schema({
                vol.Required("app_material"): selector.FileSelector(selector.FileSelectorConfig(accept=".json")),
            }),
            errors=errors,
        )

    async def async_step_code_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._method = ThailandAuthenticationMethod.VERIFICATION_CODE
        return await self._credentials_form(user_input)

    async def async_step_password_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._method = ThailandAuthenticationMethod.PASSWORD
        return await self._credentials_form(user_input)

    async def _credentials_form(self, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        self._step = "password_login" if self._method is ThailandAuthenticationMethod.PASSWORD else "code_login"
        if user_input is not None:
            try:
                credentials = ThailandCredentials(
                    user_input["account"],
                    user_input.get("password") if self._method is ThailandAuthenticationMethod.PASSWORD else None,
                    secrets.token_hex(16), authentication_method=self._method,
                )
                if (
                    self._entry is not None
                    and self._entry.unique_id != credentials.account_binding
                ):
                    return self._show("invalid_account")
                await self.async_set_unique_id(credentials.account_binding)
                if self._entry is None or self._entry.unique_id != credentials.account_binding:
                    self._abort_if_unique_id_configured()
                self._store = account_store(self.hass, credentials.account_binding)
                state = await self._store.auth()
                if state is not None:
                    credentials = replace(credentials, device_id=state.device_id)
                    if state.authentication_method is not self._method:
                        state = replace(state, authentication_method=self._method, verification_requested_at=None)
                        self._force_login = True
                    if self._force_login:
                        state = replace(state, verification_requested_at=None)
                self._credentials = credentials
                self._state = state
                self._consent = False
            except (KeyError, TypeError, ValueError):
                return self._show("invalid_account")
            return await self._authenticate()
        return self._show()

    def _show(self, error: str | None = None) -> ConfigFlowResult:
        schema = credentials_schema(self._method, self._default_account)
        if self._step == "consent":
            schema = vol.Schema({vol.Required("confirm", default=False): bool})
        elif self._step == "verification":
            schema = vol.Schema({vol.Required("verification_code"): password_selector(one_time=True)})
        elif self._step == "initialization":
            schema = vol.Schema({})
        elif self._step == "new_device":
            schema = vol.Schema({vol.Required("confirm", default=False): bool})
        return self.async_show_form(
            step_id=self._step, data_schema=schema,
            errors={"base": error} if error else {},
            description_placeholders={"reason": self._initialization_reason},
        )

    async def async_step_consent(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._step = "consent"
        if user_input is None or user_input.get("confirm") is not True:
            return self._show("confirmation_required" if user_input else None)
        self._consent = True
        return await self._authenticate()

    async def async_step_verification(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._step = "verification"
        if user_input is None:
            return self._show()
        return await self._authenticate(verification_code=user_input.get("verification_code"))

    async def async_step_initialization(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._step = "initialization"
        return self._show() if user_input is None else await self._authenticate()

    async def async_step_new_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._step = "new_device"
        if user_input is None or user_input.get("confirm") is not True:
            return self._show("confirmation_required" if user_input else None)
        if self._credentials is None:
            return self.async_abort(reason="invalid_flow_state")
        self._method = ThailandAuthenticationMethod.VERIFICATION_CODE
        self._credentials = replace(
            self._credentials, device_id=secrets.token_hex(16), password=None,
            authentication_method=self._method,
        )
        self._state = ThailandAuthState.for_credentials(self._credentials)
        self._force_login = False
        self._consent = True
        return await self._authenticate()

    async def _authenticate(self, *, verification_code: str | None = None) -> ConfigFlowResult:
        if self._material is None or self._credentials is None or self._store is None:
            return self.async_abort(reason="invalid_flow_state")
        client = ThailandClient(self._material)
        try:
            result = await client.authenticate(
                self._credentials, state=self._state, verification_code=verification_code,
                allow_session_reclaim=self._consent, force_login=self._force_login,
            )
            self._state = result.state
            await self._store.save_auth(result.state)
        except GwmRateLimitError:
            return self._show("rate_limited")
        except OSError:
            return self._show("material_storage_failed")
        except GwmClientError:
            return self._show("authentication_failed")
        finally:
            await client.aclose()
        if isinstance(result, ThailandSessionConsentRequired):
            self._step = "consent"
        elif isinstance(result, ThailandVerificationRequired):
            self._step = "verification"
            return self._show("invalid_code" if result.code_rejected else None)
        elif isinstance(result, ThailandInitializationRequired):
            self._force_login = False
            self._initialization_reason = result.reason
            self._step = "new_device" if result.reason == "device_certificate_exists" else "initialization"
        elif isinstance(result, ThailandAuthenticated):
            data: dict[str, Any] = {
                "account": self._credentials.account,
                "authentication_method": self._method.value,
            }
            if self._method is ThailandAuthenticationMethod.PASSWORD:
                data["password"] = self._credentials.password
            if self._entry is not None:
                self.hass.config_entries.async_update_entry(
                    self._entry, data=data, unique_id=self._credentials.account_binding,
                )
                await self.hass.config_entries.async_reload(self._entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            return self.async_create_entry(title=NAME, data=data)
        return self._show()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._entry = self._get_reauth_entry()
        self._default_account = self._entry.data["account"]
        self._force_login = True
        return await self.async_step_material()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._entry = self._get_reconfigure_entry()
        self._default_account = self._entry.data["account"]
        self._force_login = True
        return await self.async_step_material()


class GwmThailandOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            existing = self.config_entry.options.get("security_pin", "")
            provided = user_input.get("security_pin")
            pin = provided.strip() if isinstance(provided, str) and provided.strip() else existing
            if user_input.get("enable_remote_commands") is True and not pin:
                errors["security_pin"] = "pin_required"
            else:
                data = dict(user_input)
                if data.get("enable_remote_commands") is True:
                    data["security_pin"] = pin
                else:
                    data.pop("security_pin", None)
                return self.async_create_entry(data=data)
        current = self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required("poll_interval", default=current.get("poll_interval", DEFAULT_POLL_SECONDS)):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_SECONDS, max=MAX_POLL_SECONDS)),
            vol.Required("enable_remote_commands", default=current.get("enable_remote_commands", False)): bool,
            vol.Optional("security_pin"): password_selector(),
            vol.Required("record_journeys", default=current.get("record_journeys", False)): bool,
        }), errors=errors)
