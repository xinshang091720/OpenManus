"""PID-scoped SZ-IFC inspection and DOCX report export automation."""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import requests
import win32gui
import win32process
from pywinauto import Application
from pywinauto.keyboard import send_keys

from q_agent_function_module.ohresult.logger_config import setup_logger
from app.tool_progress import set_tool_progress


logger = setup_logger()


class SzIfcUserActionRequired(RuntimeError):
    """The desktop state has a real ambiguity that cannot be chosen safely."""


class SzIfcInspectionCancelled(RuntimeError):
    """The user cancelled while automation was waiting for a safe next action."""

_RULE_BY_PROFESSION = {
    "建筑": "建筑工程BIM设计交付标准-施工图设计阶段_建筑",
    "总图": "建筑工程BIM设计交付标准-施工图设计阶段_总图",
    "电气": "建筑工程BIM设计交付标准-施工图设计阶段_电气_智能化",
    "结构": "建筑工程BIM设计交付标准-施工图设计阶段_结构_钢结构_装配式混凝土",
    "给排水": "建筑工程BIM设计交付标准-施工图设计阶段_给排水",
    "通风空调": "建筑工程BIM设计交付标准-施工图设计阶段_通风空调_燃气",
    "燃气": "建筑工程BIM设计交付标准-施工图设计阶段_通风空调_燃气",
}


def _cbims_windows(process_id=None):
    windows = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        class_name = win32gui.GetClassName(hwnd)
        if not class_name.startswith("HwndWrapper[CBIMS.Manager;;"):
            return
        _, owner = win32process.GetWindowThreadProcessId(hwnd)
        if process_id is not None and owner != process_id:
            return
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        windows.append(
            {
                "handle": hwnd,
                "pid": owner,
                "title": win32gui.GetWindowText(hwnd).strip(),
                "area": max(0, right - left) * max(0, bottom - top),
            }
        )

    win32gui.EnumWindows(visit, None)
    return windows


def _connect_window(handle):
    app = Application(backend="uia").connect(handle=handle, timeout=5)
    window = app.window(handle=handle)
    window.wait("visible", timeout=5)
    return app, window


def _model_ready(window, ifc_filename, full_scan=False):
    model = window.child_window(title=ifc_filename, found_index=0)
    tab = window.child_window(title="模型检查", control_type="TabItem", found_index=0)
    if model.exists(timeout=0.2) and tab.exists(timeout=0.2) and tab.is_enabled():
        return True
    if not full_scan:
        return False
    model_visible = False
    tab_enabled = False
    for element in window.descendants():
        try:
            text = element.window_text().strip()
            if text == ifc_filename:
                model_visible = True
            if text == "模型检查" and element.is_enabled():
                tab_enabled = True
        except Exception:
            continue
    return model_visible and tab_enabled


def _find_prepared_sz_ifc_main_window(
    ifc_filename=None, process_id=None, *, full_scan=True
):
    """Return the unique prepared main window, preferring the requested model."""
    all_cbims = _cbims_windows(process_id)
    if not all_cbims:
        raise SzIfcUserActionRequired(
            f"未检测到已运行的 SZ-IFC 应用程序。请先启动 SZ-IFC，并在软件中手动加载目标 IFC 模型：{ifc_filename or ''}".strip()
        )

    main_windows = [
        item for item in all_cbims if item["title"] != "StartWindow"
    ]
    if ifc_filename:
        matches = []
        for item in main_windows:
            try:
                _, window = _connect_window(item["handle"])
                if _model_ready(window, ifc_filename, full_scan=full_scan):
                    matches.append(item["handle"])
            except Exception:
                continue
        if len(matches) > 1:
            raise SzIfcUserActionRequired(
                "检测到多个 SZ-IFC 实例加载了同一个 IFC，无法唯一确定质检窗口。"
            )
        if matches:
            return matches[0]
        raise SzIfcUserActionRequired(
            f"SZ-IFC 应用程序已打开，但尚未检测到加载目标 IFC 模型。请在 SZ-IFC 中手动加载：{ifc_filename}"
        )
    if main_windows:
        return max(main_windows, key=lambda item: item["area"])["handle"]
    if all_cbims:
        raise RuntimeError("检测到 SZ-IFC StartWindow，但尚未检测到模型主窗口。")
    raise RuntimeError("未检测到已打开的 SZ-IFC/CBIMS 主窗口。")


