"""布局层：表名、表头、列区间、字块/横线归类。

公开约定：
- 表名 = 表框正上方距离不超过 name_max_dist 的最近一行文字；
  更高的文字是页眉，表框外的文字（页脚、下一表名）不是业务值。
- 表头带 = 表框上缘到第一条通栏横线（表头分隔线）之间；
  表头字块横向聚类（间隙 ≤ header_cluster_gap）得到有序列名。
- 列水平边界 = 相邻完整列标题中心的中点；表左右界 = 表框。
- 字块按中心 x 归入某列；越过列边界超过 col_cross_tol → 结构问题。
- 表体横线分类：落在编号列内 → 候选行界；落在地区列内 → 地区区间线；
  通栏线（非表头分隔线）与无法归类的线只记录、不参与行界。
"""
from __future__ import annotations

from .config import Config
from .model import WorkDoc, Frame, Block


def _norm(s: str) -> str:
    return "".join((s or "").split())


def layout_document(doc: WorkDoc, cfg: Config) -> None:
    _assign_frame_names(doc, cfg)
    _assign_blocks_to_frames(doc, cfg)
    for f in doc.frames:
        _parse_frame_header(doc, f, cfg)
    _classify_body_blocks(doc, cfg)
    _classify_hlines(doc, cfg)
    _detect_markers(doc, cfg)
    _collect_ignored(doc, cfg)


# ---------------------------------------------------------------- 表名 / 忽略文字
def _assign_frame_names(doc: WorkDoc, cfg: Config) -> None:
    by_page = {}
    for b in doc.blocks:
        by_page.setdefault(b.page, []).append(b)
    for f in doc.frames:
        cands = []
        for b in by_page.get(f.page, []):
            if b.frame != -1:
                continue
            if b.bottom <= f.top + cfg.line_y_tol and 0 <= f.top - b.bottom <= cfg.name_max_dist:
                if b.x1 > f.x0 and b.x0 < f.x1:  # 与表框横向相交
                    cands.append(b)
        if not cands:
            f.name = None
            continue
        # 最近的一行（按行聚类，取 bottom 最大者）
        cands.sort(key=lambda b: b.bottom)
        line = [cands[-1]]
        for b in reversed(cands[:-1]):
            if abs(b.cy - line[-1].cy) <= cfg.line_y_tol:
                line.append(b)
            else:
                break
        line.sort(key=lambda b: b.x0)
        f.name = _norm("".join(b.text for b in line))
        for b in line:
            b.role = "name"
            b.frame = f.fid


def _assign_blocks_to_frames(doc: WorkDoc, cfg: Config) -> None:
    for b in doc.blocks:
        if b.role == "name":
            continue
        for f in doc.frames:
            if b.page != f.page:
                continue
            if f.x0 <= b.cx <= f.x1 and f.top <= b.cy <= f.bottom:
                b.frame = f.fid
                break


def _collect_ignored(doc: WorkDoc, cfg: Config) -> None:
    for b in doc.blocks:
        if b.frame == -1 and b.role != "name":
            b.role = "ignored"
            doc.ignored.append(
                {
                    "bid": b.bid,
                    "page": b.page,
                    "text": b.text,
                    "bbox": [round(b.x0, 2), round(b.top, 2), round(b.x1, 2), round(b.bottom, 2)],
                    "reason": "表框之外（页眉/页脚/下一表名等），不是业务值",
                }
            )


# ---------------------------------------------------------------- 表头与列
def _parse_frame_header(doc: WorkDoc, f: Frame, cfg: Config) -> None:
    tbl_problems = _frame_problems(doc, f)
    fw = []
    for ln in doc.hlines:
        if ln.page != f.page:
            continue
        covered = (min(ln.x1, f.x1) - max(ln.x0, f.x0)) / (f.x1 - f.x0)
        if covered >= cfg.full_width_ratio:
            fw.append(ln)
    for ln in fw:
        if abs(ln.y - f.top) <= 1.0 or abs(ln.y - f.bottom) <= 1.0:
            ln.role = "frame_edge"
            ln.frame = f.fid
    inner = sorted(
        (ln for ln in fw if ln.role != "frame_edge" and f.top < ln.y < f.bottom),
        key=lambda ln: ln.y,
    )
    if not inner:
        tbl_problems.append(
            {
                "code": "missing_header_separator",
                "message": f"第 {f.page} 页表框（{f.name or '未命名'}）缺少表头分隔通栏线",
                "page": f.page,
            }
        )
        return
    sep = inner[0]
    sep.role = "header_sep"
    sep.frame = f.fid
    f.header_sep = sep.y
    for ln in inner[1:]:
        ln.role = "fullwidth_body"
        ln.frame = f.fid

    # 表头带内字块 → 横向聚类 → 列
    heads = [
        b
        for b in doc.blocks
        if b.frame == f.fid and f.top <= b.cy <= f.header_sep
    ]
    heads.sort(key=lambda b: b.x0)
    clusters = []
    for b in heads:
        b.role = "header"
        if clusters and b.x0 - clusters[-1][-1].x1 <= cfg.header_cluster_gap:
            clusters[-1].append(b)
        else:
            clusters.append([b])
    names, centers = [], []
    for cl in clusters:
        cl.sort(key=lambda b: (b.top, b.x0))
        names.append(_norm("".join(b.text for b in cl)))
        x0 = min(b.x0 for b in cl)
        x1 = max(b.x1 for b in cl)
        centers.append((x0 + x1) / 2.0)
    if len(names) != len(set(names)):
        tbl_problems.append(
            {
                "code": "duplicate_column_name",
                "message": f"第 {f.page} 页表框（{f.name or '未命名'}）列名重复：{names}",
                "page": f.page,
            }
        )
    f.columns = names
    f.centers = centers
    bounds = []
    for i, c in enumerate(centers):
        lo = f.x0 if i == 0 else (centers[i - 1] + c) / 2.0
        hi = f.x1 if i == len(centers) - 1 else (c + centers[i + 1]) / 2.0
        bounds.append((lo, hi))
    f.bounds = bounds


