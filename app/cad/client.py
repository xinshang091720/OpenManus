"""AutoCAD COM client and environment manager for Windows desktop."""

from __future__ import annotations

import contextlib
from typing import Any, Generator, Optional
from app.logger import logger

try:
    import pythoncom
    import win32com.client
    import pywintypes
except ImportError:  # pragma: no cover
    pythoncom = None
    win32com = None
    pywintypes = None


class CadNotRunningError(RuntimeError):
    """Raised when AutoCAD application cannot be accessed or started."""


class CadNoActiveDocumentError(RuntimeError):
    """Raised when AutoCAD is running but has no open drawing document."""


def APoint(x: float, y: float, z: float = 0.0) -> Any:
    """Helper to convert coordinates into a COM-compatible double array VARIANT.

    AutoCAD COM methods (AddLine, AddCircle, etc.) require 3-element double arrays.
    """
    if pythoncom is None or win32com is None:
        return [float(x), float(y), float(z)]
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(x), float(y), float(z)]
    )


class CadClient:
    """Manages connection and interaction with local AutoCAD instance via Windows COM."""

    def __init__(self) -> None:
        self._acad: Any = None

    @staticmethod
    def initialize_thread() -> None:
        """Initialize COM for the current thread."""
        if pythoncom is not None:
            try:
                pythoncom.CoInitialize()
            except Exception:
                pass

    @staticmethod
    def uninitialize_thread() -> None:
        """Uninitialize COM for the current thread."""
        if pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def get_application(self, auto_launch: bool = True) -> Any:
        """Attach to the running AutoCAD instance or start one if requested."""
        if win32com is None:
            raise RuntimeError("win32com is not available in the current environment.")

        self.initialize_thread()

        # 1. Try to get currently running AutoCAD instance
        try:
            acad = win32com.client.GetActiveObject("AutoCAD.Application")
            self._acad = acad
            return acad
        except Exception:
            pass

        # 2. Try Dispatch if GetActiveObject didn't find active running one
        try:
            acad = win32com.client.Dispatch("AutoCAD.Application")
            acad.Visible = True
            self._acad = acad
            return acad
        except Exception as error:
            if not auto_launch:
                raise CadNotRunningError(
                    f"未检测到运行中的 AutoCAD 实例，且无法启动：{error}"
                ) from error
            raise CadNotRunningError(
                f"未能连接或启动 AutoCAD 应用程序：{error}"
            ) from error

    def get_active_document(self, auto_create_if_empty: bool = False) -> Any:
        """Get the active drawing document in AutoCAD."""
        acad = self.get_application()
        try:
            doc = acad.ActiveDocument
            if doc is None:
                raise AttributeError("ActiveDocument is None")
            return doc
        except Exception as error:
            if auto_create_if_empty:
                try:
                    logger.info("当前无活动图纸，正在新建一张空白图纸...")
                    return acad.Documents.Add()
                except Exception as add_err:
                    raise CadNoActiveDocumentError(
                        f"AutoCAD 当前没有打开任何图纸，且自动新建失败：{add_err}"
                    ) from add_err
            raise CadNoActiveDocumentError(
                "AutoCAD 当前没有打开任何图纸。请在 AutoCAD 中打开或新建一张图纸后再试。"
            ) from error

    @contextlib.contextmanager
    def silent_mode(self, doc: Optional[Any] = None) -> Generator[None, None, None]:
        """Context manager to temporarily suppress dialog popups (FILEDIA=0, CMDDIA=0)."""
        target_doc = doc or self.get_active_document()
        old_filedia = None
        old_cmddia = None

        try:
            try:
                old_filedia = target_doc.GetVariable("FILEDIA")
                target_doc.SetVariable("FILEDIA", 0)
            except Exception:
                pass

            try:
                old_cmddia = target_doc.GetVariable("CMDDIA")
                target_doc.SetVariable("CMDDIA", 0)
            except Exception:
                pass

            yield
        finally:
            if old_filedia is not None:
                try:
                    target_doc.SetVariable("FILEDIA", old_filedia)
                except Exception:
                    pass
            if old_cmddia is not None:
                try:
                    target_doc.SetVariable("CMDDIA", old_cmddia)
                except Exception:
                    pass