def _raise_if_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise SzIfcInspectionCancelled("用户已停止 SZ-IFC 质检自动化。")


def _wait(deadline, description, getter, interval=0.5, cancel_event=None):
    last_error = None
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancel_event)
        try:
            value = getter()
            if value:
                return value
        except (SzIfcUserActionRequired, SzIfcInspectionCancelled):
            raise
        except Exception as error:
            last_error = error
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    suffix = f"：{last_error}" if last_error else ""
    raise TimeoutError(f"等待{description}超时，SZ-IFC 最终状态未知{suffix}")


def _activate_window(handle, window=None):
    """Restore and bring SZ-IFC main window to foreground."""
    try:
        if win32gui.IsIconic(handle):
            win32gui.ShowWindow(handle, 9)  # SW_RESTORE
        else:
            win32gui.ShowWindow(handle, 5)  # SW_SHOW
        win32gui.SetForegroundWindow(handle)
        win32gui.BringWindowToTop(handle)
    except Exception as err:
        logger.debug(f"窗口置前辅助异常（继续使用 UI Automation 焦点）：{err}")
    if window is not None:
        try:
            window.set_focus()
        except Exception:
            pass


def _click_once(element, window=None, handle=None):
    if not element.is_enabled():
        raise RuntimeError(f"控件尚未启用：{element.window_text()}")
    if handle:
        _activate_window(handle, window)
    try:
        element.invoke()
        return
    except Exception:
        pass
    try:
        element.click_input()
    except Exception as e:
        logger.warning(f"click_input 失败，再次尝试 invoke: {e}")
        element.invoke()


def _wait_control(window, deadline, title, control_type=None, cancel_event=None):
    def locate():
        kwargs = {"title": title, "found_index": 0}
        if control_type:
            kwargs["control_type"] = control_type
        control = window.child_window(**kwargs)
        if control.exists(timeout=0.2) and control.is_visible() and control.is_enabled():
            return control
        return None

    return _wait(
        deadline,
        f"“{title}”控件启用",
        locate,
        cancel_event=cancel_event,
    )


def _version_key(text):
    match = re.search(r"V(\d+(?:\.\d+)*)", text, re.IGNORECASE)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def _rule_base(text):
    return re.sub(r"V\d+(?:\.\d+)*", "", text, flags=re.IGNORECASE).strip()


def _choose_rule(window, profession, exact_rule_name=None):
    desired = (exact_rule_name or _RULE_BY_PROFESSION.get(profession, profession)).strip()
    available = {}
    matched = {}
    for element in window.descendants():
        try:
            text = element.window_text().strip()
        except Exception:
            continue
        if not text:
            continue
        if "BIM" in text and ("交付" in text or "设计" in text):
            available[text] = element
        if exact_rule_name:
            if text == desired:
                matched[text] = element
        elif text.startswith(desired) or desired in text:
            matched[text] = element
    if not matched and not available:
        return None
    if not matched:
        return {
            "status": "selection_required",
            "message": f"未找到与“{desired}”唯一匹配的 SZ-IFC 规则，请选择规则。",
            "candidates": [
                {"display_name": text, "display_version": "SZ-IFC 规则", "rule_name": text}
                for text in sorted(available)
            ],
        }
    bases = {_rule_base(text) for text in matched}
    if len(bases) > 1:
        return {
            "status": "selection_required",
            "message": "检测到多个不同业务规则，请选择本次质检规则。",
            "candidates": [
                {"display_name": text, "display_version": "SZ-IFC 规则", "rule_name": text}
                for text in sorted(matched)
            ],
        }
    selected_text = max(matched, key=lambda text: (_version_key(text), text))
    return selected_text, matched[selected_text]


