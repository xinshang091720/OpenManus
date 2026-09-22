import os
import time
import win32gui
import win32con
import requests
import pyautogui
from pywinauto import Application
from ohresult.logger_config import setup_logger
logger = setup_logger()


def auto_close_export_dialog(wait_timeout=10):
    """
    强力查找并关闭标题包含 '导出模型' 的弹窗
    """
    logger.info("⏳ 正在尝试捕捉并关闭【导出模型】弹窗...")
    start_time = time.time()

    while time.time() - start_time < wait_timeout:
        # ---- 方法 1：使用底层 Win32 API 直接按标题查找窗口句柄（不依赖 Pywinauto 遍历） ----
        hwnd = win32gui.FindWindow(None, "导出模型")

        # 如果准确名称没找到，模糊搜寻带“导出”的窗口
        if not hwnd:
            def enum_windows_callback(h_window, extra):
                if win32gui.IsWindowVisible(h_window):
                    title = win32gui.GetWindowText(h_window)
                    if "导出模型" in title or "导出" in title:
                        extra.append(h_window)

            found_hwnds = []
            win32gui.EnumWindows(enum_windows_callback, found_hwnds)
            if found_hwnds:
                hwnd = found_hwnds[0]

        if hwnd:
            logger.info(f"🔍 Win32 成功找到窗口句柄 HWND: {hwnd}")

            # 1. 强行显示并激活置顶该窗口
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.5)

            # 2. 尝试用 pywinauto 点击【确认】按钮
            try:
                app = Application(backend="uia").connect(handle=hwnd)
                dlg = app.window(handle=hwnd)
                btn = dlg.child_window(title="确认", control_type="Button")
                if btn.exists():
                    btn.click_input()
                    logger.info("🤖 已成功通过 pywinauto 点击【确认】按钮")
                    return True
            except Exception as e:
                logger.debug(f"按钮点击尝试跳过: {e}")

            # 3. 如果控件定位失败，但窗口已成功置顶，直接发 Enter 闭合！
            pyautogui.press("enter")
            logger.info("🤖 窗口已强行置顶，成功发送全局 Enter 键闭合弹窗")
            return True

        time.sleep(1)

    # ---- 方法 2：如果以上都找不着，直接全局发送 Enter 兜底 ----
    logger.warning("⚠️ 未能准确定位 HWND，尝试执行全局 Enter 兜底")
    pyautogui.press("enter")
    return False

def get_unique_ifc_path(folder_path, base_name):
    """
    生成唯一的 IFC 文件路径：如果文件已存在，则在后缀前循环累加 (1), (2), (3)...
    """
    count = 0
    while True:
        if count == 0:
            ifc_filename = f"{base_name}.ifc"
        else:
            ifc_filename = f"{base_name}({count}).ifc"

        full_ifc_path = os.path.join(folder_path, ifc_filename)

        # 检测文件是否存在，不存在说明路径可用，跳出循环
        if not os.path.exists(full_ifc_path):
            return full_ifc_path

        count += 1

def wait_for_files_generated(ifc_path, timeout=7200, check_interval=10):
    """
    等待接口导出完成：轮询检测 .ifc 与 .ifc.xlsx 文件是否均已在目录下生成
    :param ifc_path: IFC 文件的完整路径
    :param timeout: 最大超时时间（秒），默认 60 秒
    :param check_interval: 检查的时间间隔（秒），默认 2 秒
    """
    xlsx_path = f"{ifc_path}.xlsx"
    start_time = time.time()

    logger.info(f"等待文件落地生成: \n  1) {ifc_path}\n  2) {xlsx_path}")

    while time.time() - start_time < timeout:
        ifc_exists = os.path.exists(ifc_path)
        xlsx_exists = os.path.exists(xlsx_path)

        if ifc_exists and xlsx_exists:
            logger.info("✅ 检测到 .ifc 和 .ifc.xlsx 文件均已成功生成落地！")
            time.sleep(2)
            auto_close_export_dialog(wait_timeout=30)
            return True

        time.sleep(check_interval)

    logger.error(
        f"⏰ 等待超时（{timeout}s）：未检测到完整的导出文件 (.ifc / .ifc.xlsx)"
    )
    return False

def call_export_ifc_api(ifc_path):
    """调用 API 接口导出/生成 IFC 文件"""
    url = "http://localhost:5000/api/RevitApi/ExportIFC"
    headers = {"content-type": "application/json"}
    payload = {"IfcFilePath": ifc_path}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=300)
        res_data = response.json()

        # 获取返回体中的 code 和 msg
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
    # 检查用户输入的路径是否存在且为目录
    if not os.path.exists(folder_path):
        logger.error(f"错误：指定的路径 '{folder_path}' 不存在。")
        return

    if not os.path.isdir(folder_path):
        logger.error(f"错误：'{folder_path}' 不是一个有效的文件夹目录。")
        return


    # 遍历指定目录下的所有文件
    for filename in os.listdir(folder_path):
        # 判断文件是否以 .rvt 结尾（不区分大小写）
        if filename.lower().endswith('.rvt'):

            base_name = os.path.splitext(filename)[0]

            full_ifc_path = get_unique_ifc_path(folder_path, base_name)

            call_export_ifc_api(full_ifc_path)


# ==================== 使用示例 ====================
if __name__ == "__main__":
    # 让用户输入目录地址
    user_directory = r"D:\project\ai_agent\data\revit_ifc"
    # 执行处理
    result_paths = process_rvt_files(user_directory)