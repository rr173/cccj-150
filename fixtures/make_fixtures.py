"""合成调查长表 PDF 生成器（ReportLab）。

所有布局以"距页顶"的自上而下坐标计算（与 pdfplumber 一致），
绘制时转换为 ReportLab 的自下而上坐标。每个 fixture 的布局字典
（全部坐标）会保存为 JSON，供 fixtures/expectations.py 独立推导预期，
提取器本身不参与预期的计算。
"""
from __future__ import annotations

import json
import os
import random

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
FONT = "STSong-Light"
SIZE = 10.5

PAGE_W, PAGE_H = 595.0, 842.0
LINE_H = 15.0          # 行内文字行距
PAD = 4.0              # 单元格上内边距
FRAME_X0 = 50.0
FRAME_W = 495.0
FRAME_TOP = 96.0
HEADER_H = 26.0
NAME_TOP = 76.0        # 表名 top（表框上缘 96，相距 20 ≤ name_max_dist 48）
PAGE_HEADER_TOP = 40.0
FOOTER_TOP = 800.0
COLS = ["编号", "地区", "对象", "备注"]
PAGE_HEADER = "调查长表汇编（内部资料）"

# 主表两种列宽（第一页与续页不同；续页编号列需容纳承接标记 Q17（续））
WIDTHS_V1 = [70.0, 70.0, 80.0, 275.0]
WIDTHS_V2 = [80.0, 90.0, 60.0, 265.0]
WIDTHS_V3 = [80.0, 80.0, 70.0, 265.0]


def text_width(s: str) -> float:
    return pdfmetrics.stringWidth(s, FONT, SIZE)


def col_ranges(widths):
    """各列视觉 (x0, x1)。"""
    out = []
    x = FRAME_X0
    for w in widths:
        out.append((x, x + w))
        x += w
    return out


def col_bounds(widths):
    """各列水平边界（公开约定：相邻左对齐列标题中心的中点，两端取表框）。

    表头与单元格均左对齐于视觉列 x0+4，标题两字宽 21pt。
    """
    centers = [x0 + 4.0 + 21.0 / 2.0 for x0, _ in col_ranges(widths)]
    bounds = []
    for i, c in enumerate(centers):
        lo = FRAME_X0 if i == 0 else (centers[i - 1] + c) / 2.0
        hi = FRAME_X0 + FRAME_W if i == len(centers) - 1 else (c + centers[i + 1]) / 2.0
        bounds.append((lo, hi))
    return bounds


class PageSpec:
    """一页的全部绘制内容（自上而下坐标）。"""

    def __init__(self, page_no, table_name, widths, footer):
        self.page_no = page_no
        self.table_name = table_name
        self.widths = widths
        self.footer = footer
        self.frame_bottom = None
        self.texts = []        # (x, top, s, tag)  tag 供预期定位（如 "Q17:备注"）
        self.id_line_ys = []   # 编号列短横线 y
        self.region_line_ys = []

    @property
    def header_sep_y(self):
        return FRAME_TOP + HEADER_H

    @property
    def body_top(self):
        return self.header_sep_y

    def text(self, col, top, s, tag=""):
        x0, x1 = col_ranges(self.widths)[col]
        self.texts.append((x0 + 4.0, top, s, tag))

    def centered_text(self, col, top, s, tag=""):
        x0, x1 = col_ranges(self.widths)[col]
        self.texts.append((x0 + (x1 - x0 - text_width(s)) / 2.0, top, s, tag))

    def id_line(self, y):
        self.id_line_ys.append(y)

    def region_line(self, y):
        self.region_line_ys.append(y)

    # ---- 行堆叠 ----
    def stack(self, rows, seps_below=True):
        """rows: [(rid, {col: [lines]})]；返回 (结束 y, 每行底界 y 列表)。"""
        y = self.body_top
        bottoms = []
        for rid, cells in rows:
            nlines = max([len(v) for v in cells.values()] + [1])
            h = nlines * LINE_H + 2 * PAD
            for col_idx, lines in cells.items():
                for i, s in enumerate(lines):
                    self.text(col_idx, y + PAD + i * LINE_H, s, tag=f"{rid}:{COLS[col_idx]}")
            y += h
            bottoms.append(y)
        return y, bottoms


