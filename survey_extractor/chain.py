"""成表层：把各页表框按页序链接成表，计算全局纵坐标，识别接缝/片段/地区区间。

公开约定：
- 续页接入前表的条件（三者同时满足）：表名相同、列名次序相同、
  地区延续标记匹配（前表地区区间开口时，本页地区列第一个事件必须是
  收口的横线而不是新的地区文字）。
- 承接标记（如 Q17（续））出现在续页表体上缘：以上一页最后一个编号
  作为虚拟锚点，该接缝不强制断行；标记本身不是字段值。
- 无承接标记的接缝是强制行界；续页顶部的无编号片段与上一页最后一条
  记录一起列为待核实，不凭空接到最近记录。
- 表内全局纵坐标 gy：各页表体按页序拼接，gy = y_off + (top - body_top)。
"""
from __future__ import annotations

from .config import Config
from .model import WorkDoc, Frame, Table, Seam, RegionInterval, FATAL_CODES
from .util import assemble_blocks as _assemble


def chain_frames(doc: WorkDoc, cfg: Config) -> None:
    sink = getattr(doc, "_frame_problem_sink", {})
    for f in doc.frames:
        if f.name is None:
            sink.setdefault(f.fid, []).append(
                {
                    "code": "missing_table_name",
                    "message": f"第 {f.page} 页表框上方 {cfg.name_max_dist}pt 内未找到表名",
                    "page": f.page,
                }
            )

    for f in doc.frames:
        prev_tbl = doc.tables[-1] if doc.tables else None
        if prev_tbl is not None and _can_join(doc, prev_tbl, f, cfg):
            _join(doc, prev_tbl, f, cfg)
        else:
            tbl = Table(tid=len(doc.tables), name=f.name or "（无表名）", columns=list(f.columns))
            tbl.frames.append(f.fid)
            f.table = tbl.tid
            f.y_off = 0.0
            doc.tables.append(tbl)
            _assign_gy(doc, f)
            # 新表首帧上的无编号片段（仅当文档中已存在前表时才有意义）
            _handle_fragments(doc, tbl, f, cfg, prev_table=prev_tbl, joined=False)
        tbl = doc.tables[f.table]
        tbl.problems.extend(sink.get(f.fid, []))

    # 每表：锚点/杂字归类、地区区间解析
    for tbl in doc.tables:
        _classify_id_blocks(doc, tbl, cfg)
        _parse_region_intervals(doc, tbl, cfg)


def _assign_gy(doc: WorkDoc, f: Frame) -> None:
    """帧链接后立即计算该帧字块/横线的全局纵坐标。"""
    for b in doc.blocks:
        if b.frame == f.fid and b.role == "body":
            b.gy0 = f.y_off + (b.top - f.body_top)
            b.gy1 = f.y_off + (b.bottom - f.body_top)
    for ln in doc.hlines:
        if ln.frame == f.fid and ln.role in ("row_candidate", "region"):
            ln.gy = f.y_off + (ln.y - f.body_top)


# ---------------------------------------------------------------- 接入判定
def _can_join(doc: WorkDoc, tbl: Table, f: Frame, cfg: Config) -> bool:
    if f.name is None or tbl.name != f.name:
        return False
    if list(f.columns) != list(tbl.columns):
        return False
    # 地区延续：前表区间开口时，本页地区列第一个事件必须是收口横线
    if _region_pending(doc, tbl, cfg):
        events = _frame_region_events(doc, f, cfg)
        if events and events[0][0] == "block":
            tbl.problems.append(
                {
                    "code": "region_continuation_mismatch",
                    "message": "前表地区区间未收口，但续页地区列先出现新的地区文字，拒绝接入",
                    "page": f.page,
                }
            )
            return False
    # 承接标记：标记编号必须等于前表最后一个编号
    if f.marker_bid >= 0:
        marker = doc.blocks[f.marker_bid]
        anchors = _table_anchors(doc, tbl, cfg)
        last_id = anchors[-1].text if anchors else None
        if marker.text_id != last_id:
            tbl.problems.append(
                {
                    "code": "marker_mismatch",
                    "message": (
                        f"续页承接标记「{marker.text}」与前表最后编号「{last_id}」不一致，拒绝接入"
                    ),
                    "page": f.page,
                }
            )
            return False
    return True


def _join(doc: WorkDoc, tbl: Table, f: Frame, cfg: Config) -> None:
    prev_f = doc.frames[tbl.frames[-1]]
    f.table = tbl.tid
    f.y_off = prev_f.y_off + prev_f.body_height
    tbl.frames.append(f.fid)
    _assign_gy(doc, f)
    kind = "continuation" if f.marker_bid >= 0 else "hard"
    tbl.seams.append(Seam(prev_frame=prev_f.fid, next_frame=f.fid, gy=f.y_off, kind=kind,
                          marker_bid=f.marker_bid))
    if kind == "hard":
        _handle_fragments(doc, tbl, f, cfg, prev_table=None, joined=True)