def _non_conflicting_report(path):
    requested = Path(path)
    requested.parent.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        suffix = "" if index == 0 else f"({index})"
        candidate = requested.with_name(f"{requested.stem}{suffix}{requested.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _top_level_windows():
    windows = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        windows.append(
            {
                "handle": hwnd,
                "pid": pid,
                "title": win32gui.GetWindowText(hwnd).strip(),
                "class_name": win32gui.GetClassName(hwnd),
            }
        )

    win32gui.EnumWindows(visit, None)
    return windows


def _window_related_to_target(window, target_pid, target_hwnd):
    if window["pid"] == target_pid:
        return True
    owner = window["handle"]
    for _ in range(8):
        owner = win32gui.GetWindow(owner, 4)  # GW_OWNER
        if not owner:
            return False
        if owner == target_hwnd:
            return True
        _, owner_pid = win32process.GetWindowThreadProcessId(owner)
        if owner_pid == target_pid:
            return True
    return False


def _connect_top_level_window(handle):
    app = Application(backend="uia").connect(handle=handle, timeout=3)
    window = app.window(handle=handle)
    window.wait("visible", timeout=3)
    return window


def _is_windows_save_dialog_title(title):
    """Match standard Windows save-dialog captions across supported shells.

    Windows 10/11 may expose the common file dialog as ``保存`` or as
    ``保存 在 <folder>``.  The latter is the caption shown by the native
    Explorer-style dialog and was inadvertently excluded by the stricter
    PID-scoped rewrite.
    """
    normalized = " ".join((title or "").strip().split())
    return bool(
        re.match(r"^(?:保存|另存为)(?:\s+在\b.*)?$", normalized)
        or re.match(r"^(?:Save As|Save)(?:\s+in\b.*)?$", normalized, re.IGNORECASE)
    )


def _save_dialog_file_name_edit(window):
    """Return the filename input of a native file dialog, if present."""
    # 1001 is used by classic dialogs; 1148 is used by Explorer-style dialogs
    # on some Windows builds.  Do not fall back to a generic Edit: that can be
    # the "search this folder" box and would save neither the report name nor
    # its target directory.
    for automation_id in ("1001", "1148"):
        edit = window.child_window(auto_id=automation_id, control_type="Edit")
        try:
            if edit.exists(timeout=0.1) and edit.is_visible() and edit.is_enabled():
                return edit
        except Exception:
            continue
    return None


def _save_dialog_has_save_button(window):
    button = window.child_window(
        title_re=r"^(?:保存|Save)(?:\(&S\)|\(S\))?$",
        control_type="Button",
        found_index=0,
    )
    try:
        return button.exists(timeout=0.1) and button.is_visible() and button.is_enabled()
    except Exception:
        return False


def _activate_dialog(item, dialog):
    """Bring a verified dialog forward before changing its filename/clicking."""
    try:
        win32gui.ShowWindow(item["handle"], 9)  # SW_RESTORE
        win32gui.SetForegroundWindow(item["handle"])
    except Exception:
        # Foreground restrictions are normal on Windows.  UI Automation focus
        # below is still sufficient when Windows rejects SetForegroundWindow.
        pass
    dialog.set_focus()


def _save_dialog(target_pid, target_hwnd, excluded_handles=None):
    excluded_handles = set(excluded_handles or ())
    related_matches = []
    native_dialog_matches = []
    for item in _top_level_windows():
        if item["handle"] in excluded_handles:
            continue
        if not _is_windows_save_dialog_title(item["title"]):
            continue
        if item["class_name"] != "#32770":
            continue
        try:
            window = _connect_top_level_window(item["handle"])
            if not _save_dialog_file_name_edit(window) or not _save_dialog_has_save_button(
                window
            ):
                continue
        except Exception:
            continue
        candidate = (item, window)
        native_dialog_matches.append(candidate)
        if _window_related_to_target(item, target_pid, target_hwnd):
            related_matches.append(candidate)

    # A common Explorer-style Save dialog can be hosted by another Windows
    # process, so it has no reliable owner/PID relationship to CBIMS.  It is
    # still safe to accept when it appeared only after this export click and is
    # the sole *real* native save dialog (filename input + enabled Save button).
    matches = related_matches or native_dialog_matches
    if len(matches) > 1:
        raise SzIfcUserActionRequired(
            "检测到多个本次出现的报告保存窗口，无法唯一确定本次导出窗口。"
        )
    return matches[0] if matches else None


def _success_dialog(target_pid, target_hwnd, excluded_handles=None):
    excluded_handles = set(excluded_handles or ())
    for item in _top_level_windows():
        if item["handle"] in excluded_handles:
            continue
        if not _window_related_to_target(item, target_pid, target_hwnd):
            continue
        try:
            window = _connect_top_level_window(item["handle"])
            texts = [window.window_text().strip()]
            texts.extend(
                element.window_text().strip() for element in window.descendants()
            )
        except Exception:
            continue
        if "导出成功" not in texts:
            continue
        for label in ("确定", "确认", "OK"):
            button = window.child_window(title=label, control_type="Button", found_index=0)
            if button.exists(timeout=0.1) and button.is_enabled():
                return window, button
    return None


def _docx_ready(path):
    try:
        with zipfile.ZipFile(path) as document:
            return document.testzip() is None and "word/document.xml" in document.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def _wait_for_stable_docx(path, deadline, cancel_event=None):
    last = None
    stable = 0
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancel_event)
        try:
            stat = path.stat()
            fingerprint = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            fingerprint = None
        if fingerprint and fingerprint == last:
            stable += 1
        elif fingerprint:
            last = fingerprint
            stable = 1
        else:
            last = None
            stable = 0
        if stable >= 3 and _docx_ready(path):
            return True
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    return False


