"""Cross-process serialization for the single-threaded local Revit plugin."""

import asyncio
import contextvars
import ctypes
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import os


_lock_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "revit_process_lock_depth", default=0
)


class RevitProcessLock:
    """A Windows named mutex shared by every local Revit MCP bridge process."""

    _MUTEX_NAME = r"Local\OpenManusRevitPluginApiLock"
    _WAIT_OBJECT_0 = 0
    _WAIT_ABANDONED = 0x00000080
    _INFINITE = 0xFFFFFFFF

    def __init__(self) -> None:
        self._fallback = asyncio.Lock()
        self._handle = None
        self._mutex_executor = None
        if os.name == "nt":
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel32.CreateMutexW.argtypes = [
                ctypes.c_void_p,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            ]
            self._kernel32.CreateMutexW.restype = ctypes.c_void_p
            self._kernel32.WaitForSingleObject.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
            ]
            self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
            self._kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
            self._kernel32.ReleaseMutex.restype = wintypes.BOOL
            self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._kernel32.CloseHandle.restype = wintypes.BOOL
            self._handle = self._kernel32.CreateMutexW(None, False, self._MUTEX_NAME)
            if not self._handle:
                raise OSError(ctypes.get_last_error(), "Unable to create Revit mutex")
            # A Windows mutex is owned by an OS thread.  Acquire and release
            # therefore must use this same one-worker executor; asyncio's
            # default thread pool does not make that guarantee.
            self._mutex_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="revit-mutex"
            )

    def _acquire_windows_mutex(self) -> None:
        result = self._kernel32.WaitForSingleObject(self._handle, self._INFINITE)
        if result not in (self._WAIT_OBJECT_0, self._WAIT_ABANDONED):
            raise OSError(ctypes.get_last_error(), "Unable to acquire Revit mutex")

    def _release_windows_mutex(self) -> None:
        if not self._kernel32.ReleaseMutex(self._handle):
            raise OSError(ctypes.get_last_error(), "Unable to release Revit mutex")

    @asynccontextmanager
    async def hold(self):
        # MCPServer holds this lock around every Revit tool.  Workflows also
        # use it when invoked directly.  A second named-mutex acquisition from
        # a different executor thread would otherwise block forever, so nested
        # calls in the same async request are deliberately re-entrant.
        depth = _lock_depth.get()
        if depth:
            token = _lock_depth.set(depth + 1)
            try:
                yield
            finally:
                _lock_depth.reset(token)
            return
        if self._handle is None:
            async with self._fallback:
                token = _lock_depth.set(1)
                try:
                    yield
                finally:
                    _lock_depth.reset(token)
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._mutex_executor, self._acquire_windows_mutex)
        token = _lock_depth.set(1)
        try:
            yield
        finally:
            _lock_depth.reset(token)
            await loop.run_in_executor(self._mutex_executor, self._release_windows_mutex)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._mutex_executor:
            self._mutex_executor.shutdown(wait=False, cancel_futures=True)
            self._mutex_executor = None
