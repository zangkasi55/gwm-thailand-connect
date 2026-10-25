"""Account-bound Thai authentication, cached reads and capability-bound controls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
import ssl
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self
from urllib.parse import quote

from ._protocol import _AsyncTransport, _Deadline, _TransportRequest, _TransportResponse
from .async_utils import _blocking_call
from .commands import RemoteCommandAcceptance, RemoteCommandResult, RemoteCommandState
from .config import RequestTimeouts
from .errors import (
    GwmApiError,
    GwmAuthenticationError,
    GwmClientError,
    GwmClosedError,
    GwmConfigurationError,
    GwmDeadlineExceededError,
    GwmRateLimitError,
    GwmRoutePolicyError,
    GwmSchemaError,
    GwmSignatureError,
    is_overseas_refresh_rejected,
)
from .models import (
    CloudVehicle,
    CloudVehicleStatus,
    GwmSession,
    VehicleIdentifier,
    parse_cloud_vehicle_status,
    parse_cloud_vehicles,
)
from .responses import _decode_envelope
from .thailand_controls import (
    THAILAND_CONTROLS,
    ThailandControlAction,
    ThailandControlCommand,
    ThailandVehicleFeatures,
    parse_thailand_vehicle_features,
)
from .thailand_identity import (
    _ERROR_CATEGORIES as _IDENTITY_ERROR_CATEGORIES,
)
from .thailand_identity import (
    ThailandCertificateRequest,
    ThailandIdentityError,
    ThailandIssuedIdentity,
    _device_binding,
    complete_thailand_certificate_request,
    create_thailand_bootstrap_ssl_context,
    create_thailand_certificate_request,
    create_thailand_issued_ssl_context,
)
from .thailand_protocol import (
    ThailandAppMaterial,
    ThailandProtocolError,
    sign_thailand_request,
)
from .transport import AiohttpTransport

_AUTH = "https://ap-h5-gateway.gwmcloud.com/app-api/api/"
_APP = "https://ap-app-gateway.gwmcloud.com/app-api/api/v1.0/"
_CERTIFICATE = "https://ap-app-gateway-common.gwmcloud.com/app-api/api/v1.0/"
_DEVICE_ID = re.compile(r"[0-9a-f]{32}")
_BINDING = re.compile(r"[0-9a-f]{64}")
_VERIFICATION_CODES = frozenset({"309702", "308103", "110641"})
_WRONG_VERIFICATION_CODES = frozenset({"308011", "308012"})
_VERIFICATION_INTERVAL = timedelta(minutes=10)
_HEADERS = {
    "appId": "1",
    "brand": "1",
    "terminal": "GW_APP_Haval",
    "enterpriseId": "CC01",
    "systemType": "1",
    "rs": "2",
    "cVer": "3.0.8",
    "secVersion": "2.0",
    "country": "TH",
    "regionCode": "TH",
    "channel": "APP",
    "language": "en",
    "timezone": "GMT+07:00",
    "ip": "0.0.0.0",
    "Accept": "application/json",
}


class ThailandAuthenticationMethod(StrEnum):
    PASSWORD = "password"
    VERIFICATION_CODE = "verification_code"


@dataclass(frozen=True, slots=True)
class ThailandClientConfig:
    timeouts: RequestTimeouts = field(default_factory=RequestTimeouts)
    max_response_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            type(self.timeouts) is not RequestTimeouts
            or type(self.max_response_bytes) is not int
            or not 0 < self.max_response_bytes <= 16 * 1024 * 1024
        ):
            raise GwmConfigurationError()


@dataclass(frozen=True, slots=True)
class ThailandCredentials:
    account: str = field(repr=False)
    password: str | None = field(repr=False)
    device_id: str = field(repr=False)
    country: str = "TH"
    authentication_method: ThailandAuthenticationMethod | str = ThailandAuthenticationMethod.PASSWORD

    def __post_init__(self) -> None:
        try:
            method = ThailandAuthenticationMethod(self.authentication_method)
        except (TypeError, ValueError):
            raise GwmConfigurationError(operation="login") from None
        if (
            not isinstance(self.account, str)
            or not isinstance(self.device_id, str)
            or _DEVICE_ID.fullmatch(self.device_id) is None
            or not isinstance(self.country, str)
            or self.country.strip().upper() != "TH"
        ):
            raise GwmConfigurationError(operation="login")
        account = self.account.strip().replace(" ", "")
        if method is ThailandAuthenticationMethod.PASSWORD:
            if not isinstance(self.password, str) or not self.password:
                raise GwmConfigurationError(operation="login")
        elif self.password not in (None, ""):
            raise GwmConfigurationError(operation="login")
        try:
            valid = (
                0 < len(account.encode("utf-8")) <= 4096
                and len((self.password or "").encode("utf-8")) <= 64 * 1024
                and all(ord(character) >= 0x20 for character in account)
            )
        except UnicodeEncodeError:
            valid = False
        if not valid:
            raise GwmConfigurationError(operation="login")
        object.__setattr__(self, "account", account)
        object.__setattr__(self, "country", "TH")
        object.__setattr__(self, "authentication_method", method)
        if method is ThailandAuthenticationMethod.VERIFICATION_CODE:
            object.__setattr__(self, "password", None)

    @property
    def account_binding(self) -> str:
        return hashlib.sha256(b"gwm-thailand-account-v1\0TH\0" + self.account.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ThailandAuthState:
    account_binding: str = field(repr=False)
    device_id: str = field(repr=False)
    country: str = "TH"
    access_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    gw_id: str | None = field(default=None, repr=False)
    issued_identity: ThailandIssuedIdentity | None = field(default=None, repr=False)
    verification_requested_at: datetime | None = field(default=None, repr=False)
    authentication_method: ThailandAuthenticationMethod | str = ThailandAuthenticationMethod.PASSWORD
    certificate_request: ThailandCertificateRequest | None = field(default=None, repr=False)
    pending_certificate: str | None = field(default=None, repr=False)
    certificate_conflict: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        try:
            method = ThailandAuthenticationMethod(self.authentication_method)
        except (TypeError, ValueError):
            raise GwmConfigurationError(operation="login") from None
        object.__setattr__(self, "authentication_method", method)
        if (
            not isinstance(self.account_binding, str)
            or _BINDING.fullmatch(self.account_binding) is None
            or not isinstance(self.device_id, str)
            or _DEVICE_ID.fullmatch(self.device_id) is None
            or self.country != "TH"
            or (self.issued_identity is not None and type(self.issued_identity) is not ThailandIssuedIdentity)
            or (self.certificate_request is not None and type(self.certificate_request) is not ThailandCertificateRequest)
            or self.pending_certificate is not None and (
                not isinstance(self.pending_certificate, str) or not 0 < len(self.pending_certificate) <= 96 * 1024
                or self.certificate_request is None
            )
            or self.issued_identity is not None and (
                self.certificate_request is not None or self.pending_certificate is not None
            )
            or type(self.certificate_conflict) is not bool
            or self.certificate_conflict and (
                self.access_token is None or self.issued_identity is not None or self.pending_certificate is not None
                or self.certificate_request is None
            )
            or (
                self.verification_requested_at is not None
                and (
                    not isinstance(self.verification_requested_at, datetime)
                    or self.verification_requested_at.tzinfo is None
                )
            )
        ):
            raise GwmConfigurationError(operation="login")
        for value in (self.access_token, self.refresh_token, self.gw_id):
            if value is not None and not _valid_token(value):
                raise GwmConfigurationError(operation="login")
        if (self.access_token is None) != (self.gw_id is None):
            raise GwmConfigurationError(operation="login")
        if self.access_token is None and (
            self.refresh_token is not None or self.issued_identity is not None or self.certificate_request is not None
        ):
            raise GwmConfigurationError(operation="login")
        if (
            self.certificate_request is not None and self.gw_id is not None
            and self.certificate_request.binding != _device_binding(self.gw_id, self.device_id)
        ):
            raise GwmConfigurationError(operation="login")
        if (
            self.issued_identity is not None
            and self.gw_id is not None
            and self.issued_identity.binding != _device_binding(self.gw_id, self.device_id)
        ):
            raise GwmConfigurationError(operation="login")

    @classmethod
    def for_credentials(cls, credentials: ThailandCredentials) -> ThailandAuthState:
        if type(credentials) is not ThailandCredentials:
            raise GwmConfigurationError(operation="login")
        return cls(
            account_binding=credentials.account_binding, device_id=credentials.device_id,
            authentication_method=credentials.authentication_method,
        )

    def matches(self, credentials: object) -> bool:
        return (
            type(credentials) is ThailandCredentials
            and self.account_binding == credentials.account_binding
            and self.device_id == credentials.device_id
            and self.country == credentials.country
            and self.authentication_method == credentials.authentication_method
        )


@dataclass(frozen=True, slots=True)
class ThailandAuthenticated:
    state: ThailandAuthState = field(repr=False)
    session: GwmSession = field(repr=False)
    material: ThailandAppMaterial = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.state) is not ThailandAuthState
            or type(self.session) is not GwmSession
            or type(self.material) is not ThailandAppMaterial
            or self.state.issued_identity is None
            or self.state.access_token != self.session.access_token
            or self.state.gw_id != self.session.gw_id
            or self.state.device_id != self.session.device_id
            or self.session.country != "TH"
            or not _valid_context(self.session.app_ssl_context)
        ):
            raise GwmConfigurationError(operation="login")


@dataclass(frozen=True, slots=True)
class ThailandVerificationRequired:
    state: ThailandAuthState = field(repr=False)
    code_requested: bool = False
    code_rejected: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.state) is not ThailandAuthState
            or type(self.code_requested) is not bool
            or type(self.code_rejected) is not bool
        ):
            raise GwmConfigurationError(operation="verify_code")


@dataclass(frozen=True, slots=True)
class ThailandSessionConsentRequired:
    state: ThailandAuthState = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.state) is not ThailandAuthState:
            raise GwmConfigurationError(operation="login")


@dataclass(frozen=True, slots=True)
class ThailandInitializationRequired:
    state: ThailandAuthState = field(repr=False)
    reason: str

    def __post_init__(self) -> None:
        if (
            type(self.state) is not ThailandAuthState or self.state.access_token is None
            or self.state.gw_id is None or not isinstance(self.reason, str)
            or self.reason not in _IDENTITY_ERROR_CATEGORIES | {
                "certificate_response_invalid", "enrollment_failed", "device_certificate_exists",
            }
        ):
            raise GwmConfigurationError(operation="enroll_certificate")


type ThailandAuthenticationResult = (
    ThailandAuthenticated | ThailandVerificationRequired | ThailandSessionConsentRequired | ThailandInitializationRequired
)


class ThailandClient:
    """Own a verified Thai session; controls require fresh advertised capabilities."""

    def __init__(
        self,
        material: ThailandAppMaterial,
        *,
        config: ThailandClientConfig | None = None,
        state: ThailandAuthState | None = None,
        session: GwmSession | None = None,
        transport: _AsyncTransport | None = None,
    ) -> None:
        configuration = ThailandClientConfig() if config is None else config
        if type(material) is not ThailandAppMaterial or type(configuration) is not ThailandClientConfig:
            raise GwmConfigurationError()
        if state is not None and type(state) is not ThailandAuthState:
            raise GwmConfigurationError(operation="login")
        if session is not None:
            if state is None:
                raise GwmConfigurationError(operation="login")
            ThailandAuthenticated(state, session, material)
        self._material = material
        self._config = configuration
        self._state = state
        self._session = session
        self._bootstrap_context: ssl.SSLContext | None = None
        self._owns_transport = transport is None
        self._transport = (
            AiohttpTransport.create_owned(max_response_bytes=configuration.max_response_bytes)
            if transport is None else transport
        )
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._closing = False
        self._vehicles: dict[str, CloudVehicle] = {}
        self._vehicle_roles: dict[str, int] = {}
        self._features: dict[str, ThailandVehicleFeatures] = {}

    @property
    def authenticated(self) -> bool:
        return self._session is not None and not self._closed and not self._closing

    async def __aenter__(self) -> Self:
        self._ensure_open()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            try:
                async with self._lock:
                    if self._owns_transport:
                        await self._transport.aclose()
            except BaseException:
                self._closing = False
                raise
            self._session = None
            self._state = None
            self._bootstrap_context = None
            self._vehicles.clear()
            self._vehicle_roles.clear()
            self._features.clear()
            self._closed = True
            self._closing = False

    async def authenticate(
        self,
        credentials: ThailandCredentials,
        *,
        state: ThailandAuthState | None = None,
        verification_code: str | None = None,
        allow_session_reclaim: bool = False,
        allow_password_login: bool = True,
        force_login: bool = False,
        timeout: float | None = None,
    ) -> ThailandAuthenticationResult:
        if (
            type(credentials) is not ThailandCredentials
            or type(allow_session_reclaim) is not bool
            or type(allow_password_login) is not bool
            or type(force_login) is not bool
            or (allow_session_reclaim and not allow_password_login)
            or (force_login and not allow_password_login)
            or (state is not None and (type(state) is not ThailandAuthState or not state.matches(credentials)))
        ):
            raise GwmConfigurationError(operation="login")
        candidate = ThailandAuthState.for_credentials(credentials) if state is None else state
        code = None
        if verification_code is not None:
            if not isinstance(verification_code, str):
                raise GwmConfigurationError(operation="verify_code")
            code = verification_code.strip()
            if not 0 < len(code) <= 64 or not code.isascii() or not code.isalnum():
                raise GwmConfigurationError(operation="verify_code")
        async with self._operation("login", timeout) as deadline:
            if candidate.access_token is not None and not force_login:
                return await self._install(candidate, deadline)
            if not allow_password_login:
                raise GwmAuthenticationError(operation="login")
            if not allow_session_reclaim:
                return ThailandSessionConsentRequired(candidate)
            if credentials.authentication_method is ThailandAuthenticationMethod.VERIFICATION_CODE:
                return await self._authenticate_with_code(
                    credentials, candidate, code=code, deadline=deadline
                )
            if code is not None:
                try:
                    await self._request(
                        "verify_code", _AUTH + "v2.0/userAuth/checkVerifyCode", credentials.device_id,
                        deadline=deadline,
                        body={
                            "account": credentials.account, "verifyCode": code, "type": "17",
                            "accountType": "2" if "@" in credentials.account else "1",
                            "countryCode": "+66", "validCodeMode": 1,
                        },
                    )
                except GwmApiError as error:
                    if error.api_code in _WRONG_VERIFICATION_CODES:
                        return ThailandVerificationRequired(candidate, code_rejected=True)
                    raise
            body: dict[str, object] = {
                "account": credentials.account,
                "accountType": "2" if "@" in credentials.account else "1",
                "countryCode": "+66",
                "agreement": [1, 2],
                "password": credentials.password,
                "deviceId": credentials.device_id,
                "appType": "0",
                "pushToken": None,
                "country": "TH",
            }
            if code is not None:
                body.update(verifyCode=code, validCodeMode="1")
            try:
                data = await self._request(
                    "login", _AUTH + "v2.0/userAuth/loginWithPassword", credentials.device_id,
                    deadline=deadline, body=body,
                )
            except GwmApiError as error:
                if error.api_code == "308001":
                    raise GwmAuthenticationError(operation="login", api_code=error.api_code) from None
                if code is not None and error.api_code in _WRONG_VERIFICATION_CODES:
                    return ThailandVerificationRequired(candidate, code_rejected=True)
                if error.api_code not in _VERIFICATION_CODES:
                    raise
                now = _utc_now()
                requested = candidate.verification_requested_at
                if requested is not None and timedelta(0) <= now - requested < _VERIFICATION_INTERVAL:
                    return ThailandVerificationRequired(candidate)
                await self._request(
                    "request_verification", _AUTH + "v2.0/userAuth/getVerifyCode", credentials.device_id,
                    deadline=deadline,
                    body={
                        "type": "17", "account": credentials.account,
                        "accountType": "2" if "@" in credentials.account else "1",
                        "countryCode": "+66", "validCodeMode": 1, "operateCode": "",
                        "captchaType": "", "captchaId": "", "token": "",
                    },
                )
                return ThailandVerificationRequired(
                    replace(candidate, verification_requested_at=now), code_requested=True
                )
            return await self._install_login_response(candidate, data, deadline)

    async def _authenticate_with_code(
        self,
        credentials: ThailandCredentials,
        candidate: ThailandAuthState,
        *,
        code: str | None,
        deadline: _Deadline,
    ) -> ThailandAuthenticated | ThailandVerificationRequired | ThailandInitializationRequired:
        if code is None:
            now = _utc_now()
            requested = candidate.verification_requested_at
            if requested is not None and timedelta(0) <= now - requested < _VERIFICATION_INTERVAL:
                return ThailandVerificationRequired(candidate)
            await self._request(
                "request_verification", _AUTH + "v2.0/userAuth/getVerifyCode", credentials.device_id,
                deadline=deadline,
                body={
                    "type": "3", "account": credentials.account,
                    "accountType": "2" if "@" in credentials.account else "1",
                    "countryCode": "+66", "validCodeMode": 1, "operateCode": "",
                    "captchaType": "", "captchaId": "", "token": "",
                },
            )
            return ThailandVerificationRequired(
                replace(candidate, verification_requested_at=now), code_requested=True,
            )
        if candidate.verification_requested_at is None:
            raise GwmConfigurationError(operation="verify_code")
        # The app submits a type-3 login code directly; a separate check can consume it.
        try:
            data = await self._request(
                "login", _AUTH + "v2.0/userAuth/loginWithVerifyCode", credentials.device_id,
                deadline=deadline,
                body={
                    "account": credentials.account, "verifyCode": code,
                    "accountType": "2" if "@" in credentials.account else "1",
                    "deviceId": credentials.device_id, "appType": "0",
                    "countryCode": "+66", "agreement": [1, 2],
                    "pushToken": None, "country": "TH", "validCodeMode": 1,
                },
            )
        except GwmApiError as error:
            if error.api_code in _WRONG_VERIFICATION_CODES:
                return ThailandVerificationRequired(candidate, code_rejected=True)
            raise
        return await self._install_login_response(candidate, data, deadline)

    async def _install_login_response(
        self, candidate: ThailandAuthState, data: object, deadline: _Deadline,
    ) -> ThailandAuthenticated | ThailandInitializationRequired:
        accepted_account = _token(data, "gwId", operation="login")
        if candidate.gw_id is not None and candidate.gw_id != accepted_account:
            raise GwmAuthenticationError(operation="login")
        candidate = replace(
            candidate,
            access_token=_token(data, "accessToken", operation="login"),
            refresh_token=_optional_token(data, "refreshToken", operation="login"),
            gw_id=accepted_account,
            verification_requested_at=None,
        )
        return await self._install(candidate, deadline)

    async def refresh_thailand_session(
        self,
        credentials: ThailandCredentials,
        state: ThailandAuthState,
        *,
        timeout: float | None = None,
    ) -> ThailandAuthenticated:
        if type(state) is not ThailandAuthState or not state.matches(credentials) or state.refresh_token is None:
            raise GwmAuthenticationError(operation="refresh_token")
        async with self._operation("refresh_token", timeout) as deadline:
            if (
                self._state is not None
                and self._session is not None
                and self._state.matches(credentials)
                and self._state.access_token != state.access_token
            ):
                return ThailandAuthenticated(self._state, self._session, self._material)
            try:
                data = await self._request(
                    "refresh_token", _AUTH + "v1.0/userAuth/refreshToken", state.device_id,
                    deadline=deadline,
                    body={
                        "accessToken": state.access_token,
                        "refreshToken": state.refresh_token,
                        "deviceId": state.device_id,
                    },
                    access_token="",
                )
            except GwmApiError as error:
                if is_overseas_refresh_rejected(error) or error.api_code == "607501":
                    self._session = None
                    raise GwmAuthenticationError(operation="refresh_token", api_code=error.api_code) from None
                raise
            gw_id = _optional_token(data, "gwId", operation="refresh_token") or state.gw_id
            if gw_id != state.gw_id:
                raise GwmAuthenticationError(operation="refresh_token")
            renewed = replace(
                state,
                access_token=_token(data, "accessToken", operation="refresh_token"),
                refresh_token=_token(data, "refreshToken", operation="refresh_token"),
            )
            result = await self._install(renewed, deadline)
            if isinstance(result, ThailandInitializationRequired):
                raise GwmConfigurationError(operation="enroll_certificate")
            return result

    async def acquire_vehicles(self, *, timeout: float | None = None) -> tuple[CloudVehicle, ...]:
        async with self._operation("acquire_vehicles", timeout) as deadline:
            session = self._require_session()
            data = await self._request(
                "acquire_vehicles", _APP + "globalapp/vehicle/acquireVehicles", session.device_id,
                deadline=deadline, access_token=session.access_token,
                context=session.app_ssl_context,
            )
            try:
                vehicles = parse_cloud_vehicles(data, allow_numbers_for_strings=False)
                roles = {}
                if not isinstance(data, list):
                    raise ValueError("vehicle_schema_invalid")
                for item in data:
                    ownership = item.get("ownership")
                    if ownership is None:
                        ownership = 0
                    if type(ownership) is not int or not 0 <= ownership <= 10:
                        raise ValueError("vehicle_schema_invalid")
                    roles[item["vin"]] = ownership + 1
            except (TypeError, ValueError):
                raise GwmSchemaError(operation="acquire_vehicles") from None
            self._vehicles = {vehicle.identifier.value: vehicle for vehicle in vehicles}
            self._vehicle_roles = roles
            self._features = {vin: features for vin, features in self._features.items() if vin in self._vehicles}
            return vehicles

    async def get_vehicle_features(
        self, identifier: VehicleIdentifier, *, refresh: bool = False, timeout: float | None = None,
    ) -> ThailandVehicleFeatures:
        async with self._operation("get_vehicle_features", timeout) as deadline:
            session = self._require_discovered_vehicle(identifier)
            if refresh or identifier.value not in self._features:
                data = await self._request(
                    "get_vehicle_features",
                    _APP + "vehicle/findVehicleCapabilityItem?vin=" + identifier.encoded
                    + "&userRole=" + str(self._vehicle_roles[identifier.value]),
                    session.device_id, deadline=deadline, access_token=session.access_token,
                    context=session.app_ssl_context, vin=identifier.value,
                )
                try:
                    self._features[identifier.value] = parse_thailand_vehicle_features(data)
                except (TypeError, ValueError):
                    self._features.pop(identifier.value, None)
                    raise GwmSchemaError(operation="get_vehicle_features") from None
            return self._features[identifier.value]

    async def send_control(
        self, command: ThailandControlCommand, *, security_password_hash: str,
        timeout: float | None = None,
    ) -> RemoteCommandAcceptance:
        operation = "send_thailand_control"
        if (
            type(command) is not ThailandControlCommand or not isinstance(security_password_hash, str)
            or re.fullmatch(r"[0-9a-f]{32}", security_password_hash) is None
        ):
            raise GwmConfigurationError(operation=operation)
        features = await self.get_vehicle_features(command.identifier, refresh=True, timeout=timeout)
        try:
            instructions = command.instructions(features)
        except ValueError:
            raise GwmRoutePolicyError(operation=operation) from None
        async with self._operation(operation, timeout) as deadline:
            session = self._require_discovered_vehicle(command.identifier)
            await self._request(
                "check_security_password", _AUTH + "v1.0/userAuth/checkSecurityPassword",
                session.device_id, deadline=deadline, access_token=session.access_token,
                body={"type": "2", "securityPassword": security_password_hash},
            )
            sequence = secrets.token_hex(16) + "1234"
            await self._request(
                operation, _APP + "vehicle/T5/sendCmd", session.device_id,
                deadline=deadline, access_token=session.access_token, context=session.app_ssl_context,
                vin=command.identifier.value,
                body={
                    "vin": command.identifier.value, "seqNo": sequence, "remoteType": "0",
                    "instructions": instructions, "securityPassword": security_password_hash, "type": 2,
                },
            )
            return RemoteCommandAcceptance(sequence)

    async def get_control_result(
        self, identifier: VehicleIdentifier, command_id: str, action: ThailandControlAction,
        *, timeout: float | None = None,
    ) -> RemoteCommandResult | None:
        operation = "get_remote_command_result"
        if (
            not isinstance(command_id, str) or re.fullmatch(r"[0-9a-f]{32}1234", command_id) is None
            or type(action) is not ThailandControlAction
        ):
            raise GwmConfigurationError(operation=operation)
        async with self._operation(operation, timeout) as deadline:
            session = self._require_discovered_vehicle(identifier)
            data = await self._request(
                operation, _APP + "vehicle/getRemoteCtrlResultT5?seqNo=" + quote(command_id, safe=""),
                session.device_id, deadline=deadline, access_token=session.access_token,
                context=session.app_ssl_context, vin=identifier.value,
            )
            if not isinstance(data, list) or len(data) > 64 or any(not isinstance(item, dict) for item in data):
                raise GwmSchemaError(operation=operation)
            expected = THAILAND_CONTROLS[action].command_code.casefold()
            matching = [
                item for item in data
                if isinstance(item.get("remoteType"), str) and item["remoteType"].casefold() == expected
                and item.get("seqNo", command_id) == command_id
            ]
            if not matching:
                return None
            if len(matching) != 1 or not isinstance(matching[0].get("resultCode"), str):
                raise GwmSchemaError(operation=operation)
            code = matching[0]["resultCode"]
            if re.fullmatch(r"-?[0-9]{1,8}", code) is None:
                raise GwmSchemaError(operation=operation)
            state: RemoteCommandState = (
                "completed" if code in {"0", "6"} else "pending" if code in {"1000", "2000"} else "failed"
            )
            return RemoteCommandResult(state=state, result_code=code)

    def _require_discovered_vehicle(self, identifier: VehicleIdentifier) -> GwmSession:
        session = self._require_session()
        if type(identifier) is not VehicleIdentifier or identifier.value not in self._vehicles:
            raise GwmConfigurationError(operation="request")
        return session

    async def get_last_status(
        self, identifier: VehicleIdentifier, *, timeout: float | None = None,
    ) -> CloudVehicleStatus:
        async with self._operation("get_last_status", timeout) as deadline:
            session = self._require_session()
            if type(identifier) is not VehicleIdentifier or identifier.value not in self._vehicles:
                raise GwmConfigurationError(operation="get_last_status")
            data = await self._request(
                "get_last_status",
                _APP + "vehicle/getLastStatus?vin=" + identifier.encoded + "&seqNo=&modelId=",
                session.device_id, deadline=deadline, access_token=session.access_token,
                context=session.app_ssl_context, vin=identifier.value,
            )
            try:
                return parse_cloud_vehicle_status(
                    data, allow_stringified_numbers=True, allow_numbers_for_strings=False
                )
            except (TypeError, ValueError):
                raise GwmSchemaError(operation="get_last_status") from None

    async def _install(
        self, state: ThailandAuthState, deadline: _Deadline,
    ) -> ThailandAuthenticated | ThailandInitializationRequired:
        if state.access_token is None or state.gw_id is None:
            raise GwmAuthenticationError(operation="login")
        if state.certificate_conflict:
            return ThailandInitializationRequired(state, "device_certificate_exists")
        access_token = state.access_token
        gw_id = state.gw_id
        identity = state.issued_identity
        if identity is None or identity.expires_at <= _utc_now() + timedelta(days=1):
            request = state.certificate_request
            if request is None:
                try:
                    request = await _blocking_call(
                        lambda: create_thailand_certificate_request(gw_id, state.device_id, now=_utc_now())
                    )
                except ThailandIdentityError as error:
                    return ThailandInitializationRequired(state, error.category)
                state = replace(state, issued_identity=None, certificate_request=request)
            encoded = state.pending_certificate
            if encoded is None:
                try:
                    data = await self._request(
                        "enroll_certificate", _CERTIFICATE + "appAuth/applyCertificate", state.device_id,
                        deadline=deadline, access_token=access_token,
                        body={"csr": request.csr, "phone": gw_id},
                    )
                    encoded = _text(data, "encoded", operation="enroll_certificate", maximum=96 * 1024)
                except GwmSchemaError:
                    return ThailandInitializationRequired(state, "certificate_response_invalid")
                except GwmAuthenticationError:
                    raise
                except GwmApiError as error:
                    if error.api_code == "310022":
                        return ThailandInitializationRequired(
                            replace(state, certificate_conflict=True), "device_certificate_exists",
                        )
                    return ThailandInitializationRequired(state, "enrollment_failed")
                except GwmClientError:
                    return ThailandInitializationRequired(state, "enrollment_failed")
                state = replace(state, pending_certificate=encoded)
            enrollment_request = request
            encoded_certificate = encoded
            try:
                identity = await _blocking_call(
                    lambda: complete_thailand_certificate_request(
                        enrollment_request, encoded_certificate, material=self._material.bootstrap,
                        gw_id=gw_id, device_id=state.device_id, now=_utc_now(),
                    )
                )
            except ThailandIdentityError as error:
                return ThailandInitializationRequired(state, error.category)
            state = replace(
                state, issued_identity=identity, certificate_request=None, pending_certificate=None
            )
        issued_identity = identity
        try:
            context = await _blocking_call(
                lambda: create_thailand_issued_ssl_context(
                    issued_identity, material=self._material.bootstrap,
                    gw_id=gw_id, device_id=state.device_id, now=_utc_now(),
                )
            )
        except ThailandIdentityError as error:
            return ThailandInitializationRequired(state, error.category)
        if deadline.remaining(asyncio.get_running_loop().time()) <= 0:
            raise GwmDeadlineExceededError(operation="enroll_certificate")
        session = GwmSession("TH", state.device_id, access_token, context, gw_id)
        result = ThailandAuthenticated(state, session, self._material)
        if self._state is None or self._state.account_binding != state.account_binding:
            self._vehicles.clear()
            self._vehicle_roles.clear()
            self._features.clear()
        self._state = state
        self._session = session
        return result

    async def _request(
        self,
        operation: str,
        url: str,
        device_id: str,
        *,
        deadline: _Deadline,
        body: dict[str, object] | None = None,
        access_token: str | None = None,
        context: ssl.SSLContext | None = None,
        vin: str | None = None,
    ) -> object:
        if context is None:
            if self._bootstrap_context is None:
                try:
                    self._bootstrap_context = await _blocking_call(
                        lambda: create_thailand_bootstrap_ssl_context(self._material.bootstrap, now=_utc_now())
                    )
                except ThailandIdentityError:
                    raise GwmConfigurationError(operation=operation) from None
            context = self._bootstrap_context
        try:
            serialized = None if body is None else json.dumps(
                body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            signed = sign_thailand_request(
                self._material.signing, "GET" if body is None else "POST", url, serialized
            )
            headers = {**_HEADERS, **signed.headers, "deviceId": device_id, "iccid": device_id}
            if access_token is not None:
                headers["accessToken"] = access_token
            if vin is not None:
                headers["vin"] = vin
            if serialized is not None:
                headers["Content-Type"] = "application/json; charset=utf-8"
            request = _TransportRequest(
                operation, signed.url, headers, context, signed.method,
                None if signed.body is None else signed.body.encode("utf-8"),
            )
        except (ThailandProtocolError, ValueError, TypeError, UnicodeEncodeError):
            raise GwmConfigurationError(operation=operation) from None
        response = await self._transport.execute(
            request, deadline=deadline, connect_timeout=self._config.timeouts.connect,
            read_timeout=self._config.timeouts.read,
        )
        try:
            if operation == "enroll_certificate":
                return _decode_certificate_reply(response)
            return _decode_envelope(
                response, operation=operation,
            )
        except GwmApiError as error:
            if error.api_code == "607099":
                raise GwmSignatureError(operation=operation, api_code=error.api_code) from None
            if error.api_code in {"308008", "308024"}:
                raise GwmRateLimitError(operation=operation, api_code=error.api_code) from None
            raise

    @asynccontextmanager
    async def _operation(self, operation: str, timeout: float | None) -> AsyncIterator[_Deadline]:
        self._ensure_open()
        duration = self._config.timeouts.total if timeout is None else timeout
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int | float)
            or not math.isfinite(duration)
            or duration <= 0
        ):
            raise GwmConfigurationError(operation=operation)
        deadline = _Deadline(asyncio.get_running_loop().time() + duration)
        try:
            async with asyncio.timeout(duration):
                async with self._lock:
                    self._ensure_open()
                    yield deadline
        except TimeoutError:
            raise GwmDeadlineExceededError(operation=operation) from None

    def _require_session(self) -> GwmSession:
        if self._session is None:
            raise GwmAuthenticationError(operation="request")
        return self._session

    def _ensure_open(self) -> None:
        if self._closed or self._closing:
            raise GwmClosedError()


def _valid_token(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 16 * 1024
        and all(0x21 <= ord(character) <= 0x7E for character in value)
    )


def _decode_certificate_reply(response: _TransportResponse) -> object:
    data = _decode_envelope(
        response, operation="enroll_certificate",
        accepted_api_codes=frozenset({"000000", "310022"}),
    )
    if isinstance(data, Mapping) and isinstance(data.get("encoded"), str) and data["encoded"]:
        return data
    # The common validator has already checked the JSON structure and duplicate keys.
    envelope = json.loads(response.body)
    if envelope["code"] == "310022":
        raise GwmApiError(operation="enroll_certificate", api_code="310022")
    return data


def _valid_context(context: ssl.SSLContext) -> bool:
    return (
        isinstance(context, ssl.SSLContext)
        and context.check_hostname
        and context.verify_mode == ssl.CERT_REQUIRED
        and context.security_level >= 2
        and context.minimum_version >= ssl.TLSVersion.TLSv1_2
    )


def _text(data: object, name: str, *, operation: str, maximum: int) -> str:
    value = data.get(name) if isinstance(data, Mapping) else None
    if not isinstance(value, str) or not 0 < len(value) <= maximum:
        raise GwmSchemaError(operation=operation)
    return value


def _token(data: object, name: str, *, operation: str) -> str:
    value = _text(data, name, operation=operation, maximum=16 * 1024)
    if not _valid_token(value):
        raise GwmSchemaError(operation=operation)
    return value


def _optional_token(data: object, name: str, *, operation: str) -> str | None:
    value = data.get(name) if isinstance(data, Mapping) else None
    if value is None:
        return None
    return _token(data, name, operation=operation)


def _utc_now() -> datetime:
    return datetime.now(UTC)
