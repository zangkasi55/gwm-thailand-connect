"""Private, account-bound state. Never include these records in diagnostics."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import fields
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .gwm_client.thailand_client import ThailandAuthenticationMethod, ThailandAuthState
from .gwm_client.thailand_identity import ThailandCertificateRequest, ThailandIssuedIdentity


def _identity_document(value: ThailandIssuedIdentity | ThailandCertificateRequest | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        field.name: base64.b64encode(item).decode("ascii") if isinstance(item := getattr(value, field.name), bytes) else item
        for field in fields(value)
    }


def encode_state(state: ThailandAuthState) -> dict[str, Any]:
    result = {field.name: getattr(state, field.name) for field in fields(state)}
    result["authentication_method"] = ThailandAuthenticationMethod(state.authentication_method).value
    result["verification_requested_at"] = (
        state.verification_requested_at.isoformat() if state.verification_requested_at else None
    )
    result["issued_identity"] = _identity_document(state.issued_identity)
    result["certificate_request"] = _identity_document(state.certificate_request)
    return result


def decode_state(document: dict[str, Any]) -> ThailandAuthState:
    if set(document) != {field.name for field in fields(ThailandAuthState)}:
        raise ValueError("Unexpected private account-state schema")
    values = dict(document)
    for name, cls, binary in (
        ("issued_identity", ThailandIssuedIdentity, {"certificate_data", "encrypted_private_key_data", "private_key_password"}),
        ("certificate_request", ThailandCertificateRequest, {"encrypted_private_key_data", "private_key_password"}),
    ):
        if values[name] is not None:
            item: dict[str, Any] = dict(values[name])
            if set(item) != {field.name for field in fields(cls)}:
                raise ValueError("Unexpected private identity-state schema")
            for key in binary:
                item[key] = base64.b64decode(item[key], validate=True)
            values[name] = cls(**item)
    if values["verification_requested_at"] is not None:
        values["verification_requested_at"] = datetime.fromisoformat(values["verification_requested_at"])
    return ThailandAuthState(**values)


class AccountStore:
    def __init__(self, hass: HomeAssistant, account_binding: str) -> None:
        self._store = Store[dict[str, Any]](
            hass, 1, DOMAIN + ".account." + account_binding, private=True, atomic_writes=True,
        )
        self._document: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def _load(self) -> dict[str, Any]:
        if self._document is None:
            loaded = await self._store.async_load()
            self._document = {"auth": None, "commands": []} if loaded is None else loaded
            if set(self._document) != {"auth", "commands"} or not isinstance(self._document["commands"], list):
                raise ValueError("Invalid private account store")
        return self._document

    async def auth(self) -> ThailandAuthState | None:
        async with self._lock:
            document = await self._load()
            return None if document["auth"] is None else decode_state(document["auth"])

    async def save_auth(self, state: ThailandAuthState) -> None:
        async with self._lock:
            document = {**await self._load(), "auth": encode_state(state)}
            await self._store.async_save(document)
            self._document = document

    async def commands(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [dict(item) for item in (await self._load())["commands"]]

    async def save_command(self, command: dict[str, Any]) -> None:
        async with self._lock:
            current = await self._load()
            commands = [item for item in current["commands"] if item["sequence"] != command["sequence"]]
            document = {**current, "commands": [*commands, dict(command)][-32:]}
            await self._store.async_save(document)
            self._document = document

    async def remove(self) -> None:
        async with self._lock:
            await self._store.async_remove()
            self._document = None


def account_store(hass: HomeAssistant, account_binding: str) -> AccountStore:
    stores: dict[str, AccountStore] = hass.data.setdefault(DOMAIN + "_account_stores", {})
    if account_binding not in stores:
        stores[account_binding] = AccountStore(hass, account_binding)
    return stores[account_binding]
