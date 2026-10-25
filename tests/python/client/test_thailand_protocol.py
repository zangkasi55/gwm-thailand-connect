"""Thai signatures must match the recovered native policy, without leaking keys."""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
from pathlib import Path
from urllib.parse import urlencode

import pytest

from custom_components.gwm_thailand_connect.gwm_client import thailand_protocol as protocol
from custom_components.gwm_thailand_connect.gwm_client.thailand_protocol import (
    ThailandProtocolError,
    ThailandSigningMaterial,
    load_thailand_signing_material_from_apk,
    sign_thailand_request,
)

APP = "https://ap-app-gateway.gwmcloud.com/app-api/api/v1.0/"
H5 = "https://ap-h5-gateway.gwmcloud.com/app-api/api/"
LIST_URL = APP + "globalapp/vehicle/acquireVehicles"
STATUS_URL = APP + "vehicle/getLastStatus"
REFRESH_URL = H5 + "v1.0/userAuth/refreshToken"
LOGIN_URL = H5 + "v2.0/userAuth/loginWithPassword"
TIMESTAMP = "1700000000000"
NATIVE_NONCE = "0123456789ABCDEF"
CURRENT_NONCE = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def material() -> ThailandSigningMaterial:
    return ThailandSigningMaterial("1000000000", "0" * 32)


