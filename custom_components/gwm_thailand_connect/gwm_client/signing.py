"""Neutral MIT-licensed helpers extracted from ha-gwm-ev; see NOTICE.md."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SignedRequest:
    """The request URL and generated auth headers from a signing operation.

    The headers are additions for an HTTP adapter to merge into its request;
    they are not a replacement for content or application headers.
    """
    method: str
    url: str = field(repr=False)
    headers: dict[str, str] = field(repr=False)
    body: str | None = field(repr=False)
