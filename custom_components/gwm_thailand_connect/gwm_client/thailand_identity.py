"""Private, verified TLS provisioning for the Thai My GWM app identity.

The reviewed source is com.gwm.tha 3.0.8 from Huawei AppGallery C109631289.
Only public identity fingerprints are part of this module. The transformed
private material remains in the owner's app artifact or private configuration.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import secrets
import ssl
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .crypto import (
    _read_der_value,
    generate_client_certificate_request,
    load_certificate,
    recover_transformed_private_key,
)
from .tls import _PEM_CERTIFICATE, _validate_gwm_ca_bundle

_APP_ARTIFACT_SHA256: Final = "7594b52138ad8c36a5c426321e251c899dead78ab578bdc548e7db4a6e26c7f3"
_BOOTSTRAP_CERTIFICATE_SHA256: Final = "e4efd3be93a65dd4086862aa33925bde627b2385b89a7a7d1907ad21ef8ce275"
_CA_DER_SHA256: Final = frozenset(
    {
        "7ad7726c15374e2ea58ece1f5d6aaff5e0ba9d44cea616504c9bcec8b860f0ac",
        "0b73613f9caee93f8cb69a51632a49682b7746544f98169ebe136116ab8b2a80",
        "937f862cf2d1ee232b6c93890c6e9b31b1340497e70d0761cee64dc47ab1a96e",
        "006e11b5c43bea92da7359650e8fe5030450340b1e4978569e4f997e9a2ff90d",
    }
)
_ERROR_CATEGORIES: Final = frozenset(
    {
        "app_artifact_unreadable",
        "app_artifact_unrecognized",
        "bootstrap_identity_invalid",
        "bootstrap_identity_unrecognized",
        "bootstrap_private_key_invalid",
        "ca_bundle_invalid",
        "identity_expired",
        "identity_key_mismatch",
        "identity_binding_mismatch",
        "identity_not_yet_valid",
        "issued_identity_invalid",
        "issued_identity_chain_invalid",
        "identity_basic_constraints_invalid",
        "identity_key_usage_invalid",
        "identity_extended_key_usage_invalid",
        "certificate_request_invalid",
        "temporary_identity_storage_failed",
        "time_invalid",
        "tls_context_invalid",
    }
)
_MEMBERS: Final = (
    "assets/pki_release/gwm_general.cer",
    "assets/pki_release/gwm_general.key",
    "assets/pki_release/gwm_root.pem",
)
_MAX_MATERIAL_BYTES: Final = 128 * 1024


class ThailandIdentityError(ValueError):
    """Fixed-category errors never include credentials or input material."""

    __slots__ = ("category",)

    def __init__(self, category: object) -> None:
        self.category = (
            category
            if isinstance(category, str) and category in _ERROR_CATEGORIES
            else "bootstrap_identity_invalid"
        )
        super().__init__(self.category)

    def __repr__(self) -> str:
        return f"ThailandIdentityError(category={self.category!r})"


@dataclass(frozen=True, slots=True)
class ThailandBootstrapMaterial:
    """App identity bytes, never serialized through object representations."""

    certificate_data: bytes = field(repr=False)
    transformed_private_key_data: bytes = field(repr=False)
    ca_bundle: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, bytes) or not value or len(value) > _MAX_MATERIAL_BYTES
            for value in (
                self.certificate_data,
                self.transformed_private_key_data,
                self.ca_bundle,
            )
        ):
            raise ThailandIdentityError("bootstrap_identity_invalid")


@dataclass(frozen=True, slots=True)
class ThailandCertificateRequest:
    """An account/device-bound CSR and encrypted, locally generated RSA key."""

    csr: str = field(repr=False)
    common_name: str = field(repr=False)
    binding: str = field(repr=False)
    encrypted_private_key_data: bytes = field(repr=False)
    private_key_password: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.csr, str) or not 0 < len(self.csr) <= 16 * 1024
            or not isinstance(self.common_name, str) or not self.common_name.startswith("LGW")
            or len(self.common_name) > 64 or not self.common_name.isascii()
            or not isinstance(self.binding, str) or len(self.binding) != 64
            or any(character not in "0123456789abcdef" for character in self.binding)
            or not isinstance(self.encrypted_private_key_data, bytes)
            or not self.encrypted_private_key_data.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
            or len(self.encrypted_private_key_data) > 16 * 1024
            or not isinstance(self.private_key_password, bytes)
            or not 32 <= len(self.private_key_password) <= 128
        ):
            raise ThailandIdentityError("certificate_request_invalid")


@dataclass(frozen=True, slots=True)
class ThailandIssuedIdentity:
    """Private persistence contains an encrypted key, never plaintext PKCS#8."""

    certificate_data: bytes = field(repr=False)
    common_name: str = field(repr=False)
    binding: str = field(repr=False)
    encrypted_private_key_data: bytes = field(repr=False)
    private_key_password: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.certificate_data, bytes)
            or not 0 < len(self.certificate_data) <= 64 * 1024
            or not isinstance(self.common_name, str)
            or not 0 < len(self.common_name) <= 64
            or not self.common_name.isascii()
            or not isinstance(self.binding, str)
            or len(self.binding) != 64
            or any(character not in "0123456789abcdef" for character in self.binding)
            or not isinstance(self.encrypted_private_key_data, bytes)
            or not self.encrypted_private_key_data.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
            or len(self.encrypted_private_key_data) > 16 * 1024
            or not isinstance(self.private_key_password, bytes)
            or not 32 <= len(self.private_key_password) <= 128
        ):
            raise ThailandIdentityError("issued_identity_invalid")

    @property
    def expires_at(self) -> datetime:
        try:
            return load_certificate(self.certificate_data).not_valid_after_utc
        except (TypeError, ValueError, UnsupportedAlgorithm):
            raise ThailandIdentityError("issued_identity_invalid") from None


