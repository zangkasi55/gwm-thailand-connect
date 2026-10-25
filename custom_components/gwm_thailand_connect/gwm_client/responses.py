"""Neutral MIT-licensed helpers extracted from ha-gwm-ev; see NOTICE.md."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping

from ._protocol import _TransportResponse
from .errors import GwmApiError, GwmAuthenticationError, GwmHttpError, GwmRateLimitError, GwmSchemaError

_MAX_JSON_DEPTH = 64

def _decode_envelope(response: _TransportResponse, *, operation: str, accepted_api_codes: frozenset[str]=frozenset({'000000'})) -> object:
    """Validate overseas success; payload requirements belong to each reader."""
    if response.status in {401, 403}:
        raise GwmAuthenticationError(operation=operation)
    if response.status == 429:
        retry_after = response.headers.get('retry-after')
        retry_seconds = int(retry_after) if retry_after and len(retry_after) <= 10 and retry_after.isdecimal() else None
        raise GwmRateLimitError(operation=operation, retry_after_seconds=retry_seconds)
    if not 200 <= response.status <= 299:
        raise GwmHttpError(operation=operation, status=response.status)
    invalid = False
    try:
        text = response.body.decode('utf-8', errors='strict')
        envelope = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_json_constant)
        _validate_json_depth(envelope)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        invalid = True
        envelope = None
    if invalid or not isinstance(envelope, Mapping):
        raise GwmSchemaError(operation=operation)
    code = envelope.get('code')
    if not isinstance(code, str) or code not in accepted_api_codes:
        raise GwmApiError(operation=operation, api_code=code)
    return envelope.get('data')

def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result

def _reject_json_constant(_value: str) -> object:
    raise ValueError('invalid_json_number')

def _validate_json_depth(value: object, *, depth: int=0) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError('json_too_deep')
    if isinstance(value, float) and (not math.isfinite(value)):
        raise ValueError('invalid_json_number')
    if isinstance(value, Mapping):
        for child in value.values():
            _validate_json_depth(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_json_depth(child, depth=depth + 1)
