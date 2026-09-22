import os
import time
import winreg
import subprocess
from pywinauto import Application
from pywinauto.keyboard import send_keys
from ohresult.logger_config import setup_logger
logger = setup_logger()

def set_clipboard_text(text):
    """2. 修复 set_clipboard_text 缺失：用 Windows 原生 clip 设置剪贴板"""
    process = subprocess.Popen('clip', stdin=subprocess.PIPE, shell=True)
    process.communicate(input=text.encode('utf-16le'))


def find_sz_ifc_by_registry():
    """从注册表中提取 CBIMS.Manager.exe 的绝对路径"""
    logger.info(" -> 正在解析注册表定位 SZ-IFC (CBIMS.Manager.exe)...")
    target_exe_names = ["CBIMS.Manager.exe", "CBIMS.exe"]

    reg_targets = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    for root, sub_key_path in reg_targets:
        for access_flag in [winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ | winreg.KEY_WOW64_32KEY]:
            try:
                key = winreg.OpenKey(root, sub_key_path, 0, access_flag)
                num_subkeys = winreg.QueryInfoKey(key)[0]
                for i in range(num_subkeys):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        sub_key = winreg.OpenKey(root, f"{sub_key_path}\\{subkey_name}", 0, access_flag)
                        display_name, install_loc = "", ""

                        try:
                            display_name, _ = winreg.QueryValueEx(sub_key, "DisplayName")
                        except OSError:
                            pass
                        try:
                            install_loc, _ = winreg.QueryValueEx(sub_key, "InstallLocation")
                        except OSError:
                            pass

                        if display_name and any(kw in str(display_name) for kw in ["SZ-IFC", "SZIFC", "自检工具"]):
                            if install_loc and os.path.exists(install_loc):
                                for target_exe in target_exe_names:
                                    direct_path = os.path.join(install_loc, target_exe)
                                    if os.path.exists(direct_path):
                                        return direct_path
                    except OSError:
                        continue
            except OSError:
                continue
    return None


def click_element_center(main_win, element):
    """获取控件真实的物理绝对坐标并进行模拟鼠标点击"""
    rect = element.rectangle()
    center_x = (rect.left + rect.right) // 2
    center_y = (rect.top + rect.bottom) // 2
    main_win.click_input(coords=(center_x, center_y))
    return center_x, center_y


