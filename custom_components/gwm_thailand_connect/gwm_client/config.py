"""Neutral MIT-licensed helpers extracted from ha-gwm-ev; see NOTICE.md."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestTimeouts:
    """One monotonic request budget and its socket phase ceilings."""
    total: float = 30.0
    connect: float = 10.0
    read: float = 20.0

    def __post_init__(self) -> None:
        values = (self.total, self.connect, self.read)
        if any(isinstance(value, bool) or not isinstance(value, int | float) or (not math.isfinite(value)) or (value <= 0) for value in values):
            raise ValueError('timeouts_invalid')
        if self.connect > self.total or self.read > self.total:
            raise ValueError('timeouts_invalid')
