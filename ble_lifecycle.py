"""Bounded, cancellation-resistant best-effort BLE cleanup.

Completion proves only that backend calls returned, never hardware safety.
"""
import asyncio

from madoka_protocol import NOTIFY_CHAR

CLEANUP_TIMEOUT = 10.0  # per backend call, not a hardware guarantee


async def cleanup_client(client):
    async def cleanup():
        errors = []
        if client.is_connected:
            try:
                await asyncio.wait_for(client.stop_notify(NOTIFY_CHAR), CLEANUP_TIMEOUT)
            except Exception as exc:
                errors.append(f"stop_notify: {type(exc).__name__}: {exc}")
        # Also try disconnect after a partially failed connect or stop_notify.
        try:
            await asyncio.wait_for(client.disconnect(), CLEANUP_TIMEOUT)
        except Exception as exc:
            errors.append(f"disconnect: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("BLE cleanup incomplete; physical state unverified: "
                               + "; ".join(errors))

    task = asyncio.create_task(cleanup())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Repeated Ctrl+C/task cancellation must not orphan cleanup.
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve a possible exception without losing the caller's cancellation.
        if not task.cancelled() and task.exception() is not None:
            import logging
            logging.getLogger(__name__).error("%s", task.exception())
        raise asyncio.CancelledError
    task.result()