def _frame_region_events(doc: WorkDoc, f: Frame, cfg: Config):
    if cfg.region_column_name not in f.columns:
        return []
    ri = f.columns.index(cfg.region_column_name)
    ev = []
    for ln in doc.hlines:
        if ln.frame == f.fid and ln.role == "region":
            ev.append(("line", ln.y))
    for b in doc.blocks:
        if b.frame == f.fid and b.role == "body" and b.col == ri:
            ev.append(("block", b.top))
    ev.sort(key=lambda e: e[1])
    return ev


def _region_pending(doc: WorkDoc, tbl: Table, cfg: Config) -> bool:
    """前表地区列：最后一个事件之后仍有地区文字（区间开口）。"""
    lines, blocks = _table_region_items(doc, tbl, cfg)
    if not blocks:
        return False
    if not lines:
        return True
    last_line_gy = max(ln.gy for ln in lines)
    return any(b.gy_center > last_line_gy for b in blocks)


def _table_region_items(doc: WorkDoc, tbl: Table, cfg: Config):
    if cfg.region_column_name not in tbl.columns:
        return [], []
    ri = tbl.columns.index(cfg.region_column_name)
    fids = set(tbl.frames)
    lines = [ln for ln in doc.hlines if ln.frame in fids and ln.role == "region"]
    blocks = [
        b
        for b in doc.blocks
        if b.frame in fids and b.role == "body" and b.col == ri and b.kind != "fragment"
    ]
    return lines, blocks


def _table_anchors(doc: WorkDoc, tbl: Table, cfg: Config):
    id_re = cfg.id_re()
    fids = set(tbl.frames)
    out = [
        b
        for b in doc.blocks
        if b.frame in fids and b.role == "body" and b.col == cfg.id_column and id_re.match(b.text)
    ]
    out.sort(key=lambda b: b.gy0)
    return out


# ---------------------------------------------------------------- 无编号片段
def _handle_fragments(doc: WorkDoc, tbl: Table, f: Frame, cfg: Config,
                      prev_table, joined: bool) -> None:
    """无承接标记时，本帧表体上缘、第一个编号之前的普通字块 = 无编号片段。"""
    if not joined and prev_table is None:
        return  # 文档第一个表的首帧：行首内容属于第一行
    id_re = cfg.id_re()
    body = [b for b in doc.blocks if b.frame == f.fid and b.role == "body"]
    anchors = [b for b in body if b.col == cfg.id_column and id_re.match(b.text)]
    region_col = tbl.columns.index(cfg.region_column_name) if cfg.region_column_name in tbl.columns else -1
    normal = [b for b in body if b.col != region_col and b.kind != "marker"]
    if anchors:
        first_top = min(b.top for b in anchors)
        frags = [b for b in normal if b.bottom <= first_top + cfg.boundary_touch_tol
                 and not (b.col == cfg.id_column)]
    else:
        frags = [b for b in normal if b.col != cfg.id_column]
    if not frags:
        return
    for b in frags:
        b.kind = "fragment"
    reason = (
        "续页缺少承接标记，顶部无编号片段不予归属"
        if joined
        else "该表未能作为续页接入前表（表名/列序/地区延续不匹配），表首无编号片段不予归属"
    )
    tbl.to_verify.append(
        {
            "kind": "unattached_fragment",
            "reason": reason,
            "blocks": [b.src(cfg.coord_digits) for b in sorted(frags, key=lambda b: (b.page, b.top))],
        }
    )
    # 前表最后一条记录可能未完
    if prev_table is not None:
        anchors_prev = _table_anchors(doc, prev_table, cfg)
        if anchors_prev:
            prev_table.row_flags[anchors_prev[-1].text] = (
                "可能未完：后续表首存在无编号片段，但承接证据不完整"
            )
            prev_table.to_verify.append(
                {
                    "kind": "possibly_unfinished_row",
                    "record_id": anchors_prev[-1].text,
                    "reason": "后续表首存在无编号片段，但表名/列序/地区延续不匹配，无法承接",
                    "blocks": [],
                }
            )
    elif joined:
        # 上一帧最后一个编号（不含本帧）
        prev_fids = set(tbl.frames[:-1])
        anchors_prev = [
            b
            for b in doc.blocks
            if b.frame in prev_fids and b.role == "body" and b.col == cfg.id_column
            and cfg.id_re().match(b.text)
        ]
        anchors_prev.sort(key=lambda b: b.gy0)
        if anchors_prev:
            tbl.row_flags[anchors_prev[-1].text] = (
                "可能未完：下一页顶部存在无编号片段但无承接标记"
            )
            tbl.to_verify.append(
                {
                    "kind": "possibly_unfinished_row",
                    "record_id": anchors_prev[-1].text,
                    "reason": "下一页顶部存在无编号片段但无承接标记",
                    "blocks": [],
                }
            )


