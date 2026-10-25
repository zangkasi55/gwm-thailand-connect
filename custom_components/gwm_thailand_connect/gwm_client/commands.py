"""Neutral MIT-licensed helpers extracted from ha-gwm-ev; see NOTICE.md."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

type RemoteCommandState = Literal['pending', 'completed', 'failed']
_COMMAND_IDENTIFIER = re.compile('[\\x21-\\x7e]{1,512}')

@dataclass(frozen=True, slots=True, repr=False)
class RemoteCommandAcceptance:
    """Provider-owned identifier returned only after command acceptance."""
    command_id: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, str) or _COMMAND_IDENTIFIER.fullmatch(self.command_id) is None:
            raise ValueError('command_acceptance_invalid')

@dataclass(frozen=True, slots=True, repr=False)
class RemoteCommandResult:
    """One correlated provider outcome in the integration's stable lifecycle."""
    state: RemoteCommandState
    result_code: str | None = None
    result_message: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {'pending', 'completed', 'failed'}:
            raise ValueError('command_result_invalid')
        for value in (self.result_code, self.result_message):
            if value is not None and (not isinstance(value, str) or len(value) > 512 or any(ord(character) < 32 for character in value)):
                raise ValueError('command_result_invalid')