def _legacy_save_report_dialog(main_window_handle, report_path, deadline, cancel_event):
    """Restore the broad save-dialog interaction used by the earlier EXE.

    SZ-IFC's native report dialog is inconsistent across Windows builds.  The
    earlier Runtime deliberately used the active dialog as a final fallback,
    then tried its filename Edit, a generic Edit, and focused keyboard input.
    Keep that behaviour for report export while the caller still validates the
    resulting DOCX before declaring success.
    """
    app = Application(backend="uia").connect(handle=main_window_handle, timeout=5)
    save_dialog = None
    for _ in range(15):
        _raise_if_cancelled(cancel_event)
        try:
            dialogs = app.windows(title_re=r".*保存.*|.*另存为.*")
            if dialogs:
                save_dialog = dialogs[0]
                break
        except Exception:
            pass
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))

    if save_dialog is None:
        # This is the historic compatibility path.  Some Explorer-style save
        # dialogs are not returned by app.windows even though they are active.
        save_dialog = app.top_window()

    try:
        save_dialog.wait("visible", timeout=10)
        save_dialog.set_focus()
    except Exception as error:
        logger.warning(f"报告保存窗口置前失败，继续使用旧版兼容输入：{error}")

    logger.info(f"旧版兼容：正在填入完整报告路径：{report_path}")
    filled = False
    for locator in (
        {"auto_id": "1001", "control_type": "Edit"},
        {"control_type": "Edit", "found_index": 0},
    ):
        try:
            edit = save_dialog.child_window(**locator)
            edit.set_edit_text(str(report_path))
            filled = True
            break
        except Exception:
            continue
    if not filled:
        send_keys("^a{BACKSPACE}")
        time.sleep(0.2)
        send_keys(str(report_path), with_spaces=True)

    time.sleep(0.8)
    try:
        save_dialog.child_window(
            title_re=r"保存.*", control_type="Button", found_index=0
        ).click_input()
    except Exception:
        send_keys("{ENTER}")
    logger.info("旧版兼容：已提交报告保存操作")

    for _ in range(15):
        _raise_if_cancelled(cancel_event)
        try:
            top_window = app.top_window()
            texts = [top_window.window_text()]
            texts.extend(element.window_text() for element in top_window.descendants())
            if any(
                marker in text
                for marker in ("导出成功", "导出已完成", "完成")
                for text in texts
            ):
                for label in ("确定", "确认", "OK"):
                    try:
                        button = top_window.child_window(
                            title=label, control_type="Button", found_index=0
                        )
                        if button.exists(timeout=0.1):
                            button.click_input()
                            logger.info(f"旧版兼容：已点击报告完成窗口“{label}”")
                            return
                    except Exception:
                        continue
                send_keys("{ENTER}")
                logger.info("旧版兼容：已确认报告完成窗口")
                return
        except Exception:
            pass
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))

    # This is intentionally retained from the earlier Runtime: after the
    # report save submission, acknowledge the active completion dialog once.
    send_keys("{ENTER}")


