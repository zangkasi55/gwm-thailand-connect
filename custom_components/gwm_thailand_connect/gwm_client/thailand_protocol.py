"""Artifact-provisioned Thai signing with fixed authentication and read routes.

The native policy was recovered from com.gwm.tha 3.0.8 and verified against
the v2 gateway with synthetic reserved-character input. Actual account/MFA
authentication still needs owner testing. No HTTP requests, vehicle commands
or password normalization happen here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import quote_plus, unquote_plus, urlsplit

from .signing import SignedRequest
from .thailand_identity import (
    _APP_ARTIFACT_SHA256,
    ThailandBootstrapMaterial,
    create_thailand_bootstrap_ssl_context,
    load_thailand_bootstrap_material_from_apk,
)

_MEMBER: Final = "lib/arm64-v8a/libapp.so"
_MEMBER_SIZE: Final = 13_519_808
_MEMBER_SHA256: Final = "cd97fb7cfa34c7e49199e49dbf56a7bbe418101ac51128c8f1c5a33be2d54a39"
_SIGNING_LITERALS: Final = (
    (0xDF73D, 10, "d1a6423c5174c156962a7deeb4e080ee0c15ac1bc02fca3d08d48132f6aba845"),
    (0xBC0A4, 32, "c2fcf5595b84a1f9bcdc4c2e596b49a1d7adb0eb6c9c7912fc00b00a4e9cebc0"),
)
_APP_HOST: Final = "ap-app-gateway.gwmcloud.com"
_H5_HOST: Final = "ap-h5-gateway.gwmcloud.com"
_COMMON_HOST: Final = "ap-app-gateway-common.gwmcloud.com"
_V1: Final = "/app-api/api/v1.0/"
_V2: Final = "/app-api/api/v2.0/"
_NATIVE_ROUTES: Final = {
    ("GET", _APP_HOST, _V1 + "globalapp/vehicle/acquireVehicles"): frozenset(),
    ("GET", _APP_HOST, _V1 + "vehicle/getLastStatus"): frozenset({"vin", "seqNo", "modelId"}),
    ("GET", _APP_HOST, _V1 + "vehicle/findVehicleCapabilityItem"): frozenset({"vin", "userRole"}),
    ("GET", _APP_HOST, _V1 + "vehicle/getRemoteCtrlResultT5"): frozenset({"seqNo"}),
    ("POST", _APP_HOST, _V1 + "vehicle/T5/sendCmd"): frozenset(),
    ("POST", _H5_HOST, _V1 + "userAuth/checkSecurityPassword"): frozenset(),
    ("POST", _H5_HOST, _V1 + "userAuth/refreshToken"): frozenset(),
    ("POST", _COMMON_HOST, _V1 + "appAuth/applyCertificate"): frozenset(),
}
_CURRENT_ROUTES: Final = frozenset(
    ("POST", _H5_HOST, _V2 + path)
    for path in (
        "userAuth/loginWithPassword",
        "userAuth/loginWithVerifyCode",
        "userAuth/getVerifyCode",
        "userAuth/checkVerifyCode",
    )
)
_ERROR_CATEGORIES: Final = frozenset(
    {
        "app_artifact_unreadable",
        "app_artifact_unrecognized",
        "app_signing_material_invalid",
        "app_material_unreadable",
        "app_material_invalid",
        "nonce_invalid",
        "request_invalid",
        "timestamp_invalid",
    }
)
_INVALID_PERCENT: Final = re.compile(r"%(?![0-9A-Fa-f]{2})")
_JAVA_WHITESPACE: Final = re.compile(r"[ \t\n\r\f\v]")
_MAX_BODY_BYTES: Final = 512 * 1024


class ThailandProtocolError(ValueError):
    """Expose a fixed error category, never packaged keys or request data."""

    def __init__(self, category: object) -> None:
        self.category = (
            category
            if isinstance(category, str) and category in _ERROR_CATEGORIES
            else "request_invalid"
        )
        super().__init__(self.category)


@dataclass(frozen=True, slots=True)
class ThailandSigningMaterial:
    """Application credentials, kept out of representations and source."""

    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.app_key, str)
            or re.fullmatch(r"[0-9]{10}", self.app_key) is None
            or not isinstance(self.app_secret, str)
            or re.fullmatch(r"[0-9a-f]{32}", self.app_secret) is None
        ):
            raise ThailandProtocolError("app_signing_material_invalid")


@dataclass(frozen=True, slots=True)
class ThailandAppMaterial:
    """Provisioning material is private configuration, not integration source."""

    signing: ThailandSigningMaterial = field(repr=False)
    bootstrap: ThailandBootstrapMaterial = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.signing) is not ThailandSigningMaterial or type(self.bootstrap) is not ThailandBootstrapMaterial:
            raise ThailandProtocolError("app_material_invalid")


def load_thailand_app_material_from_apk(apk_path: Path) -> ThailandAppMaterial:
    return ThailandAppMaterial(
        signing=load_thailand_signing_material_from_apk(apk_path),
        bootstrap=load_thailand_bootstrap_material_from_apk(apk_path),
    )


def serialize_thailand_app_material(material: ThailandAppMaterial) -> str:
    """Return a private provisioning document; callers must never log it."""

    _validate_provisioned_material(material)
    return json.dumps(
        {
            "version": 1,
            "artifact_sha256": _APP_ARTIFACT_SHA256,
            "app_key": material.signing.app_key,
            "app_secret": material.signing.app_secret,
            "certificate_data": base64.b64encode(material.bootstrap.certificate_data).decode("ascii"),
            "transformed_private_key_data": base64.b64encode(material.bootstrap.transformed_private_key_data).decode("ascii"),
            "ca_bundle": base64.b64encode(material.bootstrap.ca_bundle).decode("ascii"),
        },
        separators=(",", ":"),
    )


def load_thailand_app_material_file(path: Path) -> ThailandAppMaterial:
    """Load a small, pinned private document from the HA configuration directory."""

    try:
        with path.open("rb") as source:
            raw = source.read(64 * 1024 + 1)
    except OSError:
        raise ThailandProtocolError("app_material_unreadable") from None
    if len(raw) > 64 * 1024:
        raise ThailandProtocolError("app_material_invalid")
    try:
        document = json.loads(raw, object_pairs_hook=_unique_material_fields)
        if isinstance(document, dict) and set(document) == {"version", "minor_version", "key", "data"}:
            if (
                type(document["version"]) is not int or document["version"] != 1
                or type(document["minor_version"]) is not int or document["minor_version"] != 1
                or document["key"] != "gwm_thailand_connect.app"
            ):
                raise ThailandProtocolError("app_material_invalid")
            document = document["data"]
        expected = {
            "version", "artifact_sha256", "app_key", "app_secret",
            "certificate_data", "transformed_private_key_data", "ca_bundle",
        }
        if (
            not isinstance(document, dict)
            or set(document) != expected
            or type(document["version"]) is not int
            or document["version"] != 1
            or document["artifact_sha256"] != _APP_ARTIFACT_SHA256
            or any(not isinstance(document[name], str) for name in expected - {"version"})
        ):
            raise ThailandProtocolError("app_material_invalid")
        material = ThailandAppMaterial(
            ThailandSigningMaterial(document["app_key"], document["app_secret"]),
            ThailandBootstrapMaterial(
                base64.b64decode(document["certificate_data"], validate=True),
                base64.b64decode(document["transformed_private_key_data"], validate=True),
                base64.b64decode(document["ca_bundle"], validate=True),
            ),
        )
        _validate_provisioned_material(material)
        return material
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, binascii.Error, ValueError, TypeError):
        raise ThailandProtocolError("app_material_invalid") from None


def _unique_material_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ThailandProtocolError("app_material_invalid")
        result[name] = value
    return result


def _validate_provisioned_material(material: ThailandAppMaterial) -> None:
    if type(material) is not ThailandAppMaterial:
        raise ThailandProtocolError("app_material_invalid")
    for value, (_, _, digest) in zip(
        (material.signing.app_key, material.signing.app_secret), _SIGNING_LITERALS, strict=True
    ):
        if hashlib.sha256(value.encode("ascii")).hexdigest() != digest:
            raise ThailandProtocolError("app_signing_material_invalid")
    create_thailand_bootstrap_ssl_context(material.bootstrap)


def load_thailand_signing_material_from_apk(apk_path: Path) -> ThailandSigningMaterial:
    """Read reviewed string locations, without decompiling or executing the app."""

    if not isinstance(apk_path, Path):
        raise ThailandProtocolError("app_artifact_unreadable")
    try:
        with apk_path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != _APP_ARTIFACT_SHA256:
                raise ThailandProtocolError("app_artifact_unrecognized")
            source.seek(0)
            with zipfile.ZipFile(source) as archive:
                info = archive.getinfo(_MEMBER)
                if info.file_size != _MEMBER_SIZE:
                    raise ThailandProtocolError("app_signing_material_invalid")
                member = archive.read(info)
    except (OSError, KeyError, zipfile.BadZipFile):
        raise ThailandProtocolError("app_artifact_unreadable") from None
    if hashlib.sha256(member).hexdigest() != _MEMBER_SHA256:
        raise ThailandProtocolError("app_signing_material_invalid")
    values = []
    for offset, length, fingerprint in _SIGNING_LITERALS:
        value = member[offset:offset + length]
        if len(value) != length or hashlib.sha256(value).hexdigest() != fingerprint:
            raise ThailandProtocolError("app_signing_material_invalid")
        try:
            values.append(value.decode("ascii"))
        except UnicodeDecodeError:
            raise ThailandProtocolError("app_signing_material_invalid") from None
    return ThailandSigningMaterial(*values)


def sign_thailand_request(
    material: ThailandSigningMaterial,
    method: str,
    url: str,
    body: str | None = None,
    *,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> SignedRequest:
    """Sign only the fixed Thai auth, read and reviewed control routes."""

    if type(material) is not ThailandSigningMaterial:
        raise ThailandProtocolError("app_signing_material_invalid")
    if not isinstance(url, str) or not isinstance(method, str) or len(url) > 4096:
        raise ThailandProtocolError("request_invalid")
    try:
        url.encode("ascii")
        parsed = urlsplit(url)
    except (UnicodeEncodeError, ValueError):
        raise ThailandProtocolError("request_invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.fragment
        or any(ord(character) <= 0x20 or ord(character) >= 0x7F for character in url)
        or _INVALID_PERCENT.search(url)
    ):
        raise ThailandProtocolError("request_invalid")
    route = (method, parsed.netloc, parsed.path)
    current = route in _CURRENT_ROUTES
    if not current and route not in _NATIVE_ROUTES:
        raise ThailandProtocolError("request_invalid")
    allowed_queries = frozenset() if current else _NATIVE_ROUTES[route]
    if parsed.query:
        try:
            query_keys = {
                unquote_plus(token.partition("=")[0], encoding="utf-8", errors="strict")
                for token in parsed.query.split("&")
            }
        except UnicodeDecodeError:
            raise ThailandProtocolError("request_invalid") from None
        if not query_keys or not query_keys.issubset(allowed_queries):
            raise ThailandProtocolError("request_invalid")
    if method == "GET":
        if body is not None:
            raise ThailandProtocolError("request_invalid")
    elif not isinstance(body, str) or not body:
        raise ThailandProtocolError("request_invalid")
    try:
        if body is not None and len(body.encode("utf-8")) > _MAX_BODY_BYTES:
            raise ThailandProtocolError("request_invalid")
    except UnicodeEncodeError:
        raise ThailandProtocolError("request_invalid") from None

    actual_timestamp = str(time.time_ns() // 1_000_000) if timestamp is None else timestamp
    if not isinstance(actual_timestamp, str) or re.fullmatch(r"[0-9]{13}", actual_timestamp) is None:
        raise ThailandProtocolError("timestamp_invalid")
    actual_nonce = nonce
    if actual_nonce is None:
        actual_nonce = secrets.token_hex(16) if current else secrets.token_hex(8).upper()
    nonce_pattern = r"[0-9a-f]{32}" if current else r"[0-9A-F]{16}"
    if not isinstance(actual_nonce, str) or re.fullmatch(nonce_pattern, actual_nonce) is None:
        raise ThailandProtocolError("nonce_invalid")

    try:
        parameters = "json=" + body if body is not None else _native_query_parameters(parsed.query)
    except (UnicodeDecodeError, UnicodeEncodeError):
        raise ThailandProtocolError("request_invalid") from None
    auth = (
        f"bt-auth-appkey:{material.app_key}"
        f"bt-auth-nonce:{actual_nonce}"
        f"bt-auth-timestamp:{actual_timestamp}"
    )
    raw = _JAVA_WHITESPACE.sub("", method + parsed.path + auth + parameters + material.app_secret)
    # Thai v2 auth also requires Java form encoding, not ANZ/Dart URI escaping.
    encoded = quote_plus(raw, safe="*-._", encoding="utf-8", errors="strict").replace("~", "%7E")
    return SignedRequest(
        method,
        url,
        {
            "bt-auth-appkey": material.app_key,
            "bt-auth-nonce": actual_nonce,
            "bt-auth-timestamp": actual_timestamp,
            "bt-auth-sign": hashlib.sha256(encoded.encode("ascii")).hexdigest(),
        },
        body,
    )


def _native_query_parameters(query: str) -> str:
    parameters: dict[str, str] = {}
    for token in query.split("&") if query else ():
        name, separator, value = token.partition("=")
        decoded_name = unquote_plus(name, encoding="utf-8", errors="strict")
        if decoded_name not in parameters:
            parameters[decoded_name] = (
                unquote_plus(value, encoding="utf-8", errors="strict") if separator else "null"
            )
    names = sorted(parameters, key=lambda name: name.encode("utf-16-be"))
    return "".join(f"{name.lower()}={parameters[name]}" for name in names)
