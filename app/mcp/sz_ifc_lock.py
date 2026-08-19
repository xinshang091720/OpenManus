"""Cross-process serialization for one local SZ-IFC desktop instance."""

import asyncio
import contextvars
import ctypes
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import os


_lock_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "sz_ifc_process_lock_depth", default=0
)


class SzIfcProcessLock:
    _MUTEX_NAME = r"Local\OpenManusSzIfcDesktopLock"
    _WAIT_OBJECT_0 = 0
    _WAIT_ABANDONED = 0x00000080
    _INFINITE = 0xFFFFFFFF

    def __init__(self) -> None:
        self._fallback = asyncio.Lock()
        self._handle = None
        self._executor = None
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
                raise OSError(ctypes.get_last_error(), "Unable to create SZ-IFC mutex")
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="sz-ifc-mutex"
            )

    def _acquire(self) -> None:
        result = self._kernel32.WaitForSingleObject(self._handle, self._INFINITE)
        if result not in (self._WAIT_OBJECT_0, self._WAIT_ABANDONED):
            raise OSError(ctypes.get_last_error(), "Unable to acquire SZ-IFC mutex")

    def _release(self) -> None:
        if not self._kernel32.ReleaseMutex(self._handle):
            raise OSError(ctypes.get_last_error(), "Unable to release SZ-IFC mutex")

    @asynccontextmanager
    async def hold(self):
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
        acquire = loop.run_in_executor(self._executor, self._acquire)
        try:
            await asyncio.shield(acquire)
        except asyncio.CancelledError:
            # The OS wait continues in its worker even when the asyncio waiter
            # is cancelled.  Let it acquire and immediately release so an
            # abandoned cancellation cannot strand the named mutex.
            await asyncio.shield(acquire)
            await loop.run_in_executor(self._executor, self._release)
            raise
        token = _lock_depth.set(1)
        try:
            yield
        finally:
            _lock_depth.reset(token)
            await loop.run_in_executor(self._executor, self._release)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