def draw_page(c, spec: PageSpec, shuffle=False, rng=None):
    ops = []

    def text_op(x, top, s):
        def go():
            c.setFont(FONT, SIZE)
            c.drawString(x, PAGE_H - top - SIZE * 0.85, s)
        return go

    def hline_op(x0, top, x1):
        def go():
            c.setLineWidth(0.8)
            c.line(x0, PAGE_H - top, x1, PAGE_H - top)
        return go

    # 页眉 / 表名 / 页脚
    ops.append(text_op((PAGE_W - text_width(PAGE_HEADER)) / 2, PAGE_HEADER_TOP, PAGE_HEADER))
    ops.append(text_op(FRAME_X0, NAME_TOP, spec.table_name))
    ops.append(text_op((PAGE_W - text_width(spec.footer)) / 2, FOOTER_TOP, spec.footer))
    # 表框
    fb = spec.frame_bottom

    def frame_op():
        c.setLineWidth(1.0)
        c.rect(FRAME_X0, PAGE_H - fb, FRAME_W, fb - FRAME_TOP)
    ops.append(frame_op)
    # 表头（与单元格一致左对齐：列标题中心的中点即列界，文字不跨界）
    for i, name in enumerate(COLS):
        x0, x1 = col_ranges(spec.widths)[i]
        ops.append(text_op(x0 + 4.0, FRAME_TOP + 7, name))
    ops.append(hline_op(FRAME_X0, spec.header_sep_y, FRAME_X0 + FRAME_W))
    # 正文文字
    for x, top, s, _tag in spec.texts:
        ops.append(text_op(x, top, s))
    # 编号列短横线（画在该列水平边界内）
    id_lo, id_hi = col_bounds(spec.widths)[0]
    for y in spec.id_line_ys:
        ops.append(hline_op(id_lo + 6, y, id_hi - 6))
    # 地区列短横线
    r_lo, r_hi = col_bounds(spec.widths)[1]
    for y in spec.region_line_ys:
        ops.append(hline_op(r_lo + 6, y, r_hi - 6))

    if shuffle:
        rng.shuffle(ops)
    for op in ops:
        op()


def render_pdf(path, specs, shuffle=False, seed=0):
    rng = random.Random(seed)
    c = canvas.Canvas(path, pagesize=(PAGE_W, PAGE_H))
    for spec in specs:
        draw_page(c, spec, shuffle=shuffle, rng=rng)
        c.showPage()
    c.save()


def layout_dict(specs):
    """布局字典：坐标全部在此，供独立预期使用。"""
    out = []
    for spec in specs:
        out.append(
            {
                "page": spec.page_no,
                "table_name": spec.table_name,
                "widths": spec.widths,
                "frame": [FRAME_X0, FRAME_TOP, FRAME_X0 + FRAME_W, spec.frame_bottom],
                "header_sep_y": spec.header_sep_y,
                "body_top": spec.body_top,
                "texts": [
                    {"x": x, "top": t, "text": s, "tag": tag} for x, t, s, tag in spec.texts
                ],
                "id_line_ys": list(spec.id_line_ys),
                "region_line_ys": list(spec.region_line_ys),
            }
        )
    return out


