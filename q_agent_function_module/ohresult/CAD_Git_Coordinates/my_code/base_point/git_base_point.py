import re, os, math, time, tempfile, win32com.client, ezdxf, json
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from q_agent_function_module.ohresult.logger_config import setup_logger
logger = setup_logger()


def _distance_2d(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def is_point_in_box(pt: Tuple[float, float], box: Tuple[float, float, float, float]) -> bool:
    x, y = pt
    min_x, min_y, max_x, max_y = box
    return (min_x - 10.0) <= x <= (max_x + 10.0) and (min_y - 10.0) <= y <= (max_y + 10.0)


def find_intersection(line1: Tuple[Tuple[float, float], Tuple[float, float]],
                      line2: Tuple[Tuple[float, float], Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    p0, p1 = line1[0], line1[1]
    p2, p3 = line2[0], line2[1]
    s1_x, s1_y = p1[0] - p0[0], p1[1] - p0[1]
    s2_x, s2_y = p3[0] - p2[0], p3[1] - p2[1]
    denom = (-s2_x * s1_y + s1_x * s2_y)
    if abs(denom) < 1e-6:
        return None
    s = (-s1_y * (p0[0] - p2[0]) + s1_x * (p0[1] - p2[1])) / denom
    t = (s2_x * (p0[1] - p2[1]) - s2_y * (p0[0] - p2[0])) / denom
    if 0 <= s <= 1 and 0 <= t <= 1:
        return (p0[0] + (t * s1_x), p0[1] + (t * s1_y))
    return None


def clean_cad_text(raw_text: str) -> str:
    if not raw_text:
        return ""
    cleaned = raw_text
    cleaned = re.sub(r'\\[A-Za-z0-9.]+;', '', cleaned)
    cleaned = re.sub(r'\\[Pp]', ' ', cleaned)
    cleaned = cleaned.replace('{', '').replace('}', '')
    return cleaned.replace('\r', '').replace('\n', '').strip()


def is_entity_visible(entity: Any, doc: Any) -> bool:
    try:
        layer_name = entity.dxf.layer
        layer = doc.layers.get(layer_name)
        if layer and (not layer.is_on() or layer.is_frozen()):
            return False
        if entity.dxftype() == 'ATTRIB':
            if entity.dxf.flags & 1:
                return False
        return True
    except Exception:
        return True


def filter_spatial_outliers_mad(paired_list: List[Dict[str, Any]], threshold: float = 3.5) -> List[Dict[str, Any]]:
    if not paired_list or len(paired_list) < 3:
        return paired_list
    vals = np.array([[item["X"], item["Y"]] for item in paired_list])
    median = np.median(vals, axis=0)
    abs_deviation = np.abs(vals - median)
    med_abs_deviation = np.median(abs_deviation, axis=0)
    med_abs_deviation = np.where(med_abs_deviation == 0, 1e-6, med_abs_deviation)
    z_scores = 0.6745 * abs_deviation / med_abs_deviation
    valid_mask = (z_scores[:, 0] < threshold) & (z_scores[:, 1] < threshold)
    cleaned_list = [paired_list[i] for i in range(len(paired_list)) if valid_mask[i]]
    dropped_count = len(paired_list) - len(cleaned_list)
    if dropped_count > 0:
        logger.info(f"[方案A降噪] 动态识别并剥离了 {dropped_count} 个偏离设计核心区的空间幽灵坐标。")
    return cleaned_list


def detect_drawing_frames(msp) -> List[Tuple[float, float, float, float]]:
    frames = []
    for entity in msp:
        dxftype = entity.dxftype()
        if dxftype == 'LWPOLYLINE':
            if not entity.closed:
                continue
            points = entity.get_points(format='xy')
            if len(points) < 4 or len(points) > 6:
                continue
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            width = max_x - min_x
            height = max_y - min_y
            if width < 100 or height < 100:
                continue
            area = width * height
            ratio = width / height if height != 0 else 0
            if area > 1e7 and (0.5 < ratio < 2.0):
                frames.append((min_x, min_y, max_x, max_y))
        elif dxftype == 'INSERT':
            try:
                box = ezdxf.bbox.extents([entity])
                min_x, min_y = box.extmin.x, box.extmin.y
                max_x, max_y = box.extmax.x, box.extmax.y
                width = max_x - min_x
                height = max_y - min_y
                if (width * height) > 1e7 and (0.5 < (width / height) < 2.0):
                    frames.append((min_x, min_y, max_x, max_y))
            except Exception:
                pass
    unique_frames = []
    for f in frames:
        if not any(math.sqrt((f[0] - uf[0]) ** 2 + (f[1] - uf[1]) ** 2) < 1000.0 for uf in unique_frames):
            unique_frames.append(f)
    if unique_frames:
        logger.info(f"[图框检测] 成功通过纯几何特征侦测到 {len(unique_frames)} 个有效真实图框范围。")
    else:
        logger.info(f"[图框检测] 未能在图中抓取到符合物理特征的闭合矩形图框。")
    return unique_frames  # 返回精准清洗完成后的全图物理图框边界集


def fallback_axis_grid_in_frames(msp, frames: List[Tuple[float, float, float, float]], doc: Any) -> List[
    Dict[str, Any]]:
    logger.info("[兜底激活] 所有有效判定区域内未提取到坐标数据。启动局域轴网空间检索...")
    lines: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    axis_labels: List[Dict[str, Any]] = []
    axis_layer_patterns = ['axis', 'grid', '轴', 'dote']
    for entity in msp:
        if not is_entity_visible(entity, doc):
            continue
        layer_lower = entity.dxf.layer.lower()
        dxftype = entity.dxftype()
        if any(p in layer_lower for p in axis_layer_patterns):
            if dxftype == 'LINE':
                lines.append(((entity.dxf.start.x, entity.dxf.start.y),
                              (entity.dxf.end.x, entity.dxf.end.y)))
            elif dxftype == 'LWPOLYLINE':
                points = entity.get_points(format='xy')
                for i in range(len(points) - 1):
                    lines.append((points[i], points[i + 1]))
        if dxftype in ('TEXT', 'MTEXT'):
            text_str = clean_cad_text(entity.plain_text() if dxftype == 'MTEXT' else entity.dxf.text)
            if text_str and re.match(r'^[A-Z0-9/\-]+$', text_str) and len(text_str) <= 4:
                axis_labels.append({"label": text_str, "pos": (entity.dxf.insert.x, entity.dxf.insert.y)})
        elif dxftype == 'INSERT' and hasattr(entity, 'attribs'):
            for attrib in entity.attribs:
                if not is_entity_visible(attrib, doc): continue
                text_str = clean_cad_text(attrib.dxf.text)
                if text_str and re.match(r'^[A-Z0-9/\-]+$', text_str) and len(text_str) <= 4:
                    axis_labels.append({"label": text_str, "pos": (attrib.dxf.insert.x, attrib.dxf.insert.y)})
    if not lines or not axis_labels:
        logger.info("[兜底失败] 轴网系统图元不足，无法解算。")
        return []
    labeled_lines: List[Dict[str, Any]] = []
    for line in lines:
        mid_point = ((line[0][0] + line[1][0]) / 2, (line[0][1] + line[1][1]) / 2)
        best_label, min_dist = None, float('inf')
        for label_item in axis_labels:
            d = min(_distance_2d(label_item["pos"], line[0]),
                    _distance_2d(label_item["pos"], line[1]),
                    _distance_2d(label_item["pos"], mid_point))
            if d < min_dist:
                min_dist = d
                best_label = label_item["label"]
        if min_dist < 3000.0 and best_label:
            labeled_lines.append({"geometry": line, "label": best_label})
    grid_intersections = []
    seen_intersection_keys = set()
    for i in range(len(labeled_lines)):
        for j in range(i + 1, len(labeled_lines)):
            line1 = labeled_lines[i]
            line2 = labeled_lines[j]
            if line1["label"] == line2["label"]:
                continue
            is_l1_num = bool(re.search(r'\d', line1["label"]))
            is_l2_num = bool(re.search(r'\d', line2["label"]))
            if is_l1_num != is_l2_num or (line1["label"].isalpha() and line2["label"].isdigit()):
                pt = find_intersection(line1["geometry"], line2["geometry"])
                if pt:
                    if frames and not any(is_point_in_box(pt, frame) for frame in frames):
                        continue
                    grid_name = f"{line1['label']}轴 交 {line2['label']}轴"
                    key = (round(pt[0], 2), round(pt[1], 2))
                    if key not in seen_intersection_keys:
                        seen_intersection_keys.add(key)
                        grid_intersections.append({
                            "type": "智能轴网交点(兜底)",
                            "axis_name": grid_name,
                            "X": pt[0],
                            "Y": pt[1]
                        })
    logger.info(f"[兜底成功] 成功结算出 {len(grid_intersections)} 个轴线交叉控制点。")
    return grid_intersections


def final_fallback_colored_entities(msp, doc) -> List[Dict[str, Any]]:
    logger.info("[终极视觉兜底启动] 进入深度解算分流决策体系...")
    GRAY_ACI_LIST = {8, 9, 250, 251, 252, 253, 254, 255}
    bright_lines: List[Dict[str, Any]] = []
    for entity in msp:
        if not is_entity_visible(entity, doc):
            continue
        dxftype = entity.dxftype()
        if dxftype not in ('LINE', 'LWPOLYLINE', 'POLYLINE'):
            continue
        aci_color = entity.dxf.color
        if aci_color == 256:
            try:
                layer = doc.layers.get(entity.dxf.layer)
                aci_color = layer.color if layer else 7
            except Exception:
                aci_color = 7
        if aci_color in GRAY_ACI_LIST or aci_color == 7:
            continue
        pt = None
        if dxftype == 'LINE':
            pt = ((entity.dxf.start.x + entity.dxf.end.x) / 2, (entity.dxf.start.y + entity.dxf.end.y) / 2)
        elif dxftype in ('LWPOLYLINE', 'POLYLINE'):
            try:
                points = entity.get_points(format='xy') if dxftype == 'LWPOLYLINE' else [v.dxf.location for v in
                                                                                         entity.vertices]
                if points:
                    pt = (points[0][0], points[0][1])
            except Exception:
                pass
        if pt:
            bright_lines.append({
                "type": "高亮彩色线段(第一梯队兜底)",
                "X": pt[0],
                "Y": pt[1],
                "color_index": aci_color
            })
    if bright_lines:
        logger.info(f"【第一梯队命中】成功捕获 {len(bright_lines)} 个彩色线段坐标。触发业务短路熔断，第二梯队已自动免检。")
        return bright_lines
    logger.info("[信息] 未发现高亮彩色线段。降级启动第二梯队检测（任意可见非灰图元）...")
    visible_entities: List[Dict[str, Any]] = []
    for entity in msp:
        if not is_entity_visible(entity, doc):
            continue
        aci_color = entity.dxf.color
        if aci_color == 256:
            try:
                layer = doc.layers.get(entity.dxf.layer)
                aci_color = layer.color if layer else 7
            except Exception:
                aci_color = 7
        if aci_color in GRAY_ACI_LIST:
            continue
        dxftype = entity.dxftype()
        pt = None
        if hasattr(entity, 'dxf') and hasattr(entity.dxf, 'insert'):
            pt = (entity.dxf.insert.x, entity.dxf.insert.y)
        elif hasattr(entity, 'dxf') and hasattr(entity.dxf, 'location'):
            pt = (entity.dxf.location.x, entity.dxf.location.y)
        elif dxftype == 'LINE':
            pt = ((entity.dxf.start.x + entity.dxf.end.x) / 2, (entity.dxf.start.y + entity.dxf.end.y) / 2)
        elif dxftype in ('LWPOLYLINE', 'POLYLINE'):
            try:
                points = entity.get_points(format='xy') if dxftype == 'LWPOLYLINE' else [v.dxf.location for v in
                                                                                         entity.vertices]
                if points: pt = (points[0][0], points[0][1])
            except Exception:
                pass
        if pt:
            visible_entities.append({
                "type": "可见非灰图元(第二梯队兜底)",
                "X": pt[0],
                "Y": pt[1],
                "color_index": aci_color
            })
    if visible_entities:
        logger.info(f"【第二梯队命中】已安全筛选出可见非灰图元坐标，截取前 {min(5, len(visible_entities))} 个样本输出。")
        return visible_entities[:5]
    logger.info("[终极熔断警告] 图纸内无任何满足可见、非灰要求的几何图元，兜底链空回。")
    return []


def extract_info_from_dxf_stream(dxf_path: str) -> Dict[str, Any]:
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    frames = detect_drawing_frames(msp)
    x_pattern = re.compile(r'[xX]\s*(?:[:=\s(]|\s+)\s*(-?\d+\.\d+|-?\d+)\s*\)?')
    y_pattern = re.compile(r'[yY]\s*(?:[:=\s(]|\s+)\s*(-?\d+\.\d+|-?\d+)\s*\)?')
    # elevation_pattern = re.compile(r'绝对标高(?:相当于|为)?\s*(-?\d+\.\d+|-?\d+)\s*(?:米)?')
    elevation_pattern = re.compile(
        r'(?:绝对标高|[\?\s\\A1;]{2,}(?:绝对标高)?)(?:相当于|为)?\s*(-?\d+\.\d+|-?\d+)\s*(?:米)?')
    rotation_pattern = re.compile(
        r'(?:旋转角(?:度)?|北偏东|旋转)\s*(?:相当于|为|[:=\s])?\s*(-?\d+\.\d+|-?\d+)\s*(?:°|度)?')

    found_xs: List[Dict[str, Any]] = []
    found_ys: List[Dict[str, Any]] = []
    paired_coordinates: List[Dict[str, Any]] = []
    absolute_elevation: Optional[float] = None
    rotation_angle: Optional[float] = None

    def process_text_with_spatial_filter(text_str: str, pos: Tuple[float, float], parent_entity: Any):
        nonlocal absolute_elevation, rotation_angle
        if not text_str or not is_entity_visible(parent_entity, doc):
            return
        cleaned_text = clean_cad_text(text_str)
        if not cleaned_text:
            return

        if absolute_elevation is None:
            elev_match = elevation_pattern.search(cleaned_text)
            if elev_match:
                absolute_elevation = float(elev_match.group(1))

        if rotation_angle is None:
            rot_match = rotation_pattern.search(cleaned_text)
            if rot_match:
                rotation_angle = float(rot_match.group(1))

        if frames and not any(is_point_in_box(pos, frame) for frame in frames):
            return

        x_match = x_pattern.search(cleaned_text)
        y_match = y_pattern.search(cleaned_text)

        if x_match and y_match:
            vx, vy = float(x_match.group(1)), float(y_match.group(1))
            if _distance_2d((vx, vy), pos) > 1000000.0:
                return
            paired_coordinates.append({"X": vx, "Y": vy, "pos": pos})
        elif x_match:
            found_xs.append({"val": float(x_match.group(1)), "pos": pos})
        elif y_match:
            found_ys.append({"val": float(y_match.group(1)), "pos": pos})

    for entity in msp:
        dxftype = entity.dxftype()
        if dxftype in ('TEXT', 'MTEXT'):
            text_content = entity.plain_text() if dxftype == 'MTEXT' else entity.dxf.text
            process_text_with_spatial_filter(text_content, (entity.dxf.insert.x, entity.dxf.insert.y), entity)
        elif dxftype == 'INSERT':
            if hasattr(entity, 'attribs') and len(entity.attribs) > 0:
                for attrib in entity.attribs:
                    process_text_with_spatial_filter(attrib.dxf.text, (attrib.dxf.insert.x, attrib.dxf.insert.y),
                                                     attrib)
            else:
                try:
                    for sub_entity in entity.virtual_entities():
                        sub_type = sub_entity.dxftype()
                        if sub_type in ('TEXT', 'MTEXT'):
                            text_content = sub_entity.plain_text() if sub_type == 'MTEXT' else sub_entity.dxf.text
                            process_text_with_spatial_filter(text_content,
                                                             (sub_entity.dxf.insert.x, sub_entity.dxf.insert.y), entity)
                except Exception:
                    pass
        elif dxftype in ('DIMENSION', 'LEADER', 'MULTILEADER'):
            try:
                for sub_entity in entity.virtual_entities():
                    sub_type = sub_entity.dxftype()
                    if sub_type in ('TEXT', 'MTEXT'):
                        text_content = sub_entity.plain_text() if sub_type == 'MTEXT' else sub_entity.dxf.text
                        process_text_with_spatial_filter(text_content,
                                                         (sub_entity.dxf.insert.x, sub_entity.dxf.insert.y), entity)
            except Exception:
                pass
    used_ys = set()
    for x_item in found_xs:
        best_y_idx, min_dist = -1, float('inf')
        for idx, y_item in enumerate(found_ys):
            if idx in used_ys:
                continue
            dist = _distance_2d(x_item["pos"], y_item["pos"])
            if dist < min_dist:
                min_dist = dist
                best_y_idx = idx
        if best_y_idx != -1 and min_dist < 5000.0:
            vx, vy = x_item["val"], found_ys[best_y_idx]["val"]
            if _distance_2d((vx, vy), x_item["pos"]) > 1000000.0:
                continue
            paired_coordinates.append({
                "X": vx,
                "Y": vy,
                "pos": x_item["pos"]
            })
            used_ys.add(best_y_idx)
    paired_coordinates = filter_spatial_outliers_mad(paired_coordinates, threshold=3.5)
    unique_pairs = []
    seen = set()
    for pt in paired_coordinates:
        if frames and not any(is_point_in_box(pt["pos"], frame) for frame in frames):
            continue
        key = (round(pt["X"], 4), round(pt["Y"], 4))
        if key not in seen:
            seen.add(key)
            unique_pairs.append({"type": "清洗后真实坐标标注", "X": pt["X"], "Y": pt["Y"]})
    if unique_pairs:
        logger.info(f"[主线命中] 空间多级矩阵过滤后，成功抓取到图纸内部的真实有效坐标。")
        final_coordinates = unique_pairs
    else:
        final_coordinates = fallback_axis_grid_in_frames(msp, frames, doc)
        if not final_coordinates:
            final_coordinates = final_fallback_colored_entities(msp, doc)

    formatted_coordinates = []
    for item in final_coordinates:
        clean_item = {
            "Northsouth": str(item["X"]),
            "Eastwest": str(item["Y"])
        }
        formatted_coordinates.append(clean_item)
    return {"coordinates": formatted_coordinates,
            "Elevation": absolute_elevation,
            "Angleton": rotation_angle
            }

def _convert_dwg_to_dxf_via_autocad(dwg_path: str) -> str:
    dwg_absolute = os.path.abspath(dwg_path)
    temp_dir = tempfile.gettempdir()
    safe_temp_name = f"acad_convert_{int(time.time() * 1000)}.dxf"
    dxf_absolute = os.path.join(temp_dir, safe_temp_name)
    if os.path.exists(dxf_absolute):
        try:
            os.remove(dxf_absolute)
        except Exception:
            pass
    acad = None
    doc = None
    is_already_running = False
    dwg_absolute = dwg_absolute.replace("\\", "/")
    dxf_absolute = dxf_absolute.replace("\\", "/")
    try:
        logger.info(f"调用本地 AutoCAD 后台dwg转换dxf: {os.path.basename(dwg_path)} ...")
        try:
            acad = win32com.client.GetActiveObject("AutoCAD.Application")
            is_already_running = True
        except Exception:
            acad = win32com.client.Dispatch("AutoCAD.Application")
            is_already_running = False
        if not is_already_running:
            try:
                acad.Visible = False
            except Exception:
                pass
        try:
            acad.preferences.User.DisplayAlerts = False
        except Exception:
            pass
        try:
            doc = acad.Documents.Open(dwg_absolute, False)
        except Exception:
            doc = acad.Documents.Open(dwg_absolute)
        time.sleep(2.0)
        try:
            doc.SetVariable("FILEDIA", 0)
            doc.SetVariable("EXPERT", 5)
            doc.SendCommand("TEXPLODE\n\n")
            time.sleep(1.0)
            doc.SendCommand("_audit\n_y\n")
            time.sleep(0.5)
            doc.SendCommand("_-purge\n_a\n*\n_n\n")
            time.sleep(0.5)
            # 让后台强选国标字体，解决无界面下的乱码
            reset_font_lisp = '(vlax-for style (vla-get-textstyles (vla-get-activedocument (vlax-get-acad-object))) (vla-put-bigfontfile style "gbcbig.shx"))\n'
            doc.SendCommand(reset_font_lisp)
            time.sleep(0.5)
        except Exception:
            pass
        acR18_DXF = 37
        try:
            doc.SaveAs(dxf_absolute, acR18_DXF)
        except Exception:
            cmd = f'_(command "_.dxfout" "{dxf_absolute}" "V" "2018" "16" "")\n'
            doc.SendCommand(cmd)
        try:
            doc.SetVariable("FILEDIA", 1)
            doc.SetVariable("EXPERT", 0)
        except Exception:
            pass
        file_complete = False
        for i in range(225):
            if os.path.exists(dxf_absolute) and os.path.getsize(dxf_absolute) > 500:
                try:
                    with open(dxf_absolute, 'rb') as f:
                        f.seek(-200, os.SEEK_END)
                        tail_bytes = f.read()
                        if b'EOF' in tail_bytes:
                            file_complete = True
                            break
                except IOError:
                    pass
            time.sleep(0.2)
        if file_complete:
            time.sleep(0.5)
            return dxf_absolute
        raise FileNotFoundError("AutoCAD 转换超时，磁盘未检测到 EOF 闭合标识。")
    except Exception as e:
        raise RuntimeError(f"调用本地 AutoCAD 原生服务链路失败，详细异常: {e}")
    finally:
        try:
            if doc: doc.Close(False)
        except Exception:
            pass
        if not is_already_running and acad:
            try:
                acad.Quit()
            except Exception:
                pass


def auto_process_user_dwg(dwg_path: str) -> Dict[str, Any]:
    if not os.path.exists(dwg_path):
        raise FileNotFoundError(f"未找到用户上传的 DWG 文件: {dwg_path}")
    dxf_temp_path = None
    try:
        dxf_temp_path = _convert_dwg_to_dxf_via_autocad(dwg_path)
        result_data = extract_info_from_dxf_stream(dxf_temp_path)
        return result_data
    finally:
        if dxf_temp_path and os.path.exists(dxf_temp_path):
            try:
                os.remove(dxf_temp_path)
            except Exception:
                pass


if __name__ == "__main__":
    USER_DWG_INPUT = r"D:\project\AI_Document2\data\测试\1F建筑平面图.dwg"
    print("=================== 工业级自适应三维安全围栏流水线启动 ===================")
    if os.path.exists(USER_DWG_INPUT):
        try:
            final_report = auto_process_user_dwg(USER_DWG_INPUT)
            print(json.dumps(final_report, indent=2, ensure_ascii=False))

            # print("\n=================== 业务层成功接收到的最终数据 ===================")
            # print(f"● 绝对标高结果: {final_report['absolute_elevation']} 米")
            # print(f"● 旋转角度结果: {final_report['rotation_angle']}°")
            # print(f"● 智能配对到的位置基准集:")
            # for idx, pt in enumerate(final_report['coordinates'], 1):
            #     if "axis_name" in pt:
            #         print(f"   [{pt['type']}] #{idx} -> {pt['axis_name']} | 几何X: {pt['X']}, 几何Y: {pt['Y']}")
            #     else:
            #         print(f"   [{pt['type']}] #{idx} -> 标注X: {pt['X']}, 标注Y: {pt['Y']}")
            # print("==================================================================")
        except Exception as e:
            print(f"\n[流处理失败]: {e}")