def load_thailand_bootstrap_material_from_apk(apk_path: Path) -> ThailandBootstrapMaterial:
    """Read only the reviewed artifact's three identity members."""

    try:
        with apk_path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
            if digest != _APP_ARTIFACT_SHA256:
                raise ThailandIdentityError("app_artifact_unrecognized")
            source.seek(0)
            with zipfile.ZipFile(source) as archive:
                for member in _MEMBERS:
                    if archive.getinfo(member).file_size > _MAX_MATERIAL_BYTES:
                        raise ThailandIdentityError("bootstrap_identity_invalid")
                return ThailandBootstrapMaterial(
                    certificate_data=archive.read(_MEMBERS[0]),
                    transformed_private_key_data=archive.read(_MEMBERS[1]),
                    ca_bundle=archive.read(_MEMBERS[2]),
                )
    except ThailandIdentityError:
        raise
    except (OSError, KeyError, zipfile.BadZipFile):
        raise ThailandIdentityError("app_artifact_unreadable") from None


def create_thailand_bootstrap_ssl_context(
    material: ThailandBootstrapMaterial,
    *,
    now: datetime | None = None,
    temporary_directory: Path | None = None,
) -> ssl.SSLContext:
    """Use the pinned client leaf and normal public-gateway server trust."""

    if type(material) is not ThailandBootstrapMaterial:
        raise ThailandIdentityError("bootstrap_identity_invalid")
    current_time = datetime.now(UTC) if now is None else now
    if not isinstance(current_time, datetime) or current_time.tzinfo is None:
        raise ThailandIdentityError("time_invalid")
    current_time = current_time.astimezone(UTC)

    certificate = _verified_certificate(material.certificate_data, current_time)
    _verified_ca_bundle(material.ca_bundle)
    try:
        private_key = recover_transformed_private_key(
            material.certificate_data,
            material.transformed_private_key_data,
        )
        public_key = certificate.public_key()
        if (
            not isinstance(public_key, rsa.RSAPublicKey)
            or private_key.key_size < 2048
            or private_key.public_key().public_numbers() != public_key.public_numbers()
        ):
            raise ThailandIdentityError("identity_key_mismatch")
    except ThailandIdentityError:
        raise
    except (TypeError, ValueError, UnsupportedAlgorithm):
        raise ThailandIdentityError("bootstrap_private_key_invalid") from None

    return _create_leaf_context(certificate, private_key, temporary_directory=temporary_directory)