def run_sz_ifc_full_inspection(ifc_file_path, profession_keyword , output_docx_path=None):
    PROFESSION_dict = {
        "建筑": "建筑工程BIM设计交付标准-施工图设计阶段_建筑",
        "总图": "建筑工程BIM设计交付标准-施工图设计阶段_总图",
        "电气": "建筑工程BIM设计交付标准-施工图设计阶段_电气_智能化",
        "结构": "建筑工程BIM设计交付标准-施工图设计阶段_结构_钢结构_装配式混凝土",
        "给排水": "建筑工程BIM设计交付标准-施工图设计阶段_给排水",
        "通风空调": "建筑工程BIM设计交付标准-施工图设计阶段_通风空调_燃气",
    }

    profession_rule = PROFESSION_dict.get(profession_keyword, profession_keyword)
    ifc_abs_path = os.path.abspath(ifc_file_path)
    if not os.path.exists(ifc_abs_path):
        raise FileNotFoundError(f"输入的 IFC 文件不存在: {ifc_abs_path}")

    # =====【重点修改 1：动态处理输出路径】=====
    base_name = os.path.splitext(os.path.basename(ifc_abs_path))[0]
    default_filename = f"{base_name}_质检报告.docx"

    if not output_docx_path:
        # 用户未传入路径，默认使用 IFC 文件所在目录
        output_dir = os.path.dirname(ifc_abs_path)
        docx_abs_path = os.path.join(output_dir, default_filename)
    else:
        # 用户传入了路径
        output_docx_path = os.path.abspath(output_docx_path)
        # 如果传入的是目录（或者路径以扩展名结尾）
        if output_docx_path.lower().endswith(".docx"):
            docx_abs_path = output_docx_path
        else:
            # 认为传入的是文件夹路径
            docx_abs_path = os.path.join(output_docx_path, default_filename)

    os.makedirs(os.path.dirname(docx_abs_path), exist_ok=True)

    # ---------------- 步骤 0：启动软件 ----------------
    logger.info("[0/7] 定位程序并通过 PID 唤醒软件...")
    exe_path = find_sz_ifc_by_registry()
    if not exe_path:
        raise RuntimeError("未能从注册表找到 CBIMS.Manager.exe 安装路径。")

    cmd_list = [exe_path, ifc_abs_path]
    logger.info(f" -> 执行启动命令: {cmd_list}")
    proc = subprocess.Popen(cmd_list)

    logger.info(f" -> 软件已拉起(PID: {proc.pid})，等待 IFC 模型渲染完成...")
    time.sleep(8)

    logger.info(" -> 正在通过 PID 接入 UI 自动化句柄...")
    app = Application(backend="uia").connect(process=proc.pid, timeout=30)

    main_win = app.top_window()
    main_win.wait("visible", timeout=20)
    main_win.set_focus()
    logger.info(" -> 成功接入软件主界面！")

    # ---------------- 步骤 1：激活“模型检查”Tab ----------------
    logger.info("[1/7] 点击顶部菜单“模型检查”...")
    try:
        tab_elem = main_win.child_window(title="模型检查", control_type="TabItem", found_index=0)
    except Exception:
        tab_elem = main_win.child_window(title="模型检查", found_index=0)

    tab_elem.wait("visible", timeout=10)
    cx, cy = click_element_center(main_win, tab_elem)
    # logger.info(f" -> 已点击【模型检查】Tab 坐标 ({cx}, {cy})")
    time.sleep(2)

    # ---------------- 步骤 2：点击“新建模型质检” ----------------
    logger.info("[2/7] 点击“新建模型质检”...")
    new_check_btn = main_win.child_window(title="新建模型质检", found_index=0)
    click_element_center(main_win, new_check_btn)
    time.sleep(2)

    # ---------------- 步骤 3：寻找并选择规则文件 ----------------
    logger.info(f"[3/7] 寻找并选择规则文件: 『{profession_rule}』...")

    rule_selected = False
    time.sleep(1.5)

    clean_keyword = profession_rule.strip()
    all_descendants = main_win.descendants()
    matched_elements = []

    for elem in all_descendants:
        try:
            text = elem.window_text().strip()
            if not text:
                continue

            if text.startswith(clean_keyword) or clean_keyword in text:
                if "模型质检" not in text and "模型检查" not in text:
                    matched_elements.append((text, elem))
        except Exception:
            continue

    if matched_elements:
        matched_elements.sort(key=lambda x: len(x[0]))
        target_text, target_elem = matched_elements[0]

        logger.info(f" -> 成功定位到规则: 『{target_text}』")
        cx, cy = click_element_center(main_win, target_elem)
        logger.info(f" -> 已完成物理点击，坐标: ({cx}, {cy})")
        rule_selected = True

    if not rule_selected:
        raise RuntimeError(f"❌ 严重错误：在右侧【选择规则文件】列表中未匹配到规则：『{clean_keyword}』！")

    time.sleep(1.5)

    # ---------------- 步骤 4：点击“下一步” ----------------
    logger.info("[4/7] 点击右下角“下一步”按钮...")
    time.sleep(1.0)  # 留出时间让页面 UI 元素完成渲染
    try:
        next_btn = main_win.child_window(title="下一步", control_type="Button", found_index=0)
        next_btn.wait("exists", timeout=10)  # 优先检查存在性
        click_element_center(main_win, next_btn)
    except Exception as e:
        logger.warning(f" -> 物理点击下一步失败({e})，尝试模拟回车/快捷键...")
        send_keys("{ENTER}")

    time.sleep(2.5)  # 给 UI 页面切换留出足够缓冲区

    # ---------------- 步骤 5：点击“运行检查” ----------------
    logger.info("[5/7] 点击右下角“运行检查”按钮...")
    try:
        # 允许最多 15 秒查找该按钮
        run_btn = main_win.child_window(title="运行检查", control_type="Button", found_index=0)
        run_btn.wait("ready", timeout=15)  # 等待按钮处于 ready (可用) 状态
        click_element_center(main_win, run_btn)
        logger.info(" -> 已成功点击“运行检查”按钮！")
    except Exception as e:
        logger.warning(f" -> 物理点击“运行检查”异常({e})，尝试直接发送 ENTER 触发...")
        # 很多 Windows 客户端中“运行检查”是默认焦点按钮，直接敲 Enter 即可触发
        send_keys("{ENTER}")

    # ---------------- 步骤 6：等待计算完成并导出 ----------------
    logger.info("[6/7] 质检计算中，监听“导出报告”按钮...")

    max_wait_seconds = 600
    start_time = time.time()
    export_btn = None

    while True:
        if time.time() - start_time > max_wait_seconds:
            raise TimeoutError("❌ 模型质检计算超时！")

        try:
            # 实时抓取主窗口下所有的后代元素（解决层级包裹和 DOM 树刷新不及时问题）
            all_elements = main_win.descendants()
            for elem in all_elements:
                try:
                    # 获取控件文本或名称
                    text = elem.window_text().strip()
                    if text == "导出报告":
                        # 找到目标元素，检查它是否处于“可用”状态（计算完成后会解锁变成 enabled）
                        if elem.is_enabled():
                            export_btn = elem
                            break
                except Exception:
                    continue

            if export_btn:
                logger.info(" -> 成功捕捉到“导出报告”按钮！")
                break

        except Exception as e:
            pass  # 忽略遍历过程中的临时 UI 状态异常

        time.sleep(2)

    logger.info(" -> 质检计算完成！点击“导出报告”...")
    click_element_center(main_win, export_btn)
    time.sleep(1.5)
    # ---------------- 步骤 7：规范保存 DOCX 报告 ----------------
    target_dir = os.path.dirname(docx_abs_path)
    base_pure_filename = os.path.splitext(os.path.basename(docx_abs_path))[0]

    logger.info(f"[7/7] 准备保存报告:")
    logger.info(f" -> 目标文件夹: {target_dir}")

    counter = 0
    while True:
        if counter == 0:
            current_filename = f"{base_pure_filename}.docx"
        else:
            current_filename = f"{base_pure_filename}({counter}).docx"

        check_path = os.path.join(target_dir, current_filename)
        if not os.path.exists(check_path):
            final_docx_path = check_path
            break
        counter += 1

    logger.info(f" -> 预检完成，最终确定的保存文件名: {current_filename}")

    time.sleep(1.5)
    save_dlg = None

    for _ in range(15):
        try:
            dlgs = app.windows(title_re=".*保存.*|.*另存为.*")
            if dlgs:
                save_dlg = dlgs[0]
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not save_dlg:
        save_dlg = app.top_window()

    try:
        save_dlg.wait("visible", timeout=10)
        save_dlg.set_focus()
    except Exception as e:
        logger.error(f" -> 激活窗口焦点提示: {e}")

    time.sleep(0.8)

    # 1. 切换到目标目录（仅需执行一次）
    try:
        send_keys("{F4}")
        time.sleep(0.3)
        send_keys("^a{BACKSPACE}")
        time.sleep(0.2)
        send_keys(target_dir, with_spaces=True)
        send_keys("{ENTER}")
        logger.info(" -> 已通过地址栏导航至目标文件夹")
        time.sleep(1.0)
    except Exception as e:
        logger.error(f" -> 导航地址栏跳过/失败: {e}")

    logger.info(f" -> 正在填入文件名: {current_filename}")
    try:
        edit_box = save_dlg.child_window(control_type="Edit", found_index=0)
        edit_box.click_input()
        time.sleep(0.3)
        edit_box.set_edit_text(current_filename)
    except Exception:
        send_keys("^a{BACKSPACE}")
        time.sleep(0.2)
        send_keys(current_filename, with_spaces=True)

    time.sleep(0.8)

    # 点击【保存】按钮
    try:
        save_btn = save_dlg.child_window(title_re="保存.*", control_type="Button", found_index=0)
        click_element_center(save_dlg, save_btn)
    except Exception:
        send_keys("{ENTER}")

    # ===== 5. 处理最后的“导出成功”确认弹窗 =====
    logger.info(" -> 等待“导出成功”提示框...")
    success_closed = False

    for _ in range(15):
        try:
            top_w = app.top_window()
            all_texts = [top_w.window_text()] + [e.window_text() for e in top_w.descendants()]

            if any("导出成功" in text for text in all_texts):
                try:
                    ok_btn = top_w.child_window(title="确定", control_type="Button", found_index=0)
                    click_element_center(main_win, ok_btn)
                    logger.info(" -> 已成功点击“导出成功”弹窗上的【确定】按钮！")
                except Exception:
                    send_keys("{ENTER}")
                    logger.error(" -> 发送 ENTER 键关闭“导出成功”弹窗！")

                success_closed = True
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not success_closed:
        send_keys("{ENTER}")

    time.sleep(1)
    logger.info(f"🎉【全流程完成】DOCX 报告已导出保存在: {final_docx_path}")
    return final_docx_path


if __name__ == "__main__":
    TEST_IFC = r"D:\project\ai_agent\data\revit_ifc\2301-440300-04-01-360562_深圳市第二眼科医院项目_裙楼_A.0001.ifc"
    profession = "建筑"

    # =====【重点修改 2：支持未传 OUTPUT_PATH 情况】=====
    # 如果用户没有配置路径/传 None，程序会自动落在 TEST_IFC 目录下
    # 或者赋值为指定的路径，如: r"D:\project\AI_Document2\data\ifc_to_docx"
    OUTPUT_PATH = None

    try:
        report_file = run_sz_ifc_full_inspection(
            ifc_file_path=TEST_IFC,
            profession_keyword=profession,
            output_docx_path=OUTPUT_PATH,
        )
    except Exception as e:
        print(f"❌ 运行失败: {e}")