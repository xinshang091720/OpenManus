import os
import time
from dataclasses import dataclass, field
import win32gui
import win32con
import win32process
import requests
from q_agent_function_module.ohresult.logger_config import setup_logger
logger = setup_logger()


_SAVE_DIALOG_KEYWORDS = ("另存为", "导出IFC", "Export IFC")
_EXPORT_DIALOG_TITLES = {"导出模型", "模型压缩"}
_FINAL_EXPORT_TEXT = ("导出已完成", "导出成功", "导出完成")
_SAVE_BUTTON_LABELS = {"保存(S)", "保存(&S)", "保存"}
_CONFIRM_BUTTON_LABELS = {"确认", "确定", "OK"}


@dataclass
class RevitIfcExportDialogState:
    """Thread-shared, read-only progress state for one Revit IFC export."""

    initial_save_confirmed: bool = False
    final_confirmation_clicked: bool = False
    final_completion_seen: bool = False
    final_confirmation_resolved: bool = False
    final_completion_window_handle: int | None = None
    saw_export_dialog: bool = False
    matching_dialog_visible: bool = False
    initial_wait_expired: bool = False
    final_confirmation_at: float | None = None
    last_dialog_seen_at: float | None = None
    target_process_id: int | None = None
    baseline_window_handles: set[int] = field(default_factory=set)
    # A progress window can reuse its HWND for the final confirmation. Record
    # its original text so that reuse is accepted without clicking a stale
    # completion popup that existed before this export.
    baseline_window_texts: dict[int, tuple[str, ...]] = field(default_factory=dict)
    # Some SZ-IFC converter builds host the final modal dialog in a helper
    # process even though the command was started from Revit.  This records all
    # visible HWNDs before the request so only a *new*, exact completion popup
    # can use the compatibility path.
    external_plugin_baseline_window_handles: set[int] | None = None


def snapshot_process_window_handles(process_id):
    """Return visible top-level HWNDs owned by one process."""
    handles = set()

    def visit(hwnd, _):
        try:
            _, owner = win32process.GetWindowThreadProcessId(hwnd)
            if owner == process_id and win32gui.IsWindowVisible(hwnd):
                handles.add(hwnd)
        except Exception:
            return

    win32gui.EnumWindows(visit, None)
    return handles