def _classify_body_blocks(doc: WorkDoc, cfg: Config) -> None:
    for b in doc.blocks:
        if b.frame == -1 or b.role in ("header", "name", "ignored"):
            continue
        f = doc.frames[b.frame]
        if f.header_sep is None or not f.bounds:
            continue
        if not (f.header_sep < b.cy < f.bottom):
            continue
        b.role = "body"
        col = 0
        for i, (lo, hi) in enumerate(f.bounds):
            if lo <= b.cx < hi:
                col = i
                break
            if b.cx >= hi:
                col = i
        b.col = col
        lo, hi = f.bounds[col]
        if b.x0 < lo - cfg.col_cross_tol or b.x1 > hi + cfg.col_cross_tol:
            _frame_problems(doc, f).append(
                {
                    "code": "block_crosses_column",
                    "message": (
                        f"字块 bid={b.bid}「{b.text}」跨越第 {col} 列边界超过容差："
                        f"[{b.x0:.1f},{b.x1:.1f}] vs 列区间 [{lo:.1f},{hi:.1f}]"
                    ),
                    "page": b.page,
                }
            )


def _classify_hlines(doc: WorkDoc, cfg: Config) -> None:
    for ln in doc.hlines:
        if ln.role in ("frame_edge", "header_sep", "fullwidth_body"):
            continue
        f = next((f for f in doc.frames if f.page == ln.page and f.x0 - 1 <= (ln.x0 + ln.x1) / 2 <= f.x1 + 1
                  and f.header_sep is not None
                  and f.header_sep - cfg.line_x_tol <= ln.y <= f.bottom + cfg.line_x_tol), None)
        if f is None or not f.bounds:
            if ln.role == "":
                ln.role = "orphan"
            continue
        ln.frame = f.fid
        id_lo, id_hi = f.bounds[cfg.id_column]
        if ln.x0 >= id_lo - cfg.line_x_tol and ln.x1 <= id_hi + cfg.line_x_tol:
            ln.role = "row_candidate"
            continue
        if cfg.region_column_name in f.columns:
            ri = f.columns.index(cfg.region_column_name)
            r_lo, r_hi = f.bounds[ri]
            if ln.x0 >= r_lo - cfg.line_x_tol and ln.x1 <= r_hi + cfg.line_x_tol:
                ln.role = "region"
                continue
        ln.role = "orphan"


def _detect_markers(doc: WorkDoc, cfg: Config) -> None:
    """承接标记：表体上缘 marker_band 范围内、编号列最上方且匹配 编号+续 的字块。"""
    mre = cfg.marker_re()
    for f in doc.frames:
        if f.header_sep is None:
            continue
        cands = [
            b
            for b in doc.blocks
            if b.frame == f.fid
            and b.role == "body"
            and b.col == cfg.id_column
            and b.top <= f.body_top + cfg.marker_band
        ]
        if not cands:
            continue
        top_most = min(cands, key=lambda b: b.top)
        m = mre.match(top_most.text)
        if m:
            f.marker_bid = top_most.bid
            top_most.kind = "marker"
            top_most.text_id = m.group(1)


def _frame_problems(doc: WorkDoc, f: Frame) -> list:
    """把问题挂到该框所属的表；表尚未建立时挂到文档级暂存。"""
    if not hasattr(doc, "_frame_problem_sink"):
        doc._frame_problem_sink = {}
    return doc._frame_problem_sink.setdefault(f.fid, [])