def run_sz_ifc_full_inspection(
    ifc_file_path,
    profession_keyword,
    output_docx_path=None,
    *,
    rule_name=None,
    timeout_seconds=7200,
    cancel_event=None,
):
    """Inspect an already loaded IFC and export one verified DOCX report."""
    started = time.monotonic()
    deadline = started + max(1, min(int(timeout_seconds), 7200))
    ifc_path = Path(ifc_file_path).resolve()
    if not ifc_path.is_file() or ifc_path.suffix.lower() != ".ifc":
        raise FileNotFoundError(f"输入的 IFC 文件不存在：{ifc_path}")
    requested = (
        Path(output_docx_path).resolve()
        if output_docx_path
        else ifc_path.with_name(f"{ifc_path.stem}_质检报告.docx")
    )
    report_path = _non_conflicting_report(requested)

    logger.info("[1/7] 正在定位包含目标 IFC 的 SZ-IFC 主窗口...")
    set_tool_progress("revit_inspect_ifc", "正在定位并连接 SZ-IFC 主窗口")
    scan_state = {"next_full": 0.0}

    def locate_model():
        now = time.monotonic()
        full_scan = now >= scan_state["next_full"]
        if full_scan:
            scan_state["next_full"] = now + 5
        return _find_prepared_sz_ifc_main_window(
            ifc_path.name, full_scan=full_scan
        )

    try:
        handle = _wait(
            deadline,
            "目标 IFC 模型加载",
            locate_model,
            interval=1,
            cancel_event=cancel_event,
        )
    except SzIfcUserActionRequired as error:
        return {
            "status": "user_action_required",
            "message": str(error),
            "ifc_path": str(ifc_path),
        }
    _, main_window = _connect_window(handle)
    _, process_id = win32process.GetWindowThreadProcessId(handle)
    _activate_window(handle, main_window)
    logger.info(f"已锁定 SZ-IFC 进程 PID={process_id}, HWND={handle} 并激活置顶")

    logger.info("[2/7] 进入模型检查并新建质检...")
    set_tool_progress("revit_inspect_ifc", "正在进入 SZ-IFC 模型检查页面")
    tab = _wait_control(
        main_window, deadline, "模型检查", "TabItem", cancel_event
    )
    _click_once(tab, main_window, handle)
    new_check = _wait_control(
        main_window, deadline, "新建模型质检", cancel_event=cancel_event
    )
    _click_once(new_check, main_window, handle)

    logger.info("[3/7] 选择质检规则...")
    set_tool_progress("revit_inspect_ifc", "正在选择 SZ-IFC 质检规则")
    chosen = _wait(
        deadline,
        "规则列表显示",
        lambda: _choose_rule(main_window, profession_keyword, rule_name),
        cancel_event=cancel_event,
    )
    if isinstance(chosen, dict):
        return {
            **chosen,
            "ifc_path": str(ifc_path),
            "profession": profession_keyword,
        }
    selected_text, selected_element = chosen
    _click_once(selected_element, main_window, handle)
    logger.info(f"已选择规则：{selected_text}")

    logger.info("[4/7] 进入规则检查页面...")
    set_tool_progress("revit_inspect_ifc", "正在进入质检规则执行页面")
    next_button = _wait_control(
        main_window, deadline, "下一步", "Button", cancel_event
    )
    _click_once(next_button, main_window, handle)
    run_button = _wait_control(
        main_window, deadline, "运行检查", "Button", cancel_event
    )

    logger.info("[5/7] 执行模型检查...")
    set_tool_progress("revit_inspect_ifc", "正在运行 SZ-IFC 模型检查计算（耗时取决于模型大小，请稍候）")
    _activate_window(handle, main_window)
    _click_once(run_button, main_window, handle)
    export_button = _wait_control(
        main_window, deadline, "导出报告", "Button", cancel_event
    )

    logger.info("[6/7] 导出并保存检查报告...")
    set_tool_progress("revit_inspect_ifc", "正在导出并保存 DOCX 质检报告")
    _raise_if_cancelled(cancel_event)
    _click_once(export_button, main_window, handle)
    _legacy_save_report_dialog(handle, report_path, deadline, cancel_event)
    logger.info("[7/7] 等待 DOCX 稳定写入...")
    set_tool_progress("revit_inspect_ifc", "正在等待 DOCX 质检报告文件写入")
    if not _wait_for_stable_docx(report_path, deadline, cancel_event):
        raise TimeoutError(
            f"DOCX 报告未在限定时间内完成稳定写入，最终状态未知：{report_path}"
        )
    logger.info(f"SZ-IFC 质检完成，报告已保存：{report_path}")

    # 解析质检报告，提取未通过构件 ID 并与 Revit 交互
    try:
        failed_ids = extract_failed_element_ids_from_docx(report_path)
        if failed_ids:
            logger.info(
                f"[8/8] 质检发现未通过项，共提取到 {len(failed_ids)} 个构件 ID，"
                f"示例：{failed_ids[:5]}，正在触发 Revit 一键交付定位..."
            )
            set_tool_progress(
                "revit_inspect_ifc",
                f"检测到 {len(failed_ids)} 个未通过构件，正在调用 Revit 一键交付定位",
            )
            call_open_delivery_api(failed_ids)
        else:
            logger.info("[8/8] 质检报告检查项全部通过（通过率 100%），无需打开交付定位。")
    except Exception as error:
        logger.warning(f"质检报告未通过项解析或 OpenDelivery 调用失败，不影响主流程报告生成：{error}")

    return str(report_path)


