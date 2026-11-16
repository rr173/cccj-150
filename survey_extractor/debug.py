"""调试页：把每个表框页的布局证据与来源框画成可核对的 PDF。

纯示意（按真实坐标绘制，不依赖渲染原 PDF）：
- 黑框=表框，蓝带=表头，灰虚线=列边界；
- 字块按所属记录着色并标注 bid；橙=无编号片段，紫=地区字块，绿=承接标记；
- 绿实线=选中行界（解释 A），红虚线=解释 B 的行界（歧义时）；
- 紫色竖括号=地区区间；蓝虚线=跨页接缝。
"""
from __future__ import annotations

import os

from .extract import Extraction
from .util import gy_to_page

ROW_COLORS = [
    (0.80, 0.90, 1.00), (0.85, 1.00, 0.85), (1.00, 0.95, 0.80), (1.00, 0.85, 0.90),
    (0.90, 0.85, 1.00), (0.85, 1.00, 0.95), (1.00, 0.90, 0.80), (0.90, 1.00, 0.85),
]


def render_debug(ext: Extraction, outdir: str) -> list:
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        font = "STSong-Light"
    except Exception:
        font = "Helvetica"

    doc, cfg = ext.doc, ext.cfg
    os.makedirs(outdir, exist_ok=True)
    written = []
    for tbl in doc.tables:
        # 行号着色映射：solution[0] 的归属
        row_of_bid = {}
        if tbl.solutions:
            row_of_bid = dict(tbl.solutions[0].assign)
        anchor_row = {b.bid: i for i, b in enumerate(tbl.anchors)}
        for fid in tbl.frames:
            f = doc.frames[fid]
            pw, ph = doc.page_sizes[f.page - 1]
            path = os.path.join(outdir, f"table{tbl.tid}_page{f.page}.pdf")
            c = _canvas.Canvas(path, pagesize=(pw, ph))
            c.setFont(font, 9)
            c.drawString(20, ph - 20, f"表「{tbl.name}」 第 {f.page} 页  状态={tbl.status}")

            def Y(top):
                return ph - top

            # 表框与表头带
            c.setStrokeColorRGB(0, 0, 0)
            c.rect(f.x0, Y(f.bottom), f.x1 - f.x0, f.bottom - f.top)
            if f.header_sep is not None:
                c.setFillColorRGB(0.85, 0.90, 1.0)
                c.rect(f.x0, Y(f.header_sep), f.x1 - f.x0, f.header_sep - f.top, stroke=0, fill=1)
            # 列边界
            c.setStrokeColorRGB(0.6, 0.6, 0.6)
            c.setDash(3, 3)
            for lo, hi in f.bounds:
                c.line(lo, Y(f.bottom), lo, Y(f.top))
                c.line(hi, Y(f.bottom), hi, Y(f.top))
            c.setDash()
            # 字块
            for b in doc.blocks:
                if b.frame != fid or b.role != "body":
                    continue
                if b.kind == "fragment":
                    color = (1.0, 0.65, 0.2)
                elif b.kind == "marker":
                    color = (0.4, 0.9, 0.4)
                elif b.col == (tbl.columns.index(cfg.region_column_name)
                               if cfg.region_column_name in tbl.columns else -2):
                    color = (0.85, 0.6, 0.95)
                else:
                    row = anchor_row.get(b.bid, row_of_bid.get(b.bid))
                    color = ROW_COLORS[row % len(ROW_COLORS)] if row is not None else (0.95, 0.95, 0.95)
                c.setFillColorRGB(*color)
                c.setStrokeColorRGB(0.2, 0.2, 0.2)
                c.rect(b.x0, Y(b.bottom), b.x1 - b.x0, b.bottom - b.top, stroke=1, fill=1)
                c.setFillColorRGB(0, 0, 0)
                c.setFont(font, 5)
                c.drawString(b.x0 + 1, Y(b.bottom) + 1, f"{b.bid}:{b.text[:12]}")
            # 选中行界（解释 A 绿实线 / 解释 B 红虚线）
            bounds_sets = []
            if tbl.solutions:
                bounds_sets.append((tbl.solutions[0], (0, 0.6, 0), []))
            if len(tbl.solutions) > 1 and tbl.groups and tbl.groups[0].get("witnesses"):
                # 用隔离组见证中的第二份解释
                w = tbl.groups[0]["witnesses"]
                if len(w) > 1:
                    bounds_sets.append((None, (0.85, 0, 0), [4, 3]))
            for si, (sol, rgb, dash) in enumerate(bounds_sets):
                if sol is not None:
                    gys = [b.gy for b in sol.boundaries]
                else:
                    gys = [b_["gy"] for b_ in tbl.groups[0]["witnesses"][1]["boundaries"][1:-1]]
                c.setStrokeColorRGB(*rgb)
                c.setLineWidth(1.2 if si == 0 else 0.9)
                if dash:
                    c.setDash(*dash)
                for gy in gys:
                    if f.y_off - 0.5 <= gy <= f.y_off + f.body_height + 0.5:
                        y = gy - f.y_off + f.body_top
                        c.line(f.x0, Y(y), f.x1, Y(y))
                c.setDash()
            c.setLineWidth(1)
            # 地区区间
            c.setStrokeColorRGB(0.6, 0.1, 0.7)
            for iv in tbl.region_intervals:
                for gy in (iv.gy_top, iv.gy_bottom):
                    if f.y_off - 0.5 <= gy <= f.y_off + f.body_height + 0.5:
                        y = gy - f.y_off + f.body_top
                        c.line(f.x0 + 2, Y(y), f.x0 + 10, Y(y))
                mid = (iv.gy_top + iv.gy_bottom) / 2
                if f.y_off <= mid <= f.y_off + f.body_height:
                    c.setFont(font, 6)
                    c.setFillColorRGB(0.6, 0.1, 0.7)
                    c.drawString(f.x0 + 2, Y(mid - f.y_off + f.body_top), f"地区:{iv.value}")
                    c.setFillColorRGB(0, 0, 0)
            # 接缝
            c.setStrokeColorRGB(0.1, 0.3, 0.9)
            c.setDash(6, 3)
            for s in tbl.seams:
                nf = doc.frames[s.next_frame]
                if nf.fid == fid:
                    c.line(f.x0, Y(f.body_top), f.x1, Y(f.body_top))
                    c.setFont(font, 6)
                    c.drawString(f.x1 - 60, Y(f.body_top) - 2, f"接缝 {s.kind}")
            c.setDash()
            c.setFont(font, 7)
            c.drawString(20, 20, "绿实线=选中行界 红虚线=第二解释 橙=无编号片段 紫=地区 绿块=承接标记 数字=bid")
            c.save()
            written.append(path)
    # 索引
    import json

    idx = os.path.join(outdir, "index.json")
    with open(idx, "w", encoding="utf-8") as fh:
        json.dump({"debug_pages": written}, fh, ensure_ascii=False, indent=1)
    written.append(idx)
    return written
