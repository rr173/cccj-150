"""PDF 读取层：pdfplumber → 字块 / 横线 / 表框。

只处理横排、无旋转、字形可提取的文字层：
- 页面 rotation != 0 → 文档级结构问题，跳过该页；
- 字符要求 upright 且 size > 0；
- 不做 OCR，不修改任何字符。

字块归并规则（公开，见 Config）：
1. 按纵向中心聚类成视觉行（容差 line_y_tol）；
2. 行内按 x 排序，相邻字符水平间隙超过 char_gap_tol 则切分字块；
3. 字块 id 按 (页码, top, x0) 排序后统一编号 —— 与 PDF 内部绘字顺序无关。
"""
from __future__ import annotations

from .config import Config
from .model import Block, HLine, Frame, WorkDoc


def chars_to_blocks(chars, page_no: int, cfg: Config) -> list:
    if not chars:
        return []
    pts = sorted(chars, key=lambda c: ((c["top"] + c["bottom"]) / 2.0, c["x0"]))
    lines = []
    for ch in pts:
        c = (ch["top"] + ch["bottom"]) / 2.0
        if lines and abs(c - lines[-1]["center"]) <= cfg.line_y_tol:
            lines[-1]["chars"].append(ch)
            k = len(lines[-1]["chars"])
            lines[-1]["center"] += (c - lines[-1]["center"]) / k
        else:
            lines.append({"center": c, "chars": [ch]})
    blocks = []
    for ln in lines:
        cs = sorted(ln["chars"], key=lambda c: c["x0"])
        cur = [cs[0]]
        for ch in cs[1:]:
            if ch["x0"] - cur[-1]["x1"] > cfg.char_gap_tol:
                blocks.append(_mk_block(cur, page_no))
                cur = [ch]
            else:
                cur.append(ch)
        blocks.append(_mk_block(cur, page_no))
    return blocks


def _mk_block(cs, page_no: int) -> Block:
    return Block(
        bid=-1,
        page=page_no,
        x0=min(c["x0"] for c in cs),
        top=min(c["top"] for c in cs),
        x1=max(c["x1"] for c in cs),
        bottom=max(c["bottom"] for c in cs),
        text="".join(c["text"] for c in cs),
    )


def extract_hlines(page, page_no: int) -> list:
    """水平线：page.edges 中 orientation == 'h' 的边（含 rect 的上下边），去重。"""
    seen = set()
    out = []
    for e in page.edges:
        if e.get("orientation") != "h":
            continue
        key = (round(e["top"], 1), round(e["x0"], 1), round(e["x1"], 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(HLine(page=page_no, x0=e["x0"], x1=e["x1"], y=e["top"]))
    return out


def detect_frames(page, page_no: int, cfg: Config) -> list:
    """表框：足够大的矩形。支持一页多框；重叠框报结构问题。"""
    frames = []
    for r in page.rects:
        w = r["x1"] - r["x0"]
        h = r["bottom"] - r["top"]
        if w >= cfg.frame_min_width and h >= cfg.frame_min_height:
            frames.append(
                Frame(
                    fid=-1,
                    page=page_no,
                    x0=r["x0"],
                    top=r["top"],
                    x1=r["x1"],
                    bottom=r["bottom"],
                )
            )
    frames.sort(key=lambda f: (f.top, f.x0))
    return frames


def read_pdf(path: str, cfg: Config) -> WorkDoc:
    import pdfplumber

    doc = WorkDoc(path=path)
    with pdfplumber.open(path) as pdf:
        doc.page_count = len(pdf.pages)
        for pno, page in enumerate(pdf.pages, start=1):
            doc.page_sizes.append((float(page.width), float(page.height)))
            if page.rotation != 0:
                doc.problems.append(
                    {
                        "code": "unsupported_rotation",
                        "message": f"第 {pno} 页存在旋转（rotation={page.rotation}），"
                        "支持范围仅含无旋转横排文字层，已跳过该页",
                        "page": pno,
                    }
                )
                continue
            chars = [
                ch
                for ch in page.chars
                if ch.get("upright", True) and ch.get("size", 0) > 0 and ch.get("text")
            ]
            doc.blocks.extend(chars_to_blocks(chars, pno, cfg))
            doc.hlines.extend(extract_hlines(page, pno))
            doc.frames.extend(detect_frames(page, pno, cfg))

    # 统一编号：与绘字顺序无关
    doc.blocks.sort(key=lambda b: (b.page, b.top, b.x0))
    for i, b in enumerate(doc.blocks):
        b.bid = i
    doc.frames.sort(key=lambda f: (f.page, f.top))
    for i, f in enumerate(doc.frames):
        f.fid = i
    for i, ln in enumerate(doc.hlines):
        ln.idx = i

    # 表框重叠检查
    for a in doc.frames:
        for b in doc.frames:
            if a.fid >= b.fid or a.page != b.page:
                continue
            ox = min(a.x1, b.x1) - max(a.x0, b.x0)
            oy = min(a.bottom, b.bottom) - max(a.top, b.top)
            if ox > 0 and oy > 0:
                doc.problems.append(
                    {
                        "code": "frame_overlap",
                        "message": f"第 {a.page} 页表框 {a.fid} 与 {b.fid} 重叠",
                        "page": a.page,
                    }
                )
    return doc