def extract_failed_element_ids_from_docx(docx_path: str | Path) -> list[str]:
    """从 SZ-IFC 质检报告 docx 中精确提取所有未通过规则的构件 ID 列表。

    返回按数值/字母升序排序且已去重的字符串 ID 列表。
    """
    docx_file = Path(docx_path).resolve()
    if not docx_file.is_file():
        raise FileNotFoundError(f"输入的 DOCX 质检报告文件不存在：{docx_file}")

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    failed_ids = set()

    try:
        with zipfile.ZipFile(docx_file) as doc:
            if "word/document.xml" not in doc.namelist():
                return []
            xml_content = doc.read("word/document.xml")
            root = ET.fromstring(xml_content)

            for tbl in root.findall(".//w:tbl", ns):
                for tr in tbl.findall(".//w:tr", ns):
                    cells = [
                        "".join(tc.itertext()).strip()
                        for tc in tr.findall(".//w:tc", ns)
                    ]
                    # 匹配规则未通过行：第一列标记为 '未通过'
                    if len(cells) >= 2 and cells[0] == "未通过":
                        raw_id_str = cells[1]
                        raw_tokens = [
                            token.strip()
                            for token in re.split(r"[,;；，\s\n]+", raw_id_str)
                            if token.strip()
                        ]
                        for token in raw_tokens:
                            if token != "未通过" and (token.isdigit() or len(token) >= 2):
                                failed_ids.add(token)
    except Exception as error:
        logger.error(f"解析 DOCX 质检报告失败：{error}")
        raise

    def sort_key(val: str):
        return (0, int(val)) if val.isdigit() else (1, val)

    return sorted(list(failed_ids), key=sort_key)


def _get_revit_api_base_url() -> str:
    """获取 Revit 插件 API Base URL，优先读取环境变量 BEESYNC_REVIT_API_BASE_URL。"""
    env_url = os.environ.get("BEESYNC_REVIT_API_BASE_URL")
    if env_url and env_url.strip():
        return env_url.strip().rstrip("/")
    try:
        from app.revit.client import configured_revit_api_base_url
        return configured_revit_api_base_url()
    except Exception:
        return "http://localhost:5000//api/RevitApi"


def call_open_delivery_api(
    element_ids: list[str],
    api_url: str | None = None,
    timeout: int = 60,
) -> dict:
    """调用 C# RevitApi/OpenDelivery 接口在 Revit 中打开并高亮显示未通过构件。"""
    if api_url:
        trimmed = api_url.strip().rstrip("/")
        url = trimmed if trimmed.endswith("/OpenDelivery") else f"{trimmed}/OpenDelivery"
    else:
        base_url = _get_revit_api_base_url()
        url = f"{base_url}/OpenDelivery"

    headers = {"content-type": "application/json"}
    payload = {"ElementIds": [str(eid) for eid in element_ids]}

    logger.info(f"正在调用 OpenDelivery 接口：{url}，包含 {len(element_ids)} 个构件 ID")
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        try:
            res_data = response.json()
        except Exception:
            res_data = {"code": response.status_code, "msg": response.text}

        code = res_data.get("code")
        msg = res_data.get("msg", "")
        if code == 200:
            logger.info(f"OpenDelivery 接口调用成功 (Code: 200): {msg}")
        else:
            logger.warning(f"OpenDelivery 接口返回状态 (Code: {code}): {msg}")
        return res_data
    except Exception as error:
        logger.error(f"调用 OpenDelivery 接口异常：{error}")
        return {"code": 500, "msg": str(error)}
