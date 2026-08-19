import asyncio

from app.mcp.revit_lock import RevitProcessLock


def test_windows_process_lock_releases_from_its_own_worker_thread():
    async def scenario():
        original_name = RevitProcessLock._MUTEX_NAME
        RevitProcessLock._MUTEX_NAME = r"Local\OpenManusRevitPluginApiLockTest"
        first = RevitProcessLock()
        second = RevitProcessLock()
        try:
            async with first.hold():
                pass
            # This would hang if the first mutex was not released correctly.
            holder = second.hold()
            await asyncio.wait_for(holder.__aenter__(), timeout=2)
            await holder.__aexit__(None, None, None)
        finally:
            first.close()
            second.close()
            RevitProcessLock._MUTEX_NAME = original_name

    asyncio.run(scenario())