# ================================================================ 各 fixture
def build_main():
    """主例：两种列宽、页脚、页眉、跨行地区、折行备注、跨页未完行。

    Q17 的备注末半在第二页（承接标记 Q17（续）），Q18 从该页中部开始。
    """
    p1 = PageSpec(1, "表A：人居环境调查长表", WIDTHS_V1, "- 1 -")
    rows1 = [
        ("Q11", {0: ["Q11"], 2: ["张某"], 3: ["实地走访记录完整"]}),
        ("Q12", {0: ["Q12"], 2: ["李某"]}),                      # 备注空单元格
        ("Q13", {0: ["Q13"], 2: ["王某"], 3: ["备注第一行文字", "备注第二行文字"]}),
        ("Q14", {0: ["Q14"], 2: ["赵某"], 3: ["单次访谈"]}),
        ("Q15", {0: ["Q15"]}),                                    # 对象、备注均空
        ("Q16", {0: ["Q16"], 2: ["周某"], 3: ["电话复核无误"]}),
        ("Q17", {0: ["Q17"], 2: ["吴某"],
                 3: ["第一部分记录：房屋结构完好", "院落排水需要整修"]}),
    ]
    y, bottoms = p1.stack(rows1)
    # 行间短横线（含 Q17 之下一条：续页承接时会被标记规则过滤）
    for b in bottoms:
        p1.id_line(b)
    # 地区「北部」跨 Q11–Q12：上界=表体上缘，下界=Q12 行底
    p1.region_line(p1.body_top)
    p1.region_line(bottoms[1])
    mid = (p1.body_top + bottoms[1]) / 2
    p1.centered_text(1, mid - SIZE / 2, "北部", tag="region:北部")
    p1.frame_bottom = bottoms[-1] + 27.0

    p2 = PageSpec(2, "表A：人居环境调查长表", WIDTHS_V2, "- 2 -")
    cont = [
        ("Q17续", {0: ["Q17（续）"], 3: ["杂物堆放已现场清理", "后续电话回访确认"]}),
        ("Q18", {0: ["Q18"], 2: ["郑某"], 3: ["新开工地已备案"]}),
        ("Q19", {0: ["Q19"], 2: ["孙某"]}),
    ]
    y2, bottoms2 = p2.stack(cont)
    p2.id_line(bottoms2[0])   # Q17 续段之下（Q17–Q18 槽位的选中行界）
    p2.id_line(bottoms2[1])   # Q18–Q19 之间
    # 地区「南部」跨 Q18–Q19：上界=Q18 行顶（即续段底），下界=表体下缘
    p2.region_line(bottoms2[0])
    p2.frame_bottom = bottoms2[-1] + 14.0
    p2.region_line(p2.frame_bottom)
    mid2 = (bottoms2[0] + p2.frame_bottom) / 2
    p2.centered_text(1, mid2 - SIZE / 2, "南部", tag="region:南部")
    return [p1, p2]


def build_ambiguous():
    """一段文字落在同一对编号的两条合法候选行界之间 → 歧义见证。"""
    p1 = PageSpec(1, "表B：行界歧义示例", WIDTHS_V1, "- 1 -")
    rows = [("Q61", {0: ["Q61"], 2: ["甲某"], 3: ["六一备注"]})]
    y, bottoms = p1.stack(rows)
    c1 = bottoms[0]
    p1.id_line(c1)                       # 候选行界 1
    # 浮动文字带（不属于任何编号行）
    p1.text(3, c1 + PAD, "待补充材料", tag="float:备注")
    y2 = c1 + LINE_H + 2 * PAD
    p1.id_line(y2)                       # 候选行界 2
    rows2 = [
        ("Q62", {0: ["Q62"], 2: ["乙某"], 3: ["六二备注"]}),
        ("Q63", {0: ["Q63"], 2: ["丙某"], 3: ["六三备注"]}),
    ]
    y3 = y2
    for rid, cells in rows2:
        nlines = max(len(v) for v in cells.values())
        h = nlines * LINE_H + 2 * PAD
        for col_idx, lines in cells.items():
            for i, s in enumerate(lines):
                p1.text(col_idx, y3 + PAD + i * LINE_H, s, tag=f"{rid}:{COLS[col_idx]}")
        y3 += h
        if rid != "Q63":
            p1.id_line(y3)
    p1.frame_bottom = y3 + 14.0
    return [p1]