def _create_leaf_context(
    certificate: x509.Certificate,
    private_key: rsa.RSAPrivateKey,
    *,
    temporary_directory: Path | None = None,
) -> ssl.SSLContext:
    password = secrets.token_urlsafe(40).encode("ascii")
    try:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        if context.minimum_version < ssl.TLSVersion.TLSv1_2:
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        if (
            not context.check_hostname
            or context.verify_mode != ssl.CERT_REQUIRED
            or context.security_level < 2
        ):
            raise ThailandIdentityError("tls_context_invalid")
        # OEM identity CAs are not web-server trust anchors. Adding them here
        # makes OpenSSL auto-build a legacy client chain (CA_MD_TOO_WEAK).
        with tempfile.TemporaryDirectory(
            prefix="gwm-thai-identity-",
            dir=temporary_directory,
        ) as folder:
            certfile = Path(folder) / "client-public.pem"
            keyfile = Path(folder) / "client-encrypted.pem"
            certfile.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            encrypted_key = private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(password),
            )
            descriptor = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(encrypted_key)
            context.load_cert_chain(certfile=certfile, keyfile=keyfile, password=password)
        return context
    except ThailandIdentityError:
        raise
    except ssl.SSLError:
        raise ThailandIdentityError("tls_context_invalid") from None
    except OSError:
        raise ThailandIdentityError("temporary_identity_storage_failed") from None


def create_thailand_certificate_request(
    gw_id: str,
    device_id: str,
    *,
    now: datetime | None = None,
) -> ThailandCertificateRequest:
    """Match the native package/account/device CN binding using a strong key."""

    if now is not None and (not isinstance(now, datetime) or now.tzinfo is None):
        raise ThailandIdentityError("time_invalid")
    binding = _device_binding(gw_id, device_id)
    digest = hashlib.sha256(("com.gwm.tha" + gw_id + device_id).encode()).hexdigest()[16:48]
    try:
        generated = generate_client_certificate_request("TH", digest, now=now)
        csr_data = base64.b64decode(generated.csr, validate=True)
        csr = x509.load_der_x509_csr(csr_data)
        key = serialization.load_der_private_key(
            base64.b64decode(generated.private_key, validate=True), password=None
        )
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048 or not csr.is_signature_valid:
            raise ThailandIdentityError("certificate_request_invalid")
        common_name = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        if not isinstance(common_name, str):
            raise ThailandIdentityError("certificate_request_invalid")
        password = secrets.token_urlsafe(40).encode("ascii")
        return ThailandCertificateRequest(
            csr=generated.csr,
            common_name=common_name,
            binding=binding,
            encrypted_private_key_data=key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(password),
            ),
            private_key_password=password,
        )
    except ThailandIdentityError:
        raise
    except (ValueError, TypeError, IndexError, UnsupportedAlgorithm, binascii.Error):
        raise ThailandIdentityError("certificate_request_invalid") from None


def complete_thailand_certificate_request(
    request: ThailandCertificateRequest,
    encoded: str,
    *,
    material: ThailandBootstrapMaterial,
    gw_id: str,
    device_id: str,
    now: datetime | None = None,
) -> ThailandIssuedIdentity:
    """Accept only the matching certificate returned by authenticated enrollment."""

    if type(request) is not ThailandCertificateRequest or not isinstance(encoded, str) or len(encoded) > 96 * 1024:
        raise ThailandIdentityError("issued_identity_invalid")
    if request.binding != _device_binding(gw_id, device_id):
        raise ThailandIdentityError("identity_binding_mismatch")
    try:
        csr = x509.load_der_x509_csr(base64.b64decode(request.csr, validate=True))
        if (
            not csr.is_signature_valid
            or [
                attribute.value
                for attribute in csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            ] != [request.common_name]
        ):
            raise ThailandIdentityError("certificate_request_invalid")
        normalized = encoded.translate(str.maketrans("", "", " \t\r\n"))
        certificate = load_certificate(base64.b64decode(normalized, validate=True))
        if csr.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
        ) != certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
        ):
            raise ThailandIdentityError("identity_key_mismatch")
        names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        common_name = names[0].value if len(names) == 1 else None
        if not isinstance(common_name, str):
            raise ThailandIdentityError("identity_binding_mismatch")
        # The issuer can replace the CSR name; the signed CSR key still binds the leaf.
        identity = ThailandIssuedIdentity(
            certificate_data=certificate.public_bytes(serialization.Encoding.DER),
            common_name=common_name,
            binding=request.binding,
            encrypted_private_key_data=request.encrypted_private_key_data,
            private_key_password=request.private_key_password,
        )
        _validated_issued_identity(
            identity, material=material, gw_id=gw_id, device_id=device_id, now=now
        )
        return identity
    except ThailandIdentityError:
        raise
    except (ValueError, TypeError, UnsupportedAlgorithm, binascii.Error):
        raise ThailandIdentityError("issued_identity_invalid") from None


