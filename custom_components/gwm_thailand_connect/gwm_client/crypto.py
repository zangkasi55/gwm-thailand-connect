"""Local identity codec using standard cryptography APIs, without bundled keys."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass(frozen=True, slots=True)
class GeneratedClientCertificateRequest:
    csr: str = field(repr=False)
    private_key: str = field(repr=False)


def load_certificate(data: bytes) -> x509.Certificate:
    value = data.lstrip()
    if value.startswith(b"-----BEGIN CERTIFICATE-----"):
        return x509.load_pem_x509_certificate(value)
    return x509.load_der_x509_certificate(data)


def _read_der_value(data: bytes, offset: int, expected_tag: int) -> tuple[bytes, int]:
    if offset < 0 or offset + 2 > len(data) or data[offset] != expected_tag:
        raise ValueError("Invalid DER structure")
    size = data[offset + 1]
    start = offset + 2
    if size >= 128:
        count = size & 127
        if count == 0 or count > 4 or start + count > len(data):
            raise ValueError("Invalid DER length")
        size = int.from_bytes(data[start:start + count])
        start += count
    end = start + size
    if end > len(data):
        raise ValueError("Incomplete DER structure")
    return data[start:end], end


def recover_transformed_private_key(certificate_data: bytes, transformed_key_data: bytes) -> rsa.RSAPrivateKey:
    """Decode the reviewed local artifact format; never download an identity."""

    public = load_certificate(certificate_data).public_key()
    if not isinstance(public, rsa.RSAPublicKey):
        raise ValueError("An RSA certificate is required")
    encoded = base64.b64decode(b"".join(transformed_key_data.split()), validate=True)
    sequence, consumed = _read_der_value(encoded, 0, 48)
    if consumed != len(encoded):
        raise ValueError("Unexpected key envelope")
    integers = []
    cursor = 0
    for _ in range(9):
        raw, cursor = _read_der_value(sequence, cursor, 2)
        if not raw:
            raise ValueError("Missing key integer")
        integers.append(int.from_bytes(raw, signed=True))
    version, modulus, stored_exponent, packed, *unused = integers
    if (
        cursor != len(sequence) or version != 0 or stored_exponent != 1
        or unused != [1, 1, 1, 1, 1] or packed <= 0
        or modulus != public.public_numbers().n
    ):
        raise ValueError("Unrecognized local identity structure")
    highest = ((packed.bit_length() - 1) // 5) * 5
    exponent = (packed >> highest) << highest
    for shift in range(0, highest, 5):
        digit = (packed >> shift) & 31
        exponent |= ((digit & 24) | ((digit + 3) & 7)) << shift
    numbers = public.public_numbers()
    p, q = rsa.rsa_recover_prime_factors(modulus, numbers.e, exponent)
    return rsa.RSAPrivateNumbers(
        p=p, q=q, d=exponent,
        dmp1=rsa.rsa_crt_dmp1(exponent, p),
        dmq1=rsa.rsa_crt_dmq1(exponent, q),
        iqmp=rsa.rsa_crt_iqmp(p, q),
        public_numbers=numbers,
    ).private_key()


def generate_client_certificate_request(
    country: str | None, device_id: str | None, *, now: datetime | None = None,
) -> GeneratedClientCertificateRequest:
    if country != "TH" or device_id is None or len(device_id) != 32:
        raise ValueError("Invalid Thai enrollment identity")
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("Enrollment time requires a timezone")
    common_name = "LGWMy GWM-AD-TH" + device_id.upper() + str(int(instant.timestamp()))
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Great Wall Motor Co., Ltd."),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "EE System Design Dept"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Operational"),
    ])
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    request = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(private, hashes.SHA256())
    return GeneratedClientCertificateRequest(
        base64.b64encode(request.public_bytes(serialization.Encoding.DER)).decode("ascii"),
        base64.b64encode(private.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        )).decode("ascii"),
    )