def snapshot_visible_window_handles():
    """Return visible top-level HWNDs before an ExportIFC request starts."""
    handles = set()

    def visit(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                handles.add(hwnd)
        except Exception:
            return

    win32gui.EnumWindows(visit, None)
    return handles


def _window_child_texts(hwnd):
    texts = []

    def visit(child_hwnd, _):
        text = win32gui.GetWindowText(child_hwnd).strip()
        if text:
            texts.append(text)

    try:
        win32gui.EnumChildWindows(hwnd, visit, None)
    except Exception as error:
        logger.debug(f"Unable to enumerate export dialog text: {error}")
    return texts


def snapshot_process_window_texts(process_id):
    """Return visible window text for one process before an export begins."""
    windows = {}

    def visit(hwnd, _):
        try:
            _, owner = win32process.GetWindowThreadProcessId(hwnd)
            if owner != process_id or not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            windows[hwnd] = tuple([title, *_window_child_texts(hwnd)])
        except Exception:
            return

    win32gui.EnumWindows(visit, None)
    return windows


def _click_button_with_labels(hwnd, labels, *, allow_idok=False):
    clicked = []

    def visit(child_hwnd, _):
        if clicked:
            return
        text = win32gui.GetWindowText(child_hwnd).strip()
        if text in labels:
            win32gui.SendMessage(child_hwnd, win32con.BM_CLICK, 0, 0)
            clicked.append(child_hwnd)

    try:
        win32gui.EnumChildWindows(hwnd, visit, None)
        if clicked:
            logger.info(f"Clicked Revit export dialog button through Win32 (HWND={clicked[0]})")
            return True
    except Exception as error:
        logger.debug(f"Win32 export button lookup failed: {error}")

    if not allow_idok:
        return False
    try:
        ok_hwnd = win32gui.GetDlgItem(hwnd, 1)
        if ok_hwnd:
            win32gui.SendMessage(ok_hwnd, win32con.BM_CLICK, 0, 0)
            logger.info("Clicked scoped Revit export dialog IDOK button")
            return True
    except Exception as error:
        logger.debug(f"Scoped Revit IDOK click failed: {error}")
    return False


def _is_final_export_message(text):
    """Return true only for the converter's terminal completion sentence.

    The converter first reports messages such as ``导出已完成，正在生成统计报告``.
    That is still a progress state: closing it prevents the later modal
    ``导出已完成！`` confirmation from being handled.  Match a complete sentence,
    rather than a substring, while accepting harmless trailing punctuation.
    """
    normalized = "".join(str(text or "").split())
    return normalized in _FINAL_EXPORT_TEXT or any(
        normalized == f"{marker}{punctuation}"
        for marker in _FINAL_EXPORT_TEXT
        for punctuation in ("！", "!", "。", ".")
    )


def _contains_final_export_text(texts):
    return any(_is_final_export_message(text) for text in texts)


def _click_scoped_final_confirmation(hwnd):
    """Close only a completion dialog already proven to belong to this export."""
    # Revit can leave the modal completion dialog behind its main window.  The
    # click itself remains HWND-scoped, but restoring/focusing this verified
    # dialog preserves the reliable behaviour of the earlier implementation
    # for UI Automation fallbacks.
    try:
        minimized_before_restore = bool(win32gui.IsIconic(hwnd))
    except Exception:
        minimized_before_restore = None
    logger.info(
        "Handling Revit IFC completion dialog HWND=%s minimized_before_restore=%s",
        hwnd,
        minimized_before_restore,
    )
    try:
        restored = bool(win32gui.ShowWindow(hwnd, win32con.SW_RESTORE))
    except Exception as error:
        restored = False
        logger.debug(f"Unable to restore scoped Revit completion dialog: {error}")
    try:
        foreground = bool(win32gui.SetForegroundWindow(hwnd))
    except Exception as error:
        foreground = False
        logger.debug(f"Unable to foreground scoped Revit completion dialog: {error}")
    # The converter's final window can be created minimized.  The previous
    # working implementation waited briefly after restoring it before sending
    # BM_CLICK; without this, the button may not have been enabled yet.
    time.sleep(0.5)
    try:
        minimized_after_restore = bool(win32gui.IsIconic(hwnd))
    except Exception:
        minimized_after_restore = None
    logger.info(
        "Revit IFC completion dialog restored=%s foreground=%s minimized_after_restore=%s HWND=%s",
        restored,
        foreground,
        minimized_after_restore,
        hwnd,
    )
    if _click_button_with_labels(hwnd, _CONFIRM_BUTTON_LABELS, allow_idok=True):
        logger.info("Revit IFC completion confirmation clicked via scoped Win32 HWND=%s", hwnd)
        return True
    # Preserve the older, more tolerant UI Automation path.  It is safe here
    # because the caller already matched the target Revit PID and an exact
    # completion message inside this HWND; unlike the legacy global scan it
    # cannot click another application's confirmation button.
    clicked = _try_click_confirm_button(hwnd)
    if clicked:
        logger.info("Revit IFC completion confirmation clicked via legacy UI fallback HWND=%s", hwnd)
        return True

    # A missing standard button must not be treated as permission to close the
    # window.  In particular, the converter can expose an intermediate
    # "正在生成统计报告" window with the same title as the final modal.  Keep
    # monitoring so that the actual confirmation can be clicked later, or the
    # user can close that exact final modal themselves.
    logger.info(
        "Revit IFC completion dialog has no enumerable confirmation button; "
        "continuing to wait HWND=%s child_texts=%s",
        hwnd,
        _window_child_texts(hwnd),
    )
    return False


def monitor_revit_ifc_export_dialogs(
    state,
    cancel_event,
    *,
    initial_wait_seconds=30,
    poll_interval_seconds=0.5,
):
    """Drive only Revit's IFC save and final-confirmation dialogs for one export.

    This deliberately stays alive after the first Save click.  Revit can return
    from the HTTP request before native export windows appear, and the final
    confirmation is shown only after the export/compression progress windows.
    No global keyboard input is ever sent by this monitor.
    """
    started_at = time.monotonic()
    initial_wait_logged = False

    while not cancel_event.is_set():
        matching = []
        completed_window_visible = False

        def enum_cb(hwnd, extra):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            class_name = win32gui.GetClassName(hwnd)
            texts = tuple([title, *_window_child_texts(hwnd)])
            is_completed = _contains_final_export_text(texts)
            if state.target_process_id is not None:
                try:
                    _, owner = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    return
                if owner != state.target_process_id:
                    # Compatibility for an SZ-IFC converter helper process:
                    # it must be a new window, retain the known export-dialog
                    # title, and contain the exact completion text.  This is
                    # deliberately not used for Save/progress dialogs and
                    # cannot affect any window that existed before ExportIFC.
                    external_baseline = state.external_plugin_baseline_window_handles
                    if (
                        external_baseline is None
                        or hwnd in external_baseline
                        or title not in _EXPORT_DIALOG_TITLES
                        or not is_completed
                    ):
                        return
                    logger.info(
                        "Detected external SZ-IFC converter completion dialog "
                        "PID=%s HWND=%s title=%s",
                        owner,
                        hwnd,
                        title,
                    )
                    extra.append((hwnd, title, "final", texts))
                    return
            if hwnd in state.baseline_window_handles:
                baseline_texts = state.baseline_window_texts.get(hwnd, ())
                if not is_completed or _contains_final_export_text(baseline_texts):
                    return
            is_save = class_name == "#32770" and any(
                keyword in title for keyword in _SAVE_DIALOG_KEYWORDS
            )
            if is_save or title in _SAVE_DIALOG_KEYWORDS:
                extra.append((hwnd, title, "save", texts))
            elif is_completed:
                extra.append((hwnd, title, "final", texts))
            elif any(export_title in title for export_title in _EXPORT_DIALOG_TITLES):
                extra.append((hwnd, title, "export", texts))

        try:
            win32gui.EnumWindows(enum_cb, matching)
        except Exception as error:
            logger.debug(f"Unable to enumerate Revit export dialogs: {error}")

        state.matching_dialog_visible = bool(matching)
        if matching:
            now = time.monotonic()
            state.saw_export_dialog = True
            state.last_dialog_seen_at = now

        for hwnd, title, kind, texts in matching:
            if kind == "save":
                if not state.initial_save_confirmed and _click_button_with_labels(
                    hwnd, _SAVE_BUTTON_LABELS
                ):
                    state.initial_save_confirmed = True
                continue

            # "导出模型" and "模型压缩" may be progress dialogs. Only click a
            # scoped confirmation when the window itself says export completed.
            is_completed = _contains_final_export_text(texts)
            if is_completed:
                completed_window_visible = True
                state.final_completion_seen = True
                state.final_completion_window_handle = hwnd
            if (
                not state.final_confirmation_clicked
                and is_completed
                and _click_scoped_final_confirmation(hwnd)
            ):
                state.final_confirmation_clicked = True
                state.final_confirmation_resolved = True
                state.final_confirmation_at = time.monotonic()
                logger.info("Revit IFC export final confirmation clicked")
                return state

        # The user may click the exact completion dialog before automation
        # reaches its button.  Seeing that scoped dialog and then observing it
        # disappear is also a valid resolution; a file alone is not.
        if state.final_completion_seen and not completed_window_visible:
            state.final_confirmation_resolved = True
            state.final_confirmation_at = state.final_confirmation_at or time.monotonic()
            logger.info("Revit IFC export final confirmation was resolved by the user")
            return state

        if (
            not initial_wait_logged
            and time.monotonic() - started_at >= initial_wait_seconds
            and not state.initial_save_confirmed
        ):
            # Keep monitoring. Some plugin builds acknowledge HTTP before the
            # native Save dialog becomes visible.
            state.initial_wait_expired = True
            initial_wait_logged = True
            logger.warning("Revit IFC save dialog was not visible within the initial wait; continuing to monitor")

        time.sleep(poll_interval_seconds)

    logger.info("Revit IFC export dialog monitor stopped")
    return state


def _press_enter_fallback():
    """Use the legacy keyboard fallback only when that mode explicitly asks for it."""
    try:
        import pyautogui
    except ImportError:
        logger.warning("pyautogui is unavailable; skipping the legacy Enter fallback")
        return False
    pyautogui.press("enter")
    return True


def _try_click_confirm_button(hwnd):
    """
    尝试点击指定 HWND 弹窗里的确认类按钮。
    返回 True 表示成功点击，False 表示未找到按钮（可能是进度弹窗）。
    """
    # 1. Win32 枚举子窗口按钮 — 只按文本匹配，不按 class_name
    # ponytail: class_name 含 "button" 太宽泛，Windows 文件对话框有几十个
    # 这类子窗口（browse, 组织, min/max/close），会误点。
    _CONFIRM_LABELS = {"确认", "确定", "保存(S)", "保存(&S)", "保存", "OK"}
    clicked = []
    def visit(child_hwnd, _):
        txt = win32gui.GetWindowText(child_hwnd).strip()
        
        # Also log any static text we see, as it might be an error message
        class_name = win32gui.GetClassName(child_hwnd)
        if class_name == "Static" and txt:
            logger.info(f"📄 弹窗内容文本: {txt}")
            
        if txt in _CONFIRM_LABELS:
            win32gui.SendMessage(child_hwnd, win32con.BM_CLICK, 0, 0)
            clicked.append(child_hwnd)
    try:
        win32gui.EnumChildWindows(hwnd, visit, None)
        if clicked:
            logger.info(f"🤖 已通过 Win32 BM_CLICK 成功点击确认按钮 (HWND={clicked[0]})")
            return True
    except Exception as e:
        logger.debug(f"Win32 枚举子窗口失败: {e}")

    # 2. UIA 方式查找 Button 控件（同样只按文本匹配）
    try:
        # UIA is an optional fallback.  The initial-save path normally succeeds
        # through Win32 and must not make importing this module depend on UIA.
        from pywinauto import Application
        app = Application(backend="uia").connect(handle=hwnd)
        dlg = app.window(handle=hwnd)
        for btn in dlg.children(control_type="Button"):
            if btn.window_text().strip() in _CONFIRM_LABELS:
                try:
                    btn.click_input()
                except Exception:
                    btn.invoke()
                logger.info(f"🤖 已通过 pywinauto 成功点击按钮 [{btn.window_text()}]")
                return True
    except Exception as e:
        logger.debug(f"UIA 按钮定位失败: {e}")

    # 3. 针对“导出已完成！”对话框，尝试直接向 HWND 发送 IDOK (1) 指令
    try:
        ok_hwnd = win32gui.GetDlgItem(hwnd, 1)
        if ok_hwnd:
            win32gui.SendMessage(ok_hwnd, win32con.BM_CLICK, 0, 0)
            logger.info("🤖 已通过 GetDlgItem(1) 发送 BM_CLICK 点击确认按钮")
            return True
    except Exception:
        pass

    return False


def auto_close_export_dialog(
    wait_timeout=10,
    fallback_enter=False,
    *,
    initial_save_only=False,
    cancel_event=None,
):
    """
    查找并关闭 Revit IFC 导出流程中的所有弹窗：
      1. Revit 保存路径确认弹窗（出现在导出开始时，标题为"导出IFC"/"另存为"）
      2. 【导出模型】进度弹窗 × 2（无按钮，跳过等待）
      3. 【导出模型】最终确认弹窗（含"确认"按钮 / "导出已完成！"）

    ``initial_save_only=True`` 仅处理导出刚开始时的“导出IFC/另存为”
    保存确认窗口。该模式绝不发送全局 Enter，也不会触碰“导出模型”进度或
    完成窗口，供 ExportIFC 正在等待保存确认时安全地并行调用。

    策略：找到窗口 → 先尝试 UIA/Win32 点按钮 → 默认模式下保存弹窗兜底发 Enter。
    """
    logger.info("⏳ 正在尝试捕捉并关闭【导出模型/保存路径】弹窗...")
    start_time = time.time()

    SAVE_DIALOG_KEYWORDS = ("另存为", "导出IFC", "Export IFC")
    EXPORT_DIALOG_TITLES = {"导出模型"}
    IGNORE_KEYWORDS = ("Antigravity", "Visual Studio", "VS Code", "Chrome", "Edge", "Firefox", "OpenManus", "IDE", "analysis_export")

    while time.time() - start_time < wait_timeout:
        if cancel_event is not None and cancel_event.is_set():
            logger.info("导出保存确认任务已取消")
            return False
        found_hwnds = []

        def enum_cb(h, extra):
            if not win32gui.IsWindowVisible(h):
                return
            title = win32gui.GetWindowText(h).strip()
            if not title:
                return
            # 排除 IDE、编辑器、浏览器主窗口
            if any(ignore.lower() in title.lower() for ignore in IGNORE_KEYWORDS):
                return
            class_name = win32gui.GetClassName(h)
            is_save_dialog = class_name == "#32770" and any(
                kw in title for kw in SAVE_DIALOG_KEYWORDS
            )
            if is_save_dialog or any(kw == title for kw in SAVE_DIALOG_KEYWORDS):
                extra.append((h, title, class_name))
            elif not initial_save_only and title in EXPORT_DIALOG_TITLES:
                extra.append((h, title, class_name))

        win32gui.EnumWindows(enum_cb, found_hwnds)

        if found_hwnds:
            hwnd, title, class_name = found_hwnds[0]
            logger.info(f"🔍 Win32 找到弹窗: 「{title}」 HWND={hwnd} Class={class_name}")

            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception as error:
                logger.warning(f"SetForegroundWindow 提示: {error}")
            time.sleep(0.5)

            if _try_click_confirm_button(hwnd):
                return True

            if initial_save_only:
                # Native save controls can be populated a moment after the
                # dialog becomes visible. Keep polling this exact dialog;
                # never fall back to a global keyboard action.
                logger.info("保存确认窗口已找到，但确认按钮尚未就绪")
                time.sleep(1)
                continue

            # 如果没有找到按钮：
            # 如果是 Save 路径弹窗（#32770 或 导出IFC/另存为），尝试发送 Enter 关闭保存对话框
            if class_name == "#32770" or any(kw in title for kw in SAVE_DIALOG_KEYWORDS):
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_COMMAND, 1, 0)
                except Exception:
                    pass
                _press_enter_fallback()
                logger.info("🤖 保存路径弹窗：发送 IDOK / 全局 Enter 尝试确认保存")
                return True
            else:
                # 进度弹窗（无按钮），不发 Enter，继续等待
                logger.info(f"⏳ 弹窗「{title}」暂无确认按钮，判定为进度等待中...")

        time.sleep(1)

    logger.warning("⚠️ 未能准确定位导出弹窗")
    if fallback_enter and not initial_save_only:
        _press_enter_fallback()
    else:
        logger.info("No export dialog found; leaving the foreground window unchanged")
    return False