def build_same_text():
    """相同文字的不同来源：两种解释拼出的文本相同，来源归属不同。"""
    p1 = PageSpec(1, "表E：同文异源示例", WIDTHS_V1, "- 1 -")
    rows = [("Q51", {0: ["Q51"], 2: ["甲某"], 3: ["同上"]})]
    y, bottoms = p1.stack(rows)
    c1 = bottoms[0]
    p1.id_line(c1)
    p1.text(3, c1 + PAD, "同上", tag="float:备注")
    y2 = c1 + LINE_H + 2 * PAD
    p1.id_line(y2)
    rows2 = [
        ("Q52", {0: ["Q52"], 2: ["乙某"], 3: ["同上"]}),
        ("Q53", {0: ["Q53"], 2: ["丙某"], 3: ["五三备注"]}),
    ]
    y3 = y2
    for rid, cells in rows2:
        h = LINE_H + 2 * PAD
        for col_idx, lines in cells.items():
            for i, s in enumerate(lines):
                p1.text(col_idx, y3 + PAD + i * LINE_H, s, tag=f"{rid}:{COLS[col_idx]}")
        y3 += h
        if rid != "Q53":
            p1.id_line(y3)
    p1.frame_bottom = y3 + 14.0
    return [p1]


def build_missing_id():
    """缺编号：一个无编号可视行 → 两种合法归属 → 隔离，不臆造编号。"""
    p1 = PageSpec(1, "表F：缺编号示例", WIDTHS_V1, "- 1 -")
    rows = [("Q71", {0: ["Q71"], 2: ["甲某"], 3: ["七一备注"]})]
    y, bottoms = p1.stack(rows)
    c1 = bottoms[0]
    p1.id_line(c1)
    p1.text(3, c1 + PAD, "无编号行的备注", tag="float:备注")   # 该行没有编号
    y2 = c1 + LINE_H + 2 * PAD
    p1.id_line(y2)
    rows2 = [
        ("Q72", {0: ["Q72"], 2: ["乙某"], 3: ["七二备注"]}),
        ("Q73", {0: ["Q73"], 2: ["丙某"], 3: ["七三备注"]}),
    ]
    y3 = y2
    for rid, cells in rows2:
        h = LINE_H + 2 * PAD
        for col_idx, lines in cells.items():
            for i, s in enumerate(lines):
                p1.text(col_idx, y3 + PAD + i * LINE_H, s, tag=f"{rid}:{COLS[col_idx]}")
        y3 += h
        if rid != "Q73":
            p1.id_line(y3)
    p1.frame_bottom = y3 + 14.0
    return [p1]


def build_region_conflict():
    """地区区间冲突：区间下界穿过行中部，无法对齐任何合法行界 → 结构损坏。"""
    p1 = PageSpec(1, "表G：地区区间冲突示例", WIDTHS_V1, "- 1 -")
    rows = [
        ("Q81", {0: ["Q81"], 2: ["甲某"], 3: ["八一备注"]}),
        ("Q82", {0: ["Q82"], 2: ["乙某"], 3: ["八二第一行", "八二第二行"]}),
        ("Q83", {0: ["Q83"], 2: ["丙某"], 3: ["八三备注"]}),
    ]
    y, bottoms = p1.stack(rows)
    p1.id_line(bottoms[0])
    p1.id_line(bottoms[1])
    # 地区「东部」：上界=表体上缘，下界故意落在 Q82 行中部（非任何行界）
    p1.region_line(p1.body_top)
    bad_y = bottoms[0] + LINE_H   # Q82 第二行文字附近
    p1.region_line(bad_y)
    mid = (p1.body_top + bad_y) / 2
    p1.centered_text(1, mid - SIZE / 2, "东部", tag="region:东部")
    p1.frame_bottom = bottoms[-1] + 14.0
    return [p1]


