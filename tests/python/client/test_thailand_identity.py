"""Thai TLS provisioning must not expose keys or weaken verification."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import ssl
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from custom_components.gwm_thailand_connect.gwm_client import thailand_client, thailand_identity, thailand_protocol
from custom_components.gwm_thailand_connect.gwm_client._protocol import _Deadline, _TransportRequest, _TransportResponse
from custom_components.gwm_thailand_connect.gwm_client.errors import (
    GwmApiError,
    GwmAuthenticationError,
    GwmClosedError,
    GwmConfigurationError,
    GwmDeadlineExceededError,
)
from custom_components.gwm_thailand_connect.gwm_client.models import VehicleIdentifier
from custom_components.gwm_thailand_connect.gwm_client.thailand_client import (
    ThailandAuthenticated,
    ThailandAuthenticationMethod,
    ThailandAuthState,
    ThailandClient,
    ThailandCredentials,
    ThailandInitializationRequired,
    ThailandSessionConsentRequired,
    ThailandVerificationRequired,
)
from custom_components.gwm_thailand_connect.gwm_client.thailand_identity import (
    ThailandBootstrapMaterial,
    ThailandIdentityError,
    complete_thailand_certificate_request,
    create_thailand_bootstrap_ssl_context,
    create_thailand_certificate_request,
    create_thailand_issued_ssl_context,
    load_thailand_bootstrap_material_from_apk,
)

NOW = datetime(2026, 10, 22, tzinfo=UTC)


@dataclass(frozen=True)
class SyntheticIdentity:
    material: ThailandBootstrapMaterial = field(repr=False)
    key: rsa.RSAPrivateKey = field(repr=False)
    certificate: x509.Certificate = field(repr=False)
    ca_fingerprints: frozenset[str]
    issuer_key: rsa.RSAPrivateKey = field(repr=False)


@pytest.fixture(scope="module")
def synthetic_identity() -> SyntheticIdentity:
    issuer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    roots = []
    for index in range(4):
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Synthetic GWM CA {index}")])
        roots.append(
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(issuer_key.public_key())
            .serial_number(index + 1)
            .not_valid_before(NOW - timedelta(days=1))
            .not_valid_after(NOW + timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(issuer_key, hashes.SHA256())
        )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic app client")]))
        .issuer_name(roots[0].subject)
        .public_key(client_key.public_key())
        .serial_number(100)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .sign(issuer_key, hashes.SHA256())
    )
    material = ThailandBootstrapMaterial(
        certificate_data=certificate.public_bytes(serialization.Encoding.DER),
        transformed_private_key_data=b"synthetic-key-source-not-an-oem-key",
        ca_bundle=b"".join(root.public_bytes(serialization.Encoding.PEM) for root in roots),
    )
    return SyntheticIdentity(
        material,
        client_key,
        certificate,
        frozenset(
            hashlib.sha256(root.public_bytes(serialization.Encoding.DER)).hexdigest()
            for root in roots
        ),
        issuer_key,
    )


def activate_synthetic_identity(
    monkeypatch: pytest.MonkeyPatch,
    identity: SyntheticIdentity,
) -> None:
    monkeypatch.setattr(
        thailand_identity,
        "_BOOTSTRAP_CERTIFICATE_SHA256",
        hashlib.sha256(identity.material.certificate_data).hexdigest(),
    )
    monkeypatch.setattr(thailand_identity, "_CA_DER_SHA256", identity.ca_fingerprints)
    monkeypatch.setattr(
        thailand_identity,
        "recover_transformed_private_key",
        lambda certificate_data, key_data: identity.key,
    )


def test_material_representation_does_not_include_private_bytes(
    synthetic_identity: SyntheticIdentity,
) -> None:
    representation = repr(synthetic_identity.material)
    assert "synthetic-key-source" not in representation
    assert "BEGIN CERTIFICATE" not in representation
    assert representation == "ThailandBootstrapMaterial()"


def test_unknown_artifact_is_rejected_before_zip_parsing(tmp_path: Path) -> None:
    artifact = tmp_path / "not-the-reviewed-app.apk"
    artifact.write_bytes(b"not a reviewed app artifact")
    with pytest.raises(ThailandIdentityError, match="^app_artifact_unrecognized$"):
        load_thailand_bootstrap_material_from_apk(artifact)


def test_wrong_region_certificate_is_rejected(synthetic_identity: SyntheticIdentity) -> None:
    with pytest.raises(ThailandIdentityError, match="^bootstrap_identity_unrecognized$"):
        create_thailand_bootstrap_ssl_context(synthetic_identity.material, now=NOW)


def test_context_preserves_normal_verification_and_encrypts_temporary_key(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    tmp_path: Path,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    original = ssl.SSLContext.load_cert_chain
    paths: list[Path] = []
    baseline = ssl.create_default_context()

    def inspect_encrypted_key(
        context: ssl.SSLContext,
        certfile: Path,
        keyfile: Path,
        password: bytes,
    ) -> None:
        paths.append(keyfile)
        assert certfile.read_bytes().count(b"-----BEGIN CERTIFICATE-----") == 1
        assert keyfile.read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
        assert len(password) >= 32
        original(context, certfile=certfile, keyfile=keyfile, password=password)

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", inspect_encrypted_key)
    context = create_thailand_bootstrap_ssl_context(
        synthetic_identity.material,
        now=NOW,
        temporary_directory=tmp_path,
    )
    after = ssl.create_default_context()
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.security_level >= 2
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert after.security_level == baseline.security_level
    assert after.minimum_version == baseline.minimum_version
    assert paths and all(not path.exists() for path in paths)
    assert list(tmp_path.iterdir()) == []


def test_identity_roots_do_not_replace_or_expand_public_gateway_server_trust(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    baseline = {
        hashlib.sha256(certificate).hexdigest()
        for certificate in ssl.create_default_context().get_ca_certs(binary_form=True)
    }
    context = create_thailand_bootstrap_ssl_context(synthetic_identity.material, now=NOW)
    actual = {
        hashlib.sha256(certificate).hexdigest()
        for certificate in context.get_ca_certs(binary_form=True)
    }
    assert baseline.issubset(actual)
    assert synthetic_identity.ca_fingerprints.isdisjoint(actual)
    assert context.security_level >= 2
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_tls_failure_is_explicit_and_removes_temporary_material(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    tmp_path: Path,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)

    def reject_context(*args: object, **kwargs: object) -> None:
        raise ssl.SSLError("synthetic TLS rejection")

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", reject_context)
    with pytest.raises(ThailandIdentityError, match="^tls_context_invalid$"):
        create_thailand_bootstrap_ssl_context(
            synthetic_identity.material,
            now=NOW,
            temporary_directory=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("now", "category"),
    [
        (NOW - timedelta(days=2), "identity_not_yet_valid"),
        (NOW + timedelta(days=31), "identity_expired"),
        (NOW.replace(tzinfo=None), "time_invalid"),
    ],
)
def test_certificate_time_checks_are_not_bypassed(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    now: datetime,
    category: str,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    with pytest.raises(ThailandIdentityError, match=f"^{category}$"):
        create_thailand_bootstrap_ssl_context(synthetic_identity.material, now=now)


def test_duplicate_or_substituted_ca_bundle_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    root = synthetic_identity.material.ca_bundle.split(b"-----END CERTIFICATE-----", 1)[0]
    duplicated = (root + b"-----END CERTIFICATE-----\n") * 4
    material = ThailandBootstrapMaterial(
        synthetic_identity.material.certificate_data,
        synthetic_identity.material.transformed_private_key_data,
        duplicated,
    )
    with pytest.raises(ThailandIdentityError, match="^ca_bundle_invalid$"):
        create_thailand_bootstrap_ssl_context(material, now=NOW)


def test_material_type_errors_do_not_echo_input() -> None:
    marker = b"private-marker"
    with pytest.raises(ThailandIdentityError) as captured:
        ThailandBootstrapMaterial(b"", marker, b"invalid")
    assert marker.decode() not in str(captured.value)


def test_real_store_artifact_when_explicitly_provided(tmp_path: Path) -> None:
    configured = os.environ.get("GWM_THAILAND_TEST_APK")
    if not configured:
        pytest.skip("No owner-supplied reviewed app artifact was selected")
    material = load_thailand_bootstrap_material_from_apk(Path(configured))
    context = create_thailand_bootstrap_ssl_context(material, temporary_directory=tmp_path)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.security_level >= 2
    assert list(tmp_path.iterdir()) == []


DEVICE = "0123456789abcdef0123456789abcdef"
GW_ID = "synthetic-gw-id"


def encoded_issued_certificate(
    csr: x509.CertificateSigningRequest,
    identity: SyntheticIdentity,
    *,
    signing_key: rsa.RSAPrivateKey | None = None,
    expires: datetime = NOW + timedelta(days=30),
    include_extensions: bool = True,
    subject: x509.Name | None = None,
) -> str:
    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject if subject is None else subject)
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic GWM CA 0")]))
        .public_key(csr.public_key())
        .serial_number(1001)
        .not_valid_before(NOW - timedelta(minutes=1))
        .not_valid_after(expires)
    )
    if include_extensions:
        builder = builder.add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
        )
    certificate = builder.sign(
        identity.issuer_key if signing_key is None else signing_key, hashes.SHA256()
    )
    return base64.b64encode(certificate.public_bytes(serialization.Encoding.DER)).decode()


def test_enrollment_uses_bound_strong_csr_and_only_encrypted_private_material(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    tmp_path: Path,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    request = create_thailand_certificate_request(GW_ID, DEVICE, now=NOW)
    csr = x509.load_der_x509_csr(base64.b64decode(request.csr))
    assert csr.is_signature_valid
    public = csr.public_key()
    assert isinstance(public, rsa.RSAPublicKey)
    assert public.key_size >= 2048
    segment = hashlib.sha256(("com.gwm.tha" + GW_ID + DEVICE).encode()).hexdigest()[16:48].upper()
    assert segment in request.common_name
    assert csr.subject.get_attributes_for_oid(NameOID.STATE_OR_PROVINCE_NAME)[0].value == "Operational"
    assert request.encrypted_private_key_data.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    issued = complete_thailand_certificate_request(
        request, encoded_issued_certificate(csr, synthetic_identity),
        material=synthetic_identity.material, gw_id=GW_ID, device_id=DEVICE, now=NOW,
    )
    context = create_thailand_issued_ssl_context(
        issued, material=synthetic_identity.material, gw_id=GW_ID, device_id=DEVICE,
        now=NOW, temporary_directory=tmp_path,
    )
    assert context.security_level >= 2
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert list(tmp_path.iterdir()) == []
    assert repr(request) == "ThailandCertificateRequest()"
    assert repr(issued) == "ThailandIssuedIdentity()"


def test_enrollment_accepts_issuer_assigned_name_only_with_bound_csr_key(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    tmp_path: Path,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    request = create_thailand_certificate_request(GW_ID, DEVICE, now=NOW)
    csr = x509.load_der_x509_csr(base64.b64decode(request.csr))
    assigned_name = "OEM-" + "a" * 36
    encoded = encoded_issued_certificate(
        csr, synthetic_identity,
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, assigned_name)]),
    )
    issued = complete_thailand_certificate_request(
        request, encoded, material=synthetic_identity.material,
        gw_id=GW_ID, device_id=DEVICE, now=NOW,
    )
    assert issued.common_name == assigned_name
    assert issued.binding == request.binding
    assert issued.encrypted_private_key_data == request.encrypted_private_key_data
    context = create_thailand_issued_ssl_context(
        issued, material=synthetic_identity.material,
        gw_id=GW_ID, device_id=DEVICE, now=NOW, temporary_directory=tmp_path,
    )
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.security_level >= 2
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ThailandIdentityError, match="identity_binding_mismatch"):
        create_thailand_issued_ssl_context(
            replace(issued, common_name="different-stored-name"),
            material=synthetic_identity.material,
            gw_id=GW_ID, device_id=DEVICE, now=NOW, temporary_directory=tmp_path,
        )


@pytest.mark.parametrize("mismatch", ["csr_key", "csr_subject", "csr_signature", "malformed_csr"])
def test_enrollment_checks_saved_csr_before_accepting_issuer_assigned_subject(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    mismatch: str,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    request = create_thailand_certificate_request(GW_ID, DEVICE, now=NOW)
    csr = x509.load_der_x509_csr(base64.b64decode(request.csr))
    encoded = encoded_issued_certificate(
        csr, synthetic_identity,
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OEM-" + "a" * 36)]),
    )
    if mismatch == "csr_key":
        request = replace(
            request, csr=create_thailand_certificate_request(GW_ID, DEVICE, now=NOW).csr,
        )
    elif mismatch == "csr_subject":
        request = replace(request, common_name="LGWdifferent-request")
    elif mismatch == "csr_signature":
        damaged = bytearray(base64.b64decode(request.csr))
        damaged[-1] ^= 1
        request = replace(request, csr=base64.b64encode(damaged).decode("ascii"))
    else:
        request = replace(request, csr="not-base64")
    with pytest.raises(ThailandIdentityError):
        complete_thailand_certificate_request(
            request, encoded, material=synthetic_identity.material,
            gw_id=GW_ID, device_id=DEVICE, now=NOW,
        )


@pytest.mark.parametrize("common_name_count", [0, 2])
def test_enrollment_rejects_missing_or_ambiguous_issuer_assigned_name(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    common_name_count: int,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    request = create_thailand_certificate_request(GW_ID, DEVICE, now=NOW)
    csr = x509.load_der_x509_csr(base64.b64decode(request.csr))
    encoded = encoded_issued_certificate(
        csr, synthetic_identity,
        subject=x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, f"OEM-{index}")
            for index in range(common_name_count)
        ]),
    )
    with pytest.raises(ThailandIdentityError, match="identity_binding_mismatch"):
        complete_thailand_certificate_request(
            request, encoded, material=synthetic_identity.material,
            gw_id=GW_ID, device_id=DEVICE, now=NOW,
        )


@pytest.mark.parametrize("mismatch", ["account", "device", "issuer", "key"])
@pytest.mark.parametrize("issuer_assigned_name", [False, True])
def test_enrollment_rejects_mismatched_account_device_issuer_and_key(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    mismatch: str,
    issuer_assigned_name: bool,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    request = create_thailand_certificate_request(GW_ID, DEVICE, now=NOW)
    csr = x509.load_der_x509_csr(base64.b64decode(request.csr))
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if mismatch == "key":
        other_request = create_thailand_certificate_request(GW_ID, DEVICE, now=NOW)
        csr = x509.load_der_x509_csr(base64.b64decode(other_request.csr))
    encoded = encoded_issued_certificate(
        csr, synthetic_identity, signing_key=other_key if mismatch == "issuer" else None,
        subject=(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OEM-" + "a" * 36)])
            if issuer_assigned_name else None
        ),
    )
    with pytest.raises(ThailandIdentityError):
        complete_thailand_certificate_request(
            request, encoded, material=synthetic_identity.material,
            gw_id="different-account" if mismatch == "account" else GW_ID,
            device_id="f" * 32 if mismatch == "device" else DEVICE, now=NOW,
        )


def test_reviewed_oem_public_keys_do_not_require_decoding_legacy_subject_names() -> None:
    configured = os.environ.get("GWM_THAILAND_TEST_APK")
    if not configured:
        pytest.skip("Reviewed app artifact was not provided")
    material = load_thailand_bootstrap_material_from_apk(Path(configured))
    keys = []
    for block in thailand_identity._PEM_CERTIFICATE.finditer(material.ca_bundle.decode("ascii")):
        der = base64.b64decode("".join(block.group("payload").split()), validate=True)
        keys.append(thailand_identity._certificate_public_key_without_names(der))
    assert len(keys) == 4
    assert all(isinstance(key, rsa.RSAPublicKey) and key.key_size >= 2048 for key in keys)


@pytest.fixture
def app_material(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
) -> thailand_protocol.ThailandAppMaterial:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    monkeypatch.setattr(thailand_client, "_utc_now", lambda: NOW)
    signing = thailand_protocol.ThailandSigningMaterial("1000000000", "0" * 32)
    monkeypatch.setattr(
        thailand_protocol, "_SIGNING_LITERALS",
        tuple((0, len(value), hashlib.sha256(value.encode()).hexdigest())
              for value in (signing.app_key, signing.app_secret)),
    )
    original = create_thailand_bootstrap_ssl_context
    monkeypatch.setattr(
        thailand_protocol, "create_thailand_bootstrap_ssl_context",
        lambda material: original(material, now=NOW),
    )
    return thailand_protocol.ThailandAppMaterial(signing, synthetic_identity.material)


def test_private_provisioning_round_trip_is_pinned_and_not_a_source_resource(
    app_material: thailand_protocol.ThailandAppMaterial,
    tmp_path: Path,
) -> None:
    document = thailand_protocol.serialize_thailand_app_material(app_material)
    path = tmp_path / "private-material"
    path.write_text(document, encoding="utf-8")
    restored = thailand_protocol.load_thailand_app_material_file(path)
    assert restored == app_material
    assert repr(restored) == "ThailandAppMaterial()"
    parsed = json.loads(document)
    parsed["app_secret"] = "1" * 32
    path.write_text(json.dumps(parsed), encoding="utf-8")
    with pytest.raises(thailand_protocol.ThailandProtocolError):
        thailand_protocol.load_thailand_app_material_file(path)


class ScriptedThaiTransport:
    def __init__(self, identity: SyntheticIdentity, responses: list[dict[str, object]]) -> None:
        self.identity = identity
        self.responses = list(responses)
        self.requests: list[_TransportRequest] = []
        self.closed = False
        self.wait_forever = False
        self.enrollment_code = "000000"
        self.bad_certificate = False
        self.include_certificate_extensions = True
        self.certificate_data_missing = False

    async def execute(
        self, request: _TransportRequest, *, deadline: _Deadline,
        connect_timeout: float, read_timeout: float,
    ) -> _TransportResponse:
        self.requests.append(request)
        assert deadline.remaining(asyncio.get_running_loop().time()) > 0
        assert connect_timeout > 0 and read_timeout > 0
        if self.wait_forever:
            await asyncio.Event().wait()
        if request.operation == "enroll_certificate":
            assert request.body is not None
            body = json.loads(request.body)
            assert body["phone"] == GW_ID
            csr = x509.load_der_x509_csr(base64.b64decode(body["csr"]))
            response: dict[str, object] = {
                "code": self.enrollment_code,
                "data": {"encoded": "not a certificate" if self.bad_certificate
                         else encoded_issued_certificate(
                             csr, self.identity, include_extensions=self.include_certificate_extensions,
                         )},
            }
            if self.certificate_data_missing:
                response.pop("data")
        else:
            assert self.responses, "An unexpected extra HTTP request was made"
            response = self.responses.pop(0)
        return _TransportResponse(
            200, {"content-type": "application/json"}, json.dumps(response).encode(),
        )

    async def aclose(self) -> None:
        self.closed = True


def login_response(*, refresh: str | None = "synthetic-refresh") -> dict[str, object]:
    return {
        "code": "000000",
        "data": {"accessToken": "synthetic-access", "refreshToken": refresh, "gwId": GW_ID},
    }


@pytest.mark.asyncio
async def test_thailand_client_requires_consent_before_any_account_bound_request(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    credentials = ThailandCredentials("owner@example.invalid", "a password", DEVICE)
    transport = ScriptedThaiTransport(synthetic_identity, [])
    async with ThailandClient(app_material, transport=transport) as client:
        result = await client.authenticate(credentials)
        assert isinstance(result, ThailandSessionConsentRequired)
        assert not transport.requests
        assert not client.authenticated
        with pytest.raises(GwmAuthenticationError):
            await client.authenticate(credentials, allow_password_login=False)
        with pytest.raises(GwmAuthenticationError):
            await client.acquire_vehicles()
    assert not transport.closed


@pytest.mark.asyncio
async def test_thailand_client_authenticates_enrolls_and_reads_only_discovered_cached_status(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    password = " P +~*()!'\u0e44\u0e17\u0e22 "
    credentials = ThailandCredentials("owner@example.invalid", password, DEVICE)
    transport = ScriptedThaiTransport(
        synthetic_identity,
        [
            login_response(),
            {"code": "000000", "data": [{"vin": "SYNTHETICVIN", "modelName": "H6 PHEV"}]},
            {"code": "000000", "data": {"items": [{"code": "2013021", "value": 55}]}},
        ],
    )
    async with ThailandClient(app_material, transport=transport) as client:
        result = await client.authenticate(credentials, allow_session_reclaim=True)
        assert isinstance(result, ThailandAuthenticated)
        assert client.authenticated
        vehicles = await client.acquire_vehicles()
        status = await client.get_last_status(vehicles[0].identifier)
        assert status.items[0].value == 55
        with pytest.raises(GwmConfigurationError):
            await client.get_last_status(VehicleIdentifier("ANOTHER-VEHICLE"))
        assert not any(
            hasattr(client, name) for name in (
                "send_lock_command", "send_climate_command", "send_command", "set_charging_plan",
            )
        )
    assert [request.operation for request in transport.requests] == [
        "login", "enroll_certificate", "acquire_vehicles", "get_last_status",
    ]
    assert json.loads(transport.requests[0].body)["password"] == password
    assert json.loads(transport.requests[0].body)["countryCode"] == "+66"
    read = transport.requests[-1]
    assert parse_qs(urlsplit(read.url).query, keep_blank_values=True) == {
        "vin": ["SYNTHETICVIN"], "seqNo": [""], "modelId": [""],
    }
    assert read.headers["vin"] == "SYNTHETICVIN"
    assert all(request.ssl_context.security_level >= 2 for request in transport.requests)
    assert password not in repr(result)
    assert "synthetic-access" not in repr(result.state)
    with pytest.raises(GwmClosedError):
        await client.acquire_vehicles()


@pytest.mark.asyncio
async def test_thailand_client_mfa_wrong_code_and_resend_throttle(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    credentials = ThailandCredentials("owner@example.invalid", "password", DEVICE)
    transport = ScriptedThaiTransport(
        synthetic_identity,
        [
            {"code": "308103"}, {"code": "000000"},
            {"code": "308103"},
            {"code": "308011"},
            {"code": "000000"}, login_response(),
        ],
    )
    async with ThailandClient(app_material, transport=transport) as client:
        challenge = await client.authenticate(credentials, allow_session_reclaim=True)
        assert isinstance(challenge, ThailandVerificationRequired) and challenge.code_requested
        throttled = await client.authenticate(
            credentials, state=challenge.state, allow_session_reclaim=True,
        )
        assert isinstance(throttled, ThailandVerificationRequired)
        assert not throttled.code_requested
        wrong = await client.authenticate(
            credentials, state=challenge.state, verification_code="111111",
            allow_session_reclaim=True,
        )
        assert isinstance(wrong, ThailandVerificationRequired) and wrong.code_rejected
        result = await client.authenticate(
            credentials, state=wrong.state, verification_code="222222",
            allow_session_reclaim=True,
        )
        assert isinstance(result, ThailandAuthenticated)
        assert sum(request.operation == "request_verification" for request in transport.requests) == 1
        assert "222222" not in repr(result.state)


@pytest.mark.asyncio
async def test_thailand_client_resumes_without_password_login_and_refreshes_natively(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    credentials = ThailandCredentials("owner@example.invalid", "password", DEVICE)
    first = ScriptedThaiTransport(synthetic_identity, [login_response()])
    async with ThailandClient(app_material, transport=first) as client:
        authenticated = await client.authenticate(credentials, allow_session_reclaim=True)
        assert isinstance(authenticated, ThailandAuthenticated)
    resumed_transport = ScriptedThaiTransport(
        synthetic_identity,
        [{"code": "000000", "data": {"accessToken": "new-access", "refreshToken": "new-refresh"}}],
    )
    async with ThailandClient(app_material, transport=resumed_transport) as client:
        resumed = await client.authenticate(
            credentials, state=authenticated.state, allow_password_login=False,
        )
        assert isinstance(resumed, ThailandAuthenticated)
        assert not resumed_transport.requests
        renewed = await client.refresh_thailand_session(credentials, resumed.state)
        assert renewed.state.access_token == "new-access"
        assert renewed.state.refresh_token == "new-refresh"
        assert resumed_transport.requests[0].headers["accessToken"] == ""
        assert json.loads(resumed_transport.requests[0].body)["deviceId"] == DEVICE
        cached = await client.refresh_thailand_session(credentials, resumed.state)
        assert cached.state.access_token == "new-access"
        assert len(resumed_transport.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["000000", "310022"])
async def test_thailand_client_accepts_only_verified_matching_enrollment_certificate(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
    code: str,
) -> None:
    credentials = ThailandCredentials("owner@example.invalid", "password", DEVICE)
    transport = ScriptedThaiTransport(synthetic_identity, [login_response()])
    transport.enrollment_code = code
    transport.bad_certificate = True
    async with ThailandClient(app_material, transport=transport) as client:
        result = await client.authenticate(credentials, allow_session_reclaim=True)
        assert isinstance(result, ThailandInitializationRequired)
        assert result.reason == "issued_identity_invalid"
        assert result.state.access_token == "synthetic-access"
        assert result.state.certificate_request is not None
        assert result.state.pending_certificate == "not a certificate"
        assert not client.authenticated
        resumed = await client.authenticate(
            credentials, state=result.state, allow_password_login=False,
        )
        assert isinstance(resumed, ThailandInitializationRequired)
        assert len(transport.requests) == 2


@pytest.mark.parametrize("wrapped", [False, True])
def test_valid_x509_leaf_without_optional_usage_extensions_or_with_wrapped_base64_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    wrapped: bool,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    request = create_thailand_certificate_request(GW_ID, DEVICE, now=NOW)
    csr = x509.load_der_x509_csr(base64.b64decode(request.csr))
    encoded = encoded_issued_certificate(csr, synthetic_identity, include_extensions=False)
    if wrapped:
        encoded = "\r\n".join(encoded[index:index + 64] for index in range(0, len(encoded), 64)) + "\n"
    identity = complete_thailand_certificate_request(
        request, encoded, material=synthetic_identity.material,
        gw_id=GW_ID, device_id=DEVICE, now=NOW,
    )
    context = create_thailand_issued_ssl_context(
        identity, material=synthetic_identity.material, gw_id=GW_ID, device_id=DEVICE, now=NOW,
    )
    assert context.security_level >= 2
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize(
    ("extension", "reason"),
    [
        (x509.BasicConstraints(ca=True, path_length=None), "identity_basic_constraints_invalid"),
        (
            x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            "identity_key_usage_invalid",
        ),
        (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), "identity_extended_key_usage_invalid"),
    ],
)
def test_present_incompatible_leaf_constraints_are_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_identity: SyntheticIdentity,
    extension: x509.ExtensionType,
    reason: str,
) -> None:
    activate_synthetic_identity(monkeypatch, synthetic_identity)
    request = create_thailand_certificate_request(GW_ID, DEVICE, now=NOW)
    csr = x509.load_der_x509_csr(base64.b64decode(request.csr))
    certificate = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic GWM CA 0")]))
        .public_key(csr.public_key())
        .serial_number(1002)
        .not_valid_before(NOW - timedelta(minutes=1))
        .not_valid_after(NOW + timedelta(days=30))
        .add_extension(extension, critical=True)
        .sign(synthetic_identity.issuer_key, hashes.SHA256())
    )
    encoded = base64.b64encode(certificate.public_bytes(serialization.Encoding.DER)).decode()
    with pytest.raises(ThailandIdentityError, match=f"^{reason}$"):
        complete_thailand_certificate_request(
            request, encoded, material=synthetic_identity.material,
            gw_id=GW_ID, device_id=DEVICE, now=NOW,
        )


@pytest.mark.asyncio
async def test_certificate_network_failure_preserves_accepted_session_and_csr_for_retry(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    from custom_components.gwm_thailand_connect.gwm_client.errors import GwmNetworkError

    class InterruptedEnrollment(ScriptedThaiTransport):
        async def execute(
            self, request: _TransportRequest, *, deadline: _Deadline,
            connect_timeout: float, read_timeout: float,
        ) -> _TransportResponse:
            if request.operation == "enroll_certificate":
                self.requests.append(request)
                raise GwmNetworkError(operation="enroll_certificate")
            return await super().execute(
                request, deadline=deadline, connect_timeout=connect_timeout, read_timeout=read_timeout,
            )

    credentials = ThailandCredentials("owner@example.invalid", "password", DEVICE)
    interrupted = InterruptedEnrollment(synthetic_identity, [login_response()])
    async with ThailandClient(app_material, transport=interrupted) as client:
        result = await client.authenticate(credentials, allow_session_reclaim=True)
        assert isinstance(result, ThailandInitializationRequired)
        assert result.reason == "enrollment_failed"
        assert result.state.access_token == "synthetic-access"
        assert result.state.certificate_request is not None
        csr = result.state.certificate_request.csr
    recovered = ScriptedThaiTransport(synthetic_identity, [])
    async with ThailandClient(app_material, transport=recovered) as client:
        result = await client.authenticate(
            credentials, state=result.state, allow_password_login=False,
        )
        assert isinstance(result, ThailandAuthenticated)
        assert result.state.certificate_request is None
    assert len(recovered.requests) == 1
    assert recovered.requests[0].operation == "enroll_certificate"
    assert json.loads(recovered.requests[0].body)["csr"] == csr


@pytest.mark.asyncio
async def test_thailand_client_cancellation_deadline_and_rejected_reads_do_not_log_in(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    credentials = ThailandCredentials("owner@example.invalid", "password", DEVICE)
    transport = ScriptedThaiTransport(synthetic_identity, [])
    transport.wait_forever = True
    async with ThailandClient(app_material, transport=transport) as client:
        with pytest.raises(GwmDeadlineExceededError):
            await client.authenticate(credentials, allow_session_reclaim=True, timeout=0.03)
        assert not client.authenticated
        task = asyncio.create_task(client.authenticate(credentials, allow_session_reclaim=True))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client.authenticated


@pytest.mark.asyncio
async def test_thailand_client_unknown_api_errors_are_not_retried_or_disguised_as_success(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    transport = ScriptedThaiTransport(synthetic_identity, [{"code": "550002", "description": "private"}])
    async with ThailandClient(app_material, transport=transport) as client:
        with pytest.raises(GwmApiError) as failure:
            await client.authenticate(
                ThailandCredentials("owner@example.invalid", "password", DEVICE),
                allow_session_reclaim=True,
            )
        assert failure.value.api_code == "550002"
        assert "private" not in str(failure.value)
        assert len(transport.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("account", ["owner@example.invalid", "0812345678"])
async def test_official_thai_code_login_uses_type_three_without_password_or_preconsuming_code(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
    account: str,
) -> None:
    credentials = ThailandCredentials(
        account, None, DEVICE,
        authentication_method=ThailandAuthenticationMethod.VERIFICATION_CODE,
    )
    transport = ScriptedThaiTransport(
        synthetic_identity,
        [{"code": "000000"}, {"code": "308011"}, login_response()],
    )
    async with ThailandClient(app_material, transport=transport) as client:
        consent = await client.authenticate(credentials)
        assert isinstance(consent, ThailandSessionConsentRequired)
        assert not transport.requests
        challenge = await client.authenticate(
            credentials, state=consent.state, allow_session_reclaim=True,
        )
        assert isinstance(challenge, ThailandVerificationRequired)
        assert challenge.code_requested
        throttled = await client.authenticate(
            credentials, state=challenge.state, allow_session_reclaim=True,
        )
        assert isinstance(throttled, ThailandVerificationRequired)
        assert not throttled.code_requested
        assert len(transport.requests) == 1
        rejected = await client.authenticate(
            credentials, state=challenge.state, verification_code="111111", allow_session_reclaim=True,
        )
        assert isinstance(rejected, ThailandVerificationRequired) and rejected.code_rejected
        result = await client.authenticate(
            credentials, state=rejected.state, verification_code="222222", allow_session_reclaim=True,
        )
        assert isinstance(result, ThailandAuthenticated)
        assert result.state.authentication_method is ThailandAuthenticationMethod.VERIFICATION_CODE
        assert result.state.verification_requested_at is None
    expected_type = "2" if "@" in account else "1"
    request_body = json.loads(transport.requests[0].body)
    assert request_body == {
        "type": "3", "account": account, "accountType": expected_type,
        "countryCode": "+66", "validCodeMode": 1, "operateCode": "",
        "captchaType": "", "captchaId": "", "token": "",
    }
    login = transport.requests[2]
    assert login.url.endswith("/userAuth/loginWithVerifyCode")
    body = json.loads(login.body)
    assert body == {
        "account": account, "verifyCode": "222222", "accountType": expected_type,
        "deviceId": DEVICE, "appType": "0", "countryCode": "+66",
        "agreement": [1, 2], "pushToken": None, "country": "TH", "validCodeMode": 1,
    }
    assert [request.operation for request in transport.requests] == [
        "request_verification", "login", "login", "enroll_certificate",
    ]
    assert all("password" not in json.loads(request.body) for request in transport.requests)
    assert "222222" not in repr(result.state)


@pytest.mark.asyncio
async def test_code_login_does_not_request_sms_during_restart_or_accept_an_unrequested_code(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    credentials = ThailandCredentials(
        "owner@example.invalid", None, DEVICE,
        authentication_method=ThailandAuthenticationMethod.VERIFICATION_CODE,
    )
    transport = ScriptedThaiTransport(synthetic_identity, [])
    async with ThailandClient(app_material, transport=transport) as client:
        with pytest.raises(GwmAuthenticationError):
            await client.authenticate(credentials, allow_password_login=False)
        with pytest.raises(GwmConfigurationError):
            await client.authenticate(
                credentials, verification_code="111111", allow_session_reclaim=True,
            )
    assert not transport.requests


def test_thai_authentication_continuations_cannot_cross_password_and_code_methods() -> None:
    password = ThailandCredentials("owner@example.invalid", "password", DEVICE)
    code = ThailandCredentials(
        "owner@example.invalid", None, DEVICE,
        authentication_method=ThailandAuthenticationMethod.VERIFICATION_CODE,
    )
    assert not ThailandAuthState.for_credentials(password).matches(code)
    assert not ThailandAuthState.for_credentials(code).matches(password)
    with pytest.raises(GwmConfigurationError):
        ThailandCredentials(
            "owner@example.invalid", "unneeded-password", DEVICE,
            authentication_method=ThailandAuthenticationMethod.VERIFICATION_CODE,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_code", ["308008", "308024"])
async def test_verified_app_rate_limit_codes_never_trigger_an_automatic_authentication_retry(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
    api_code: str,
) -> None:
    from custom_components.gwm_thailand_connect.gwm_client.errors import GwmRateLimitError

    transport = ScriptedThaiTransport(synthetic_identity, [{"code": api_code}])
    async with ThailandClient(app_material, transport=transport) as client:
        with pytest.raises(GwmRateLimitError) as captured:
            await client.authenticate(
                ThailandCredentials("owner@example.invalid", "password", DEVICE),
                allow_session_reclaim=True,
            )
        assert captured.value.api_code == api_code
        assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_actual_310022_reply_without_data_stops_duplicate_enrollment_and_preserves_session(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    credentials = ThailandCredentials("owner@example.invalid", "password", DEVICE)
    transport = ScriptedThaiTransport(synthetic_identity, [login_response()])
    transport.enrollment_code = "310022"
    transport.certificate_data_missing = True
    async with ThailandClient(app_material, transport=transport) as client:
        result = await client.authenticate(credentials, allow_session_reclaim=True)
        assert isinstance(result, ThailandInitializationRequired)
        assert result.reason == "device_certificate_exists"
        assert result.state.certificate_conflict
        assert result.state.access_token == "synthetic-access"
        assert result.state.certificate_request is not None
        resumed = await client.authenticate(
            credentials, state=result.state, allow_password_login=False,
        )
        assert isinstance(resumed, ThailandInitializationRequired)
        assert resumed.reason == "device_certificate_exists"
        assert [request.operation for request in transport.requests] == ["login", "enroll_certificate"]
        assert not client.authenticated


@pytest.mark.asyncio
async def test_310022_with_a_usable_matching_certificate_keeps_the_native_success_contract(
    app_material: thailand_protocol.ThailandAppMaterial,
    synthetic_identity: SyntheticIdentity,
) -> None:
    transport = ScriptedThaiTransport(synthetic_identity, [login_response()])
    transport.enrollment_code = "310022"
    async with ThailandClient(app_material, transport=transport) as client:
        result = await client.authenticate(
            ThailandCredentials("owner@example.invalid", "password", DEVICE),
            allow_session_reclaim=True,
        )
        assert isinstance(result, ThailandAuthenticated)
        assert not result.state.certificate_conflict
