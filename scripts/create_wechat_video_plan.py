from __future__ import annotations

from pathlib import Path
from shutil import copy2

from PIL import Image
from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(r"C:\Users\jly23\Desktop\OpenManus-main")
OUT = ROOT / "output" / "wechat_video_plan"
TEMP = Path(r"C:\Users\jly23\AppData\Local\Temp")
GEN = Path(r"C:\Users\jly23\.codex\generated_images\019fd199-157e-7372-9666-39ac708a25e5\exec-b8a55043-9306-4bfd-af0e-2e012ba5214a.png")


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_cell_width(cell, twips: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(twips))
    tc_w.set(qn("w:type"), "dxa")


def set_table_geometry(table, widths: list[int]) -> None:
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(sum(widths)))
    tbl_w.set(qn("w:type"), "dxa")
    tbl_ind = OxmlElement("w:tblInd")
    tbl_ind.set(qn("w:w"), "120")
    tbl_ind.set(qn("w:type"), "dxa")
    tbl_pr.append(tbl_ind)
    grid = table._tbl.tblGrid
    for col, width in zip(grid.gridCol_lst, widths):
        col.set(qn("w:w"), str(width))
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            set_cell_width(cell, width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def set_font(run, size=11, bold=False, color="1F2937") -> None:
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    run._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def add_text(cell, text: str, size=9.2, bold=False, color="1F2937") -> None:
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.12
    set_font(p.add_run(text), size=size, bold=bold, color=color)


def add_body(doc: Document, text: str, *, size=10.5, after=6, color="1F2937") -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.25
    set_font(p.add_run(text), size=size, color=color)


def add_h1(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after = Pt(8)
    set_font(p.add_run(text), size=16, bold=True, color="1F4D78")


def add_caption(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(10)
    set_font(p.add_run(text), size=8.5, color="5B6472")


def save_crops() -> dict[str, Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    source = {
        "agent_progress": TEMP / "企业微信截图_17859260771948.png",
        "revit_before": TEMP / "企业微信截图_17859261563337.png",
        "quality_report": TEMP / "企业微信截图_17859265477669.png",
    }
    crops = {
        "01_agent_workflow.png": (source["agent_progress"], (675, 210, 1425, 555)),
        "02_revit_before.png": (source["revit_before"], (42, 115, 880, 770)),
        "04_quality_report.png": (source["quality_report"], (655, 345, 1548, 615)),
    }
    paths: dict[str, Path] = {}
    for name, (src, box) in crops.items():
        with Image.open(src) as image:
            output = OUT / name
            image.crop(box).save(output)
            paths[name] = output
    after = OUT / "03_revit_after.png"
    copy2(GEN, after)
    paths[after.name] = after
    return paths


def build_doc(images: dict[str, Path]) -> Path:
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.72)
    section.right_margin = Inches(0.72)
    section.header_distance = Inches(0.3)
    section.footer_distance = Inches(0.3)

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(header.add_run("AI 智建 | 微信公众号视频制作规划"), size=8.5, color="697586")

    title = doc.add_paragraph()
    title.paragraph_format.space_after = Pt(4)
    set_font(title.add_run("CAD-Revit-IFC 智能交付闭环"), size=25, bold=True, color="123B5D")
    subtitle = doc.add_paragraph()
    subtitle.paragraph_format.space_after = Pt(14)
    set_font(subtitle.add_run("微信公众号短视频前期制作规划 | 成片建议：90 秒"), size=12, color="536779")

    strip = doc.add_table(rows=1, cols=3)
    set_table_geometry(strip, [3120, 3120, 3120])
    for cell, (label, value) in zip(strip.rows[0].cells, [
        ("成片规格", "横版 16:9｜90 秒"),
        ("表达策略", "技术节点优先｜快节奏"),
        ("交付目标", "模型、IFC、质检报告"),
    ]):
        set_cell_shading(cell, "E8EEF5")
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(1)
        set_font(p.add_run(label + "\n"), size=8.5, bold=True, color="1F4D78")
        set_font(p.add_run(value), size=10, color="1F2937")

    add_h1(doc, "制作定位")
    add_body(doc, "面向微信公众号的技术成果展示：用“输入图纸—智能建模—IFC 语义赋值—报建质检”串联全过程。画面以软件结果、模型前后对比和交付物为主，不保留等待、重复点击或泛化说明。")
    add_body(doc, "旁白原则：只讲可验证的技术动作和结果。保留“轴网对应关系计算平移向量”“三级层级分组”“Top-K 候选复核”“文件指纹校验”等技术点；删除“为保证可靠性”“系统自动完成”等无信息密度表述。", color="1F4D78")

    add_h1(doc, "90 秒分镜与旁白")
    table = doc.add_table(rows=1, cols=4)
    set_table_geometry(table, [820, 2240, 3600, 2700])
    headers = ["时间", "画面与剪辑", "旁白", "字幕关键词"]
    for cell, text in zip(table.rows[0].cells, headers):
        set_cell_shading(cell, "DDE8F2")
        add_text(cell, text, size=9, bold=True, color="1F4D78")
    rows = [
        ("0–06s", "片头：项目路径、DWG/RVT/IFC 文件快速闪现。", "这是一个面向 BIM 交付的本地智能体流程：从 CAD 图纸到 IFC 质检报告，形成可追溯的交付闭环。", "CAD · Revit · IFC · SZ-IFC"),
        ("06–20s", "切入 Revit/CAD 对照；用 1.3 倍速，保留轴网和图纸画面。", "系统读取各楼层 DWG 的轴网与房间文字，通过 CAD 与 Revit 轴网的对应关系计算平移向量，并完成坐标对齐校验。", "轴网配准｜平移向量｜坐标校验"),
        ("20–34s", "房间创建结果与楼层画面；前后对比可用左右分屏。", "校验通过后，Revit 插件批量创建闭合区域房间，再按楼层将 CAD 房间名称写入模型，输出独立的房间处理版本。", "批量建房｜房间命名｜版本另存"),
        ("34–53s", "展示 IFC 参数栏与彩色模型；从局部拉远至全图。", "随后清除旧 IFC 标识，按构件类别、族和类型进行三级层级分组，查询深圳标准 IFC 候选集。", "IFC 重建｜三级分组｜深圳标准"),
        ("53–66s", "保留赋参结果卡片；配合参数栏放大。", "程序先对候选标识进行确定性排序，再由大模型在 Top-K 候选中做结构化复核，最后批量回写 IdentName、IdentID 和专业标识。", "Top-K 复核｜结构化 JSON｜批量赋参"),
        ("66–77s", "展示流程结果中的 IFC 导出阶段；用文件路径动画替代等待。", "已赋参模型被保存到 ifc-assigned 检查点，再导出 IFC。系统同时校验 IFC 与配套表格的文件指纹，确认交付文件真实落盘。", "检查点｜IFC 导出｜文件指纹"),
        ("77–88s", "切入 SZ-IFC 质检结果与报告路径；路径可模糊处理。", "在 SZ-IFC 中选择当前专业的深圳 BIM 交付规则，自动执行模型检查并导出 Word 质检报告。", "规则匹配｜模型检查｜Word 报告"),
        ("88–90s", "四项交付物快速收束：RVT、IFC、配套表格、DOCX。", "让模型交付从人工串行操作，升级为可复用、可审计的智能工作流。", "可复用｜可审计｜交付闭环"),
    ]
    for values in rows:
        cells = table.add_row().cells
        for index, (cell, value) in enumerate(zip(cells, values)):
            if index == 0:
                set_cell_shading(cell, "F4F6F9")
                add_text(cell, value, size=8.8, bold=True, color="1F4D78")
            elif index == 3:
                add_text(cell, value, size=8.5, bold=True, color="536779")
            else:
                add_text(cell, value, size=8.9)

    doc.add_page_break()
    add_h1(doc, "关键画面裁剪与使用方式")
    add_body(doc, "下列图片已按解说节点裁剪，作为剪辑员的选段参考。正式成片应直接从对应 MP4 的同位置截取无播放器控件的原始画面；文档图片用于明确构图、放大区域和转场关系。", size=9.5, color="536779")

    frame_table = doc.add_table(rows=1, cols=2)
    set_table_geometry(frame_table, [4680, 4680])
    items = [
        ("02_revit_before.png", "画面 1｜执行前：保留 Revit 属性栏与模型平面。", "用于 06–12 秒，承接 CAD-Revit 坐标配准。"),
        ("03_revit_after.png", "画面 2｜执行后：彩色空间结果与 IFC 参数。", "用于 20–34 秒和 34–53 秒，建议局部放大参数栏后拉远。"),
        ("01_agent_workflow.png", "画面 3｜工作流结果：房间创建、IFC 赋参、模型另存。", "用于 53–77 秒，突出阶段输出，不停留在长文本上。"),
        ("04_quality_report.png", "画面 4｜SZ-IFC 质检报告输出。", "用于 77–88 秒；报告绝对路径在正式成片中建议打码。"),
    ]
    for i, (name, caption, note) in enumerate(items):
        cell = frame_table.cell(i // 2, i % 2) if i < 2 else None
        if i == 2:
            cells = frame_table.add_row().cells
            cell = cells[0]
        elif i == 3:
            cell = frame_table.rows[1].cells[1]
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cell.paragraphs[0].add_run().add_picture(str(images[name]), width=Inches(2.95))
        p = cell.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(2)
        set_font(p.add_run(caption), size=8.8, bold=True, color="1F4D78")
        p = cell.add_paragraph()
        p.paragraph_format.space_after = Pt(5)
        set_font(p.add_run(note), size=8.2, color="536779")

    add_h1(doc, "剪辑执行清单")
    checklist = [
        "成片横版 16:9，1920×1080；原始长时等待、窗口切换和重复点击全部删除。",
        "CAD/Revit 画面使用 1.2–1.5 倍速；流程日志使用 2–3 倍速，并以关键结果帧定格 1 秒。",
        "字幕采用两层：底部完整旁白字幕，左上角技术关键词；每屏关键词不超过 10 个汉字。",
        "保留“前—后”模型对比；IFC 参数栏、报告路径和最终交付物采用局部放大。",
        "隐去本地绝对路径、用户名及与项目无关的窗口信息；报告页保留“已生成”结果即可。",
        "配音节奏控制在每秒约 3.6–4.0 个汉字，背景音乐低于人声 16–20 dB。",
    ]
    for item in checklist:
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.line_spacing = 1.15
        set_font(p.add_run(item), size=9.5)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(footer.add_run("视频制作前期规划｜内部剪辑参考"), size=8, color="697586")

    output = OUT / "CAD-Revit-IFC_微信公众号90秒视频制作规划.docx"
    doc.save(output)
    return output


if __name__ == "__main__":
    print(build_doc(save_crops()))