def create_thailand_issued_ssl_context(
    identity: ThailandIssuedIdentity,
    *,
    material: ThailandBootstrapMaterial,
    gw_id: str,
    device_id: str,
    now: datetime | None = None,
    temporary_directory: Path | None = None,
) -> ssl.SSLContext:
    certificate, key = _validated_issued_identity(
        identity, material=material, gw_id=gw_id, device_id=device_id, now=now
    )
    return _create_leaf_context(certificate, key, temporary_directory=temporary_directory)


def _validated_issued_identity(
    identity: ThailandIssuedIdentity,
    *,
    material: ThailandBootstrapMaterial,
    gw_id: str,
    device_id: str,
    now: datetime | None,
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    if type(identity) is not ThailandIssuedIdentity or type(material) is not ThailandBootstrapMaterial:
        raise ThailandIdentityError("issued_identity_invalid")
    if identity.binding != _device_binding(gw_id, device_id):
        raise ThailandIdentityError("identity_binding_mismatch")
    instant = datetime.now(UTC) if now is None else now
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise ThailandIdentityError("time_invalid")
    try:
        certificate = load_certificate(identity.certificate_data)
        key = serialization.load_pem_private_key(
            identity.encrypted_private_key_data, password=identity.private_key_password
        )
        public = certificate.public_key()
        if (
            not isinstance(key, rsa.RSAPrivateKey)
            or key.key_size < 2048
            or not isinstance(public, rsa.RSAPublicKey)
            or public.public_numbers() != key.public_key().public_numbers()
        ):
            raise ThailandIdentityError("identity_key_mismatch")
        if instant + timedelta(minutes=5) < certificate.not_valid_before_utc:
            raise ThailandIdentityError("identity_not_yet_valid")
        if instant >= certificate.not_valid_after_utc:
            raise ThailandIdentityError("identity_expired")
        names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if len(names) != 1 or names[0].value != identity.common_name:
            raise ThailandIdentityError("identity_binding_mismatch")
        basic = _optional_leaf_extension(certificate, x509.BasicConstraints)
        if basic is not None and basic.ca:
            raise ThailandIdentityError("identity_basic_constraints_invalid")
        usage = _optional_leaf_extension(certificate, x509.KeyUsage)
        if usage is not None and (not usage.digital_signature or usage.key_cert_sign):
            raise ThailandIdentityError("identity_key_usage_invalid")
        extended = _optional_leaf_extension(certificate, x509.ExtendedKeyUsage)
        if extended is not None and not (
            {ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.ANY_EXTENDED_KEY_USAGE} & set(extended)
        ):
            raise ThailandIdentityError("identity_extended_key_usage_invalid")
        algorithm = certificate.signature_hash_algorithm
        if algorithm is None or algorithm.digest_size < 32:
            raise ThailandIdentityError("issued_identity_invalid")
        _verified_ca_bundle(material.ca_bundle)
        for block in _PEM_CERTIFICATE.finditer(material.ca_bundle.decode("ascii")):
            der = base64.b64decode("".join(block.group("payload").split()), validate=True)
            issuer = _certificate_public_key_without_names(der)
            parameters = certificate.signature_algorithm_parameters
            try:
                if isinstance(issuer, rsa.RSAPublicKey) and isinstance(parameters, padding.PKCS1v15 | padding.PSS):
                    issuer.verify(certificate.signature, certificate.tbs_certificate_bytes, parameters, algorithm)
                elif isinstance(issuer, ec.EllipticCurvePublicKey) and isinstance(parameters, ec.ECDSA):
                    issuer.verify(certificate.signature, certificate.tbs_certificate_bytes, parameters)
                else:
                    continue
            except InvalidSignature:
                continue
            return certificate, key
        raise ThailandIdentityError("issued_identity_chain_invalid")
    except ThailandIdentityError:
        raise
    except (ValueError, TypeError, IndexError, x509.ExtensionNotFound, UnsupportedAlgorithm, binascii.Error):
        raise ThailandIdentityError("issued_identity_invalid") from None


def _optional_leaf_extension[T: x509.ExtensionType](
    certificate: x509.Certificate,
    extension: type[T],
) -> T | None:
    # X.509 usage extensions constrain a leaf when present; they need not exist.
    try:
        return certificate.extensions.get_extension_for_class(extension).value
    except x509.ExtensionNotFound:
        return None


def _certificate_public_key_without_names(der: bytes) -> object:
    # Legacy OEM PrintableString DNs need not be decoded to verify a signature.
    certificate, _ = _read_der_value(der, 0, 0x30)
    tbs, _ = _read_der_value(certificate, 0, 0x30)
    offset = 0
    if tbs[0] == 0xA0:
        _, offset = _read_der_value(tbs, offset, 0xA0)
    for tag in (0x02, 0x30, 0x30, 0x30, 0x30):
        _, offset = _read_der_value(tbs, offset, tag)
    start = offset
    _, offset = _read_der_value(tbs, offset, 0x30)
    return serialization.load_der_public_key(tbs[start:offset])


def _device_binding(gw_id: str, device_id: str) -> str:
    if (
        not isinstance(gw_id, str)
        or not 0 < len(gw_id) <= 16 * 1024
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in gw_id)
        or not isinstance(device_id, str)
        or len(device_id) != 32
        or any(character not in "0123456789abcdef" for character in device_id)
    ):
        raise ThailandIdentityError("identity_binding_mismatch")
    return hashlib.sha256(b"gwm-thailand-device-v1\0" + gw_id.encode() + b"\0" + device_id.encode()).hexdigest()


