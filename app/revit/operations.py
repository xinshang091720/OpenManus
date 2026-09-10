"""Waiting policy for long-running calls to the local Revit plugin."""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Awaitable, Callable

from app.logger import logger
from app.revit.client import REVIT_OPERATION_TIMEOUT_SECONDS


class RevitOperationUnknown(RuntimeError):
    """A Revit operation ended without a trustworthy final model state."""


async def call_revit_operation(
    client: Any,
    operation_name: str,
    method: Callable[..., Awaitable[dict[str, Any]]],
    *args: Any,
    heartbeat_interval_seconds: float = 30.0,
    timeout_seconds: float = REVIT_OPERATION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Wait for one plugin call without retrying a mutation. When timeout_seconds <= 0, waits indefinitely with heartbeats."""
    parameters = inspect.signature(method).parameters
    kwargs = {"wait_forever": True} if "wait_forever" in parameters else {}
    task = asyncio.create_task(method(*args, **kwargs))
    started = time.monotonic()

    while True:
        elapsed = time.monotonic() - started
        if timeout_seconds and timeout_seconds > 0:
            remaining = timeout_seconds - elapsed
            if remaining <= 0:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise RevitOperationUnknown(
                    f"{operation_name} exceeded {timeout_seconds:g} seconds; "
                    "the final Revit state is unknown"
                )
            wait_timeout = min(heartbeat_interval_seconds, remaining)
        else:
            # ponytail: timeout_seconds <= 0 disables timeout ceiling for low-spec long runs.
            wait_timeout = heartbeat_interval_seconds

        done, _ = await asyncio.wait(
            {task}, timeout=wait_timeout
        )
        if done:
            return await task
        logger.info(
            "Revit operation {} is still running (elapsed_seconds={})",
            operation_name,
            int(time.monotonic() - started),
        )
