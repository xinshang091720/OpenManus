import pathlib

# Update git_ifc.py
git_ifc_content = """import os
import time
import win32gui
import win32con
import requests
import pyautogui
from pywinauto import Application
from q_agent_function_module.ohresult.logger_config import setup_logger
logger = setup_logger()


def _try_click_confirm_button(hwnd):
    \"\"\"
    尝试点击指定 HWND 弹窗里的确认类按钮。
    返回 True 表示成功点击，False 表示未找到按钮（可能是进度弹窗）。
    \"\"\"
    # 1. Win32 枚举子窗口按钮
    clicked = []
    def visit(child_hwnd, _):
        class_name = win32gui.GetClassName(child_hwnd)
        txt = win32gui.GetWindowText(child_hwnd).strip()
        if "button" in class_name.lower() or txt in ("确认", "确定", "保存(S)", "保存", "OK"):
            win32gui.SendMessage(child_hwnd, win32con.BM_CLICK, 0, 0)
            clicked.append(child_hwnd)
    try:
        win32gui.EnumChildWindows(hwnd, visit, None)
        if clicked:
            logger.info("🤖 已通过 Win32 BM_CLICK 成功点击确认按钮")
            return True
    except Exception as e:
        logger.debug(f"Win32 枚举子窗口失败: {e}")

    # 2. UIA 方式查找 Button 控件
    try:
        app = Application(backend="uia").connect(handle=hwnd)
        dlg = app.window(handle=hwnd)
        buttons = dlg.children(control_type="Button")
        if buttons:
            for btn in buttons:
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


def auto_close_export_dialog(wait_timeout=10, fallback_enter=False):
    \"\"\"
    查找并关闭 Revit IFC 导出流程中的所有弹窗：
      1. Revit 保存路径确认弹窗（出现在导出开始时，标题为"导出IFC"/"另存为"）
      2. 【导出模型】进度弹窗 × 2（无按钮，跳过等待）
      3. 【导出模型】最终确认弹窗（含"确认"按钮 / "导出已完成！"）

    策略：找到窗口 → 先尝试 UIA/Win32 点按钮 → 保存弹窗兜底发 Enter。
    \"\"\"
    logger.info("⏳ 正在尝试捕捉并关闭【导出模型/保存路径】弹窗...")
    start_time = time.time()

    SAVE_DIALOG_KEYWORDS = ("另存为", "导出IFC", "Export IFC")
    EXPORT_DIALOG_TITLES = {"导出模型"}
    IGNORE_KEYWORDS = ("Antigravity", "Visual Studio", "VS Code", "Chrome", "Edge", "Firefox", "OpenManus", "IDE", "analysis_export")

    while time.time() - start_time < wait_timeout:
        found_hwnds = []

        def enum_cb(h, extra):
            if not win32gui.IsWindowVisible(h):
                return
            title = win32gui.GetWindowText(h).strip()
            if not title:
                return
            if any(ignore.lower() in title.lower() for ignore in IGNORE_KEYWORDS):
                return
            class_name = win32gui.GetClassName(h)
            if title in EXPORT_DIALOG_TITLES:
                extra.append((h, title, class_name))
            elif class_name == "#32770" and any(kw in title for kw in SAVE_DIALOG_KEYWORDS):
                extra.append((h, title, class_name))
            elif any(kw == title for kw in SAVE_DIALOG_KEYWORDS):
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

            if class_name == "#32770" or any(kw in title for kw in SAVE_DIALOG_KEYWORDS):
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_COMMAND, 1, 0)
                except Exception:
                    pass
                pyautogui.press("enter")
                logger.info("🤖 保存路径弹窗：发送 IDOK / 全局 Enter 尝试确认保存")
                return True
            else:
                logger.info(f"⏳ 弹窗「{title}」暂无确认按钮，判定为进度等待中...")

        time.sleep(1)

    logger.warning("⚠️ 未能准确定位导出弹窗")
    if fallback_enter:
        pyautogui.press("enter")
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


def wait_for_files_generated(ifc_path, timeout=7200, check_interval=10):
    xlsx_path = f"{ifc_path}.xlsx"
    start_time = time.time()
    logger.info(f"等待文件落地生成: \\n  1) {ifc_path}\\n  2) {xlsx_path}")

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
            logger.info(f"接口调用成功 (Code: 200)，开始校验实际文件是否生成...")
            return wait_for_files_generated(ifc_path)
        elif code == 401:
            logger.error(f"❌ 错误: 未安装SZ-IFC转换插件 ")
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
    user_directory = r"D:\\project\\ai_agent\\data\\revit_ifc"
    result_paths = process_rvt_files(user_directory)
"""

pathlib.Path(r"c:\Users\jly23\Desktop\OpenManus-main\q_agent_function_module\ohresult\CAD_Git_Coordinates\my_code\ifc_function\git_ifc.py").write_text(git_ifc_content, encoding="utf-8")
print("Updated git_ifc.py successfully!")