def _verified_certificate(data: bytes, now: datetime) -> x509.Certificate:
    try:
        certificate = load_certificate(data)
        encoded = certificate.public_bytes(serialization.Encoding.DER)
        if hashlib.sha256(encoded).hexdigest() != _BOOTSTRAP_CERTIFICATE_SHA256:
            raise ThailandIdentityError("bootstrap_identity_unrecognized")
        if now < certificate.not_valid_before_utc:
            raise ThailandIdentityError("identity_not_yet_valid")
        if now >= certificate.not_valid_after_utc:
            raise ThailandIdentityError("identity_expired")
        return certificate
    except ThailandIdentityError:
        raise
    except (TypeError, ValueError, UnsupportedAlgorithm):
        raise ThailandIdentityError("bootstrap_identity_invalid") from None


def _verified_ca_bundle(data: bytes) -> str:
    try:
        text = data.decode("ascii")
        _validate_gwm_ca_bundle(text, expected_count=4)
        fingerprints = frozenset(
            hashlib.sha256(
                base64.b64decode("".join(match.group("payload").split()), validate=True)
            ).hexdigest()
            for match in _PEM_CERTIFICATE.finditer(text)
        )
        if fingerprints != _CA_DER_SHA256:
            raise ThailandIdentityError("ca_bundle_invalid")
        return text
    except ThailandIdentityError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, binascii.Error):
        raise ThailandIdentityError("ca_bundle_invalid") from None


__all__ = [
    "ThailandBootstrapMaterial",
    "ThailandIdentityError",
    "ThailandCertificateRequest",
    "ThailandIssuedIdentity",
    "complete_thailand_certificate_request",
    "create_thailand_certificate_request",
    "create_thailand_bootstrap_ssl_context",
    "create_thailand_issued_ssl_context",
    "load_thailand_bootstrap_material_from_apk",
]