def get_unique_ifc_path(folder_path, base_name):
    count = 0
    while True:
        if count == 0:
            ifc_filename = f"{base_name}.ifc"
        else:
            ifc_filename = f"{base_name}({count}).ifc"

        full_ifc_path = os.path.join(folder_path, ifc_filename)
        if not os.path.exists(full_ifc_path):
            return full_ifc_path
        count += 1


def wait_for_files_generated(ifc_path, timeout=None, check_interval=10):
    if timeout is None or timeout == 7200 or timeout <= 0:
        timeout = int(os.environ.get("BEESYNC_DELIVERY_TIMEOUT_SECONDS", "86400"))
    xlsx_path = f"{ifc_path}.xlsx"
    start_time = time.time()
    logger.info(f"等待文件落地生成: \n  1) {ifc_path}\n  2) {xlsx_path}")

    while time.time() - start_time < timeout:
        ifc_exists = os.path.exists(ifc_path)
        xlsx_exists = os.path.exists(xlsx_path)

        if ifc_exists and xlsx_exists:
            logger.info("✅ 检测到 .ifc 和 .ifc.xlsx 文件均已成功生成落地！")
            time.sleep(2)
            auto_close_export_dialog(wait_timeout=30, fallback_enter=False)
            return True

        time.sleep(check_interval)

    logger.error(f"⏰ 等待超时（{timeout}s）：未检测到完整的导出文件 (.ifc / .ifc.xlsx)")
    return False