# ---------------------------------------------------------------- 编号列归类
def _classify_id_blocks(doc: WorkDoc, tbl: Table, cfg: Config) -> None:
    id_re = cfg.id_re()
    fids = set(tbl.frames)
    seen = {}
    for b in doc.blocks:
        if b.frame not in fids or b.role != "body" or b.col != cfg.id_column:
            continue
        if b.kind == "marker":
            # 只有作为续页承接接入的标记才合法
            ok = any(s.marker_bid == b.bid and s.kind == "continuation" for s in tbl.seams)
            if not ok:
                b.kind = "stray"
                tbl.problems.append(
                    {
                        "code": "unexpected_id_text",
                        "message": f"编号列出现无法承接的标记文字「{b.text}」(bid={b.bid})",
                        "page": b.page,
                    }
                )
            continue
        if id_re.match(b.text):
            b.kind = "anchor"
            if b.text in seen:
                tbl.problems.append(
                    {
                        "code": "duplicate_id",
                        "message": f"编号「{b.text}」在表内重复（bid={seen[b.text]} 与 {b.bid}）",
                        "page": b.page,
                    }
                )
            seen[b.text] = b.bid
        else:
            b.kind = "stray"
            tbl.problems.append(
                {
                    "code": "unexpected_id_text",
                    "message": f"编号列出现非编号文字「{b.text}」(bid={b.bid})",
                    "page": b.page,
                }
            )
    tbl.anchors = sorted(
        [b for b in doc.blocks if b.frame in fids and b.kind == "anchor"],
        key=lambda b: b.gy_center,
    )
    if not tbl.anchors:
        tbl.problems.append(
            {"code": "no_anchors", "message": "表内没有任何编号锚点", "page": doc.frames[tbl.frames[0]].page}
        )


# ---------------------------------------------------------------- 地区区间
def _parse_region_intervals(doc: WorkDoc, tbl: Table, cfg: Config) -> None:
    lines, blocks = _table_region_items(doc, tbl, cfg)
    if not lines and not blocks:
        return
    lines = sorted(lines, key=lambda ln: ln.gy)
    blocks = sorted(blocks, key=lambda b: b.gy_center)
    line_idx = {id(ln): i for i, ln in enumerate(lines)}

    for ln in lines:
        for b in blocks:
            if b.gy0 + cfg.boundary_touch_tol < ln.gy < b.gy1 - cfg.boundary_touch_tol:
                tbl.problems.append(
                    {
                        "code": "region_line_crosses_value",
                        "message": f"地区列横线 gy={ln.gy:.1f} 穿过地区文字「{b.text}」(bid={b.bid})",
                        "page": b.page,
                    }
                )

    intervals = []
    for i in range(len(lines) - 1):
        lo, hi = lines[i].gy, lines[i + 1].gy
        inside = [b for b in blocks if lo + cfg.boundary_touch_tol < b.gy_center < hi - cfg.boundary_touch_tol]
        if inside:
            intervals.append(
                RegionInterval(
                    gy_top=lo,
                    gy_bottom=hi,
                    value=_assemble(inside, cfg),
                    block_bids=[b.bid for b in inside],
                    top_line_idx=line_idx[id(lines[i])],
                    bottom_line_idx=line_idx[id(lines[i + 1])],
                )
            )
    covered = set()
    for iv in intervals:
        covered.update(iv.block_bids)
    for b in blocks:
        if b.bid in covered:
            continue
        below_last = lines and b.gy_center > lines[-1].gy + cfg.boundary_touch_tol
        above_first = lines and b.gy_center < lines[0].gy - cfg.boundary_touch_tol
        if below_last and not above_first:
            continue  # 开口区间：等续页收口；表尾仍开口则由下方检查报告
        tbl.problems.append(
            {
                "code": "region_value_outside_interval",
                "message": f"地区文字「{b.text}」(bid={b.bid}) 不在任何地区区间内",
                "page": b.page,
            }
        )
    if blocks and lines:
        last_line_gy = lines[-1].gy
        if any(b.gy_center > last_line_gy + cfg.boundary_touch_tol for b in blocks):
            tbl.problems.append(
                {
                    "code": "unclosed_region_interval",
                    "message": "地区区间在表尾未收口（最后一条地区横线之下仍有地区文字）",
                    "page": blocks[-1].page,
                }
            )
    tbl.region_intervals = intervals
