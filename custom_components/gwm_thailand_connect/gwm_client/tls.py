"""Neutral MIT-licensed helpers extracted from ha-gwm-ev; see NOTICE.md."""

from __future__ import annotations

import base64
import binascii
import re

_GWM_CA_CERTIFICATE_COUNT = 3
_PEM_CERTIFICATE = re.compile('-----BEGIN CERTIFICATE-----\\s*(?P<payload>.*?)\\s*-----END CERTIFICATE-----', re.DOTALL)

def _validate_gwm_ca_bundle(pem_data: str, *, expected_count: int=_GWM_CA_CERTIFICATE_COUNT) -> None:
    if type(expected_count) is not int or expected_count not in {3, 4}:
        raise ValueError('Unsupported GWM CA certificate count')
    matches = list(_PEM_CERTIFICATE.finditer(pem_data))
    if len(matches) != expected_count:
        raise ValueError(f'The GWM CA bundle must contain {expected_count} certificates')
    cursor = 0
    for match in matches:
        if pem_data[cursor:match.start()].strip():
            raise ValueError('Unexpected data outside GWM CA certificates')
        cursor = match.end()
        payload = ''.join(match.group('payload').split())
        try:
            der = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError('Invalid base64 in GWM CA bundle') from error
        _validate_der_certificate_envelope(der)
    if pem_data[cursor:].strip():
        raise ValueError('Unexpected data outside GWM CA certificates')

def _validate_der_certificate_envelope(der: bytes) -> None:
    if len(der) < 2 or der[0] != 48:
        raise ValueError('Invalid DER certificate in GWM CA bundle')
    first_length = der[1]
    if first_length < 128:
        header_length = 2
        content_length = first_length
    else:
        length_bytes = first_length & 127
        if length_bytes == 0 or length_bytes > 4 or 2 + length_bytes > len(der):
            raise ValueError('Invalid DER certificate length in GWM CA bundle')
        header_length = 2 + length_bytes
        content_length = int.from_bytes(der[2:header_length], 'big')
    if header_length + content_length != len(der):
        raise ValueError('Truncated or trailing DER certificate data in GWM CA bundle')