@pytest.fixture
def reviewed_synthetic_apk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    key = b"1000000000"
    secret = b"0" * 32
    member = b"test" + key + secret
    path = tmp_path / "synthetic.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(protocol._MEMBER, member)
    monkeypatch.setattr(protocol, "_APP_ARTIFACT_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(protocol, "_MEMBER_SIZE", len(member))
    monkeypatch.setattr(protocol, "_MEMBER_SHA256", hashlib.sha256(member).hexdigest())
    monkeypatch.setattr(
        protocol,
        "_SIGNING_LITERALS",
        (
            (4, len(key), hashlib.sha256(key).hexdigest()),
            (4 + len(key), len(secret), hashlib.sha256(secret).hexdigest()),
        ),
    )
    return path


@pytest.mark.parametrize(
    ("url", "body", "expected"),
    [
        (LIST_URL, None, "4dbba19aa3a32a85742dcce9d9a26a353e5e53c9edca0d9f40d156138bb03679"),
        (
            STATUS_URL + "?vin=TESTVIN&seqNo=&modelId=M1",
            None,
            "977e1b40d8a418584e321c035be151db9b5027f3a0f24d7015f82aef985eec2c",
        ),
        (
            STATUS_URL + "?" + urlencode({"vin": "a+b ~*()!'"}),
            None,
            "14324ab2d1f785c0e9c01dac9a550b843148b37707ead37047a41cd58db32505",
        ),
        (
            STATUS_URL + "?" + urlencode({"vin": "\u0e44\u0e17\u0e22\u00a0 X"}),
            None,
            "cd77cc410a7480465035b3d800cd2544ea0235f5c72afd957a7421f71504f965",
        ),
        (
            REFRESH_URL,
            '{"accessToken":"test +~*()!\'","refreshToken":"synthetic","deviceId":"device"}',
            "c629329a4e246fae57389d288332bbd10f8d5c916b1bf052c52c2e3ac1fd62e5",
        ),
        (
            REFRESH_URL,
            '{ \n"accessToken": "a b\\tc", "refreshToken":"x\u0085\u00a0 y" }',
            "bc653dfa99bed2c853d977213d6698497e330a4182e6b1033ca75b4277c6ba9e",
        ),
    ],
)
def test_native_signatures_match_independent_jdk_vectors(
    material: ThailandSigningMaterial,
    url: str,
    body: str | None,
    expected: str,
) -> None:
    signed = sign_thailand_request(
        material, "GET" if body is None else "POST", url, body,
        timestamp=TIMESTAMP, nonce=NATIVE_NONCE,
    )
    assert signed.headers["bt-auth-sign"] == expected
    assert signed.url == url
    assert signed.body == body
    assert signed.headers["bt-auth-appkey"] == material.app_key


def test_native_query_uses_first_value_null_distinction_and_utf16_sort() -> None:
    assert protocol._native_query_parameters("B=2&a=first&a=second&flag&empty=") == (
        "b=2a=firstempty=flag=null"
    )
    assert protocol._native_query_parameters("%EE%80%80=x&%F0%90%80%80=y") == "\U00010000=y\ue000=x"
    assert protocol._native_query_parameters("vin=a%2Bb+c") == "vin=a+b c"


def test_current_auth_uses_thai_native_encoding_without_altering_password(
    material: ThailandSigningMaterial,
) -> None:
    body = '{"password":" a +~*()!\'\u0e44\u0e17\u0e22\\t "}'
    signed = sign_thailand_request(
        material, "POST", LOGIN_URL, body, timestamp=TIMESTAMP, nonce=CURRENT_NONCE,
    )
    assert signed.headers["bt-auth-sign"] == "5c80fa9abf69777f2d0a579dc78f2fb3f93f232289bfa7c62759deb6ed9266ca"
    assert signed.body == body


def test_v2_reserved_character_probe_matches_independent_jdk_vector(
    material: ThailandSigningMaterial,
) -> None:
    body = '{"interopProbe":" A +~*()!\'\u0e44\u0e17\u0e22\\t "}'
    signed = sign_thailand_request(
        material, "POST", LOGIN_URL, body, timestamp=TIMESTAMP, nonce=CURRENT_NONCE,
    )
    assert signed.headers["bt-auth-sign"] == "64835eb5d1f9e22cf05f5845252d7152dfe7d937fa54ad41c64bfd66e85743ec"
    assert signed.body == body


@pytest.mark.parametrize("path", ["loginWithPassword", "loginWithVerifyCode", "getVerifyCode", "checkVerifyCode"])
def test_only_current_password_and_mfa_routes_are_available(
    material: ThailandSigningMaterial,
    path: str,
) -> None:
    request = sign_thailand_request(
        material, "POST", H5 + "v2.0/userAuth/" + path, "{}",
        timestamp=TIMESTAMP, nonce=CURRENT_NONCE,
    )
    assert request.method == "POST"


@pytest.mark.parametrize(
    ("url", "method", "body"),
    [
        ("https://br-app-gateway.gwmcloud.com/app-api/api/v1.0/globalapp/vehicle/acquireVehicles", "GET", None),
        (LIST_URL.replace("https://", "http://"), "GET", None),
        (LIST_URL.replace("ap-app-gateway", "ap-app-gateway-stage"), "GET", None),
        (LIST_URL.replace(".com/", ".com:443/"), "GET", None),
        (LIST_URL.replace("https://", "https://someone@"), "GET", None),
        (LIST_URL + "#fragment", "GET", None),
        (LIST_URL + "?wake=1", "GET", None),
        (STATUS_URL + "?vin=%FF", "GET", None),
        (STATUS_URL + "?vin=%GG", "GET", None),
        (LIST_URL + "\r\nX-Leak: private", "GET", None),
        (APP + "vehicle/sendCmd", "POST", "{}"),
        (APP + "vehicle/findStatus", "GET", None),
        (H5 + "v1.0/userAuth/loginAccount", "POST", "{}"),
        (LOGIN_URL + "?redirect=elsewhere", "POST", "{}"),
        (LIST_URL, "get", None),
        (LIST_URL, "DELETE", None),
        (LIST_URL, "GET", "{}"),
        (LOGIN_URL, "POST", None),
        (LOGIN_URL, "POST", ""),
        (LOGIN_URL, "POST", "\ud800"),
        (LOGIN_URL, "POST", "x" * (512 * 1024 + 1)),
    ],
    ids=[
        "wrong-region", "plain-http", "stage-host", "explicit-port", "userinfo",
        "fragment", "wake-query", "invalid-query-utf8", "invalid-percent", "header-injection",
        "command", "wake-route", "legacy-password-login", "auth-query",
        "lowercase-method", "delete-method", "get-body", "missing-post-body", "empty-post-body",
        "invalid-body-unicode", "oversized-body",
    ],
)
def test_unreviewed_routes_and_invalid_wire_inputs_fail_without_echoing_inputs(
    material: ThailandSigningMaterial,
    url: str,
    method: str,
    body: str | None,
) -> None:
    with pytest.raises(ThailandProtocolError, match="^request_invalid$") as failure:
        sign_thailand_request(material, method, url, body)
    assert material.app_secret not in repr(failure.value)
    assert "someone" not in repr(failure.value)


@pytest.mark.parametrize("timestamp", ["", "abc", "-1700000000000", "170000000000", "17000000000000"])
def test_bad_timestamps_are_explicit_failures(
    material: ThailandSigningMaterial,
    timestamp: str,
) -> None:
    with pytest.raises(ThailandProtocolError, match="^timestamp_invalid$"):
        sign_thailand_request(material, "GET", LIST_URL, timestamp=timestamp, nonce=NATIVE_NONCE)


@pytest.mark.parametrize("nonce", ["", "0123456789abcdef", "X" * 16, NATIVE_NONCE * 2])
def test_bad_native_nonce_shapes_are_rejected(
    material: ThailandSigningMaterial,
    nonce: str,
) -> None:
    with pytest.raises(ThailandProtocolError, match="^nonce_invalid$"):
        sign_thailand_request(material, "GET", LIST_URL, timestamp=TIMESTAMP, nonce=nonce)


def test_nonce_shapes_match_each_protocol_and_representations_hide_material(
    material: ThailandSigningMaterial,
) -> None:
    native = sign_thailand_request(material, "GET", LIST_URL)
    current = sign_thailand_request(material, "POST", LOGIN_URL, '{"password":"private-value"}')
    assert re.fullmatch(r"[0-9A-F]{16}", native.headers["bt-auth-nonce"])
    assert re.fullmatch(r"[0-9a-f]{32}", current.headers["bt-auth-nonce"])
    assert re.fullmatch(r"[0-9]{13}", native.headers["bt-auth-timestamp"])
    assert repr(material) == "ThailandSigningMaterial()"
    assert material.app_key not in repr(native)
    assert material.app_secret not in repr(native)
    assert "private-value" not in repr(current)


def test_reviewed_synthetic_artifact_loads_only_pinned_material(reviewed_synthetic_apk: Path) -> None:
    material = load_thailand_signing_material_from_apk(reviewed_synthetic_apk)
    assert material.app_key == "1000000000"
    assert material.app_secret == "0" * 32
    assert repr(material) == "ThailandSigningMaterial()"


def test_unknown_artifact_is_rejected_before_zip_decoding(tmp_path: Path) -> None:
    path = tmp_path / "not-the-reviewed-app.apk"
    path.write_bytes(b"not an apk")
    with pytest.raises(ThailandProtocolError, match="^app_artifact_unrecognized$"):
        load_thailand_signing_material_from_apk(path)


def test_unreadable_artifact_does_not_expose_its_path(tmp_path: Path) -> None:
    with pytest.raises(ThailandProtocolError, match="^app_artifact_unreadable$") as failure:
        load_thailand_signing_material_from_apk(tmp_path / "private-account-name.apk")
    assert "private-account-name" not in repr(failure.value)


@pytest.mark.parametrize("pin", ["_MEMBER_SIZE", "_MEMBER_SHA256", "_SIGNING_LITERALS"])
def test_wrong_member_or_literal_pin_is_rejected(
    reviewed_synthetic_apk: Path,
    monkeypatch: pytest.MonkeyPatch,
    pin: str,
) -> None:
    value = {
        "_MEMBER_SIZE": 1,
        "_MEMBER_SHA256": "0" * 64,
        "_SIGNING_LITERALS": ((4, 10, "0" * 64), (14, 32, "0" * 64)),
    }[pin]
    monkeypatch.setattr(protocol, pin, value)
    with pytest.raises(ThailandProtocolError, match="^app_signing_material_invalid$"):
        load_thailand_signing_material_from_apk(reviewed_synthetic_apk)


@pytest.mark.parametrize(
    ("key", "secret"),
    [("", "0" * 32), ("1000000000", "not-a-secret"), ("a" * 10, "0" * 32), ("1" * 10, "A" * 32)],
)
def test_material_validation_never_echoes_values(key: str, secret: str) -> None:
    with pytest.raises(ThailandProtocolError, match="^app_signing_material_invalid$") as failure:
        ThailandSigningMaterial(key, secret)
    assert "not-a-secret" not in repr(failure.value)


def test_original_store_artifact_provisions_matching_native_and_flutter_material() -> None:
    path = os.environ.get("GWM_THAILAND_TEST_APK")
    if path is None:
        pytest.skip("Original reviewed app artifact is not present")
    material = load_thailand_signing_material_from_apk(Path(path))
    assert hashlib.sha256(material.app_key.encode()).hexdigest() == protocol._SIGNING_LITERALS[0][2]
    assert hashlib.sha256(material.app_secret.encode()).hexdigest() == protocol._SIGNING_LITERALS[1][2]
    assert repr(material) == "ThailandSigningMaterial()"