def build_title_mismatch():
    """续页标题不符：第二页表名不同 → 不承接；两侧列待核实。"""
    p1 = PageSpec(1, "表D：核查询问记录", WIDTHS_V1, "- 1 -")
    rows = [("Q41", {0: ["Q41"], 2: ["甲某"], 3: ["四一备注上半部分"]})]
    y, bottoms = p1.stack(rows)
    p1.frame_bottom = bottoms[-1] + 14.0

    p2 = PageSpec(2, "表X：另一份独立表", WIDTHS_V1, "- 2 -")
    # 表首无编号片段（上一页遗留，但表名不符，不得承接）
    p2.text(3, p2.body_top + PAD, "这是上一页遗留的片段文字", tag="float:备注")
    y2 = p2.body_top + LINE_H + 2 * PAD
    rows2 = [("Q42", {0: ["Q42"], 2: ["乙某"], 3: ["四二备注"]})]
    for rid, cells in rows2:
        h = LINE_H + 2 * PAD
        for col_idx, lines in cells.items():
            for i, s in enumerate(lines):
                # 手工把 Q42 行放到片段之下
                p2.text(col_idx, y2 + PAD + i * LINE_H, s, tag=f"{rid}:{COLS[col_idx]}")
        y2 += h
    p2.frame_bottom = y2 + 14.0
    return [p1, p2]


def build_three_page():
    """三页连续承接：Q30 的备注跨三页，Q31 从第三页中部开始。"""
    p1 = PageSpec(1, "表C：三页承接示例", WIDTHS_V1, "- 1 -")
    rows = [("Q30", {0: ["Q30"], 2: ["甲某"],
                     3: ["三页备注第一段", "三页备注第二段"]})]
    y, bottoms = p1.stack(rows)
    p1.id_line(bottoms[0])   # 会被承接标记规则过滤
    p1.frame_bottom = bottoms[-1] + 20.0

    p2 = PageSpec(2, "表C：三页承接示例", WIDTHS_V2, "- 2 -")
    cont = [("Q30续", {0: ["Q30（续）"], 3: ["三页备注第三段", "三页备注第四段"]})]
    y2, bottoms2 = p2.stack(cont)
    p2.id_line(bottoms2[0])  # 同样会被过滤
    p2.frame_bottom = bottoms2[-1] + 20.0

    p3 = PageSpec(3, "表C：三页承接示例", WIDTHS_V3, "- 3 -")
    cont3 = [
        ("Q30续", {0: ["Q30（续）"], 3: ["三页备注第五段"]}),
        ("Q31", {0: ["Q31"], 2: ["乙某"], 3: ["三一备注"]}),
    ]
    y3, bottoms3 = p3.stack(cont3)
    p3.id_line(bottoms3[0])  # Q30–Q31 槽位的选中行界
    p3.frame_bottom = bottoms3[-1] + 14.0
    return [p1, p2, p3]


BUILDERS = {
    "main": build_main,
    "ambiguous": build_ambiguous,
    "same_text": build_same_text,
    "missing_id": build_missing_id,
    "region_conflict": build_region_conflict,
    "title_mismatch": build_title_mismatch,
    "three_page": build_three_page,
}


def build_all(outdir):
    os.makedirs(outdir, exist_ok=True)
    laydir = os.path.join(outdir, "layout")
    os.makedirs(laydir, exist_ok=True)
    paths = {}
    for name, builder in BUILDERS.items():
        specs = builder()
        path = os.path.join(outdir, f"{name}.pdf")
        render_pdf(path, specs)
        paths[name] = path
        with open(os.path.join(laydir, f"{name}.layout.json"), "w", encoding="utf-8") as fh:
            json.dump(layout_dict(specs), fh, ensure_ascii=False, indent=1)
    # 绘字顺序变体：视觉布局相同，内部绘字顺序不同
    specs = build_main()
    path = os.path.join(outdir, "draw_order.pdf")
    render_pdf(path, specs, shuffle=True, seed=42)
    paths["draw_order"] = path
    with open(os.path.join(laydir, "draw_order.layout.json"), "w", encoding="utf-8") as fh:
        json.dump(layout_dict(specs), fh, ensure_ascii=False, indent=1)
    return paths


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "out/fixtures"
    for k, v in build_all(out).items():
        print(k, "->", v)
