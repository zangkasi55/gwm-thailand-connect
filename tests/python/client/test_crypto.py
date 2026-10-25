"""Binary certificate bytes must not be treated as whitespace-delimited text."""

from unittest.mock import patch

from custom_components.gwm_thailand_connect.gwm_client.crypto import load_certificate


def test_der_certificate_signature_whitespace_bytes_are_preserved():
    encoded = b"\x30\x03\x01\x02\n"
    with patch(
        "custom_components.gwm_thailand_connect.gwm_client.crypto.x509.load_der_x509_certificate",
    ) as parser:
        assert load_certificate(encoded) is parser.return_value
        parser.assert_called_once_with(encoded)