def call_export_ifc_api(ifc_path):
    url = "http://localhost:5000/api/RevitApi/ExportIFC"
    headers = {"content-type": "application/json"}
    payload = {"IfcFilePath": ifc_path}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=300)
        res_data = response.json()
        code = res_data.get("code")
        msg = res_data.get("msg", "")

        if code == 200:
            logger.info("接口调用成功 (Code: 200)，开始校验实际文件是否生成...")
            return wait_for_files_generated(ifc_path)
        elif code == 401:
            logger.error("❌ 错误: 未安装SZ-IFC转换插件 ")
            return "未安装SZ-IFC转换插件"
        else:
            logger.error(f"⚠️ 处理失败 (Code: {code}): {msg}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"网络/接口请求异常: {e}")
        return False


def process_rvt_files(folder_path):
    if not os.path.exists(folder_path):
        logger.error(f"错误：指定的路径 '{folder_path}' 不存在。")
        return
    if not os.path.isdir(folder_path):
        logger.error(f"错误：'{folder_path}' 不是一个有效的文件夹目录。")
        return

    for filename in os.listdir(folder_path):
        if filename.lower().endswith('.rvt'):
            base_name = os.path.splitext(filename)[0]
            full_ifc_path = get_unique_ifc_path(folder_path, base_name)
            call_export_ifc_api(full_ifc_path)


if __name__ == "__main__":
    user_directory = r"D:\project\ai_agent\data\revit_ifc"
    result_paths = process_rvt_files(user_directory)
