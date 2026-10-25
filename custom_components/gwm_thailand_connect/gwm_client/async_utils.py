"""Neutral MIT-licensed helpers extracted from ha-gwm-ev; see NOTICE.md."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable


async def _blocking_call[T](function: Callable[[], T]) -> T:
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if task.done() and (not task.cancelled()):
            with contextlib.suppress(BaseException):
                task.result()
        raise cancelled
