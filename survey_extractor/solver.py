"""行界求解器：在布局证据上枚举合法解释，判定唯一/歧义/结构损坏。

公开约定（与文档一致）：
- 编号列内的短水平线是候选行界；同一对相邻编号锚点之间须选且只能选一条；
  无承接标记的跨页接缝是强制行界；有承接标记时接缝不断行，
  且选中行界必须位于承接标记之下（标记属于虚拟锚点所在行）。
- 行界不得穿过任何普通属性列字块/编号/承接标记（容差内接触允许）；
  地区列字块不受此限（行界概念上不延伸到地区列）。
- 地区区间端点必须对齐选中行界或表体端点（容差 line_up_tol）。
- 两种解释只有在"同一批字块归属不同业务行"时才算不同；
  仅在无文字空白处选择不同坐标不算（按归属签名去重）。
- 0 个合法解释 → 结构损坏；>1 个 → 按变动字块把相邻行连成隔离组，
  其余行照常输出；每个隔离组给出两份完整可复算的见证。
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from .config import Config
from .model import WorkDoc, Table, FATAL_CODES
from .util import assemble_blocks, gy_to_page


@dataclass
class Boundary:
    gy: float
    source: str  # line / seam
    line_idx: int = -1


@dataclass
class Solution:
    boundaries: list  # 每个槽位选中的 Boundary（长度 = 锚点数 - 1）
    assign: dict  # bid -> 行号
    reg_map: tuple  # 行号 -> 地区区间序号 | None


def solve_table(doc: WorkDoc, tbl: Table, cfg: Config) -> None:
    if any(p["code"] in FATAL_CODES for p in tbl.problems):
        tbl.status = "structural_corruption"
        return
    anchors = tbl.anchors
    n = len(anchors)
    fids = set(tbl.frames)
    region_col = (
        tbl.columns.index(cfg.region_column_name) if cfg.region_column_name in tbl.columns else -1
    )

    constrained = [
        b
        for b in doc.blocks
        if b.frame in fids
        and b.role == "body"
        and b.kind in ("normal", "anchor", "marker")
        and b.col != region_col
    ]
    assignable = [b for b in constrained if b.kind == "normal"]
    cand_lines = [ln for ln in doc.hlines if ln.frame in fids and ln.role == "row_candidate"]
    cand_lines.sort(key=lambda ln: ln.gy)

    total_height = sum(doc.frames[fid].body_height for fid in tbl.frames)

    # ---- 槽位与候选 ----
    slots = []
    seam_conflict = False
    for i in range(n - 1):
        a, b = anchors[i], anchors[i + 1]
        lo, hi = a.gy_center, b.gy_center
        slot_seams = [s for s in tbl.seams if lo < s.gy < hi]
        hard = [s for s in slot_seams if s.kind == "hard"]
        slot = {"between": [a.text, b.text], "seam": None, "candidates": []}
        if len(hard) > 1:
            tbl.problems.append(
                {
                    "code": "no_candidate_boundary",
                    "message": f"编号 {a.text} 与 {b.text} 之间存在多个强制接缝行界，无法唯一确定",
                    "page": a.page,
                }
            )
            seam_conflict = True
            slot["choices"] = []
            slots.append(slot)
            continue
        if hard:
            seam = hard[0]
            slot["seam"] = "hard"
            slot["choices"] = [Boundary(gy=seam.gy, source="seam")]
            for ln in cand_lines:
                if lo < ln.gy < hi:
                    slot["candidates"].append(
                        {"gy": ln.gy, "legal": False, "reason": "被强制接缝行界遮蔽"}
                    )
        else:
            # 槽位内全部承接标记都必须位于选中行界之上（标记属于虚拟锚点所在行）
            markers = [doc.blocks[s.marker_bid] for s in slot_seams if s.marker_bid >= 0]
            min_gy = max((m.gy1 for m in markers), default=None)
            choices = []
            for ln in cand_lines:
                if not (lo < ln.gy < hi):
                    continue
                cross = _crosses(ln.gy, constrained, cfg)
                if cross is not None:
                    slot["candidates"].append(
                        {
                            "gy": ln.gy,
                            "legal": False,
                            "reason": f"穿过字块 bid={cross.bid}「{cross.text}」",
                        }
                    )
                    continue
                if min_gy is not None and ln.gy < min_gy - cfg.boundary_touch_tol:
                    slot["candidates"].append(
                        {
                            "gy": ln.gy,
                            "legal": False,
                            "reason": "位于承接标记之上（标记须属于虚拟锚点所在行）",
                        }
                    )
                    continue
                slot["candidates"].append({"gy": ln.gy, "legal": True, "reason": ""})
                choices.append(Boundary(gy=ln.gy, source="line", line_idx=ln.idx))
            if slot_seams:
                slot["seam"] = "continuation"
            slot["choices"] = choices
        slots.append(slot)
        if not slot["choices"] and not seam_conflict:
            tbl.problems.append(
                {
                    "code": "no_candidate_boundary",
                    "message": f"编号 {a.text} 与 {b.text} 之间没有合法的候选行界",
                    "page": a.page,
                }
            )
    tbl.slots = slots
    if any(p["code"] == "no_candidate_boundary" for p in tbl.problems):
        tbl.status = "structural_corruption"
        return

    # ---- 枚举合法解释（按归属签名去重） ----
    solutions = {}
    overflow = [False]

    def dfs(i, chosen):
        if overflow[0]:
            return
        if i == len(slots):
            bounds = [0.0] + [c.gy for c in chosen] + [total_height]
            if not _region_ok(bounds, tbl, cfg):
                return
            sol = _make_solution(chosen, bounds, assignable, tbl, cfg, n)
            solutions.setdefault(sol[0], sol[1])
            if len(solutions) > cfg.max_solutions:
                overflow[0] = True
            return
        for c in slots[i]["choices"]:
            dfs(i + 1, chosen + [c])

    dfs(0, [])
    if overflow[0]:
        tbl.problems.append(
            {
                "code": "solution_overflow",
                "message": f"合法解释超过上限 {cfg.max_solutions}，无法可靠枚举",
                "page": None,
            }
        )
        tbl.status = "structural_corruption"
        return
    if not solutions:
        tbl.problems.append(
            {
                "code": "no_legal_interpretation",
                "message": "不存在满足全部布局约定（行界/地区区间/承接）的解释",
                "page": None,
            }
        )
        tbl.status = "structural_corruption"
        return

    sols = list(solutions.values())
    tbl.solutions = sols
    if len(sols) > 1:
        tbl.status = "ambiguous"
        _build_groups(doc, tbl, sols, assignable, cfg)
    _build_records(doc, tbl, sols, anchors, region_col, cfg)


# ---------------------------------------------------------------- 内部
def _crosses(gy, blocks, cfg):
    for b in blocks:
        if b.gy0 + cfg.boundary_touch_tol < gy < b.gy1 - cfg.boundary_touch_tol:
            return b
    return None


def _region_ok(bounds, tbl, cfg):
    for iv in tbl.region_intervals:
        if not any(abs(x - iv.gy_top) <= cfg.line_up_tol for x in bounds):
            return False
        if not any(abs(x - iv.gy_bottom) <= cfg.line_up_tol for x in bounds):
            return False
    return True


def _make_solution(chosen, bounds, assignable, tbl, cfg, n_rows):
    assign = {}
    for b in assignable:
        row = bisect_right(bounds, b.gy_center) - 1
        row = max(0, min(n_rows - 1, row))
        assign[b.bid] = row
    reg_map = []
    for i in range(n_rows):
        found = None
        for k, iv in enumerate(tbl.region_intervals):
            if bounds[i] >= iv.gy_top - cfg.line_up_tol and bounds[i + 1] <= iv.gy_bottom + cfg.line_up_tol:
                found = k
                break
        reg_map.append(found)
    sig = tuple(assign[b.bid] for b in sorted(assignable, key=lambda x: x.bid)) + tuple(
        -1 if r is None else r for r in reg_map
    )
    return sig, Solution(boundaries=list(chosen), assign=assign, reg_map=tuple(reg_map))


def _build_groups(doc, tbl, sols, assignable, cfg):
    """按变动字块把相邻行连成隔离组（并查集）。"""
    n = len(tbl.anchors)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    varying_blocks = {}
    for b in assignable:
        rows = {s.assign[b.bid] for s in sols}
        if len(rows) > 1:
            varying_blocks[b.bid] = sorted(rows)
            rs = sorted(rows)
            for r in rs[1:]:
                union(rs[0], r)
    varying_region_rows = set()
    for i in range(n):
        if len({s.reg_map[i] for s in sols}) > 1:
            varying_region_rows.add(i)
            union(i, i)

    comp = {}
    for b_bid, rows in varying_blocks.items():
        for r in rows:
            comp.setdefault(find(r), set()).add(r)
    for r in varying_region_rows:
        comp.setdefault(find(r), set()).add(r)

    groups = []
    for rows in sorted(comp.values(), key=lambda s: min(s)):
        rows = sorted(rows)
        bids = [bid for bid, rs in varying_blocks.items() if any(r in rows for r in rs)]
        groups.append({"rows": rows, "varying_blocks": bids})
    # 见证：每个隔离组给两份完整可复算的解释
    for g in groups:
        rows = set(g["rows"])
        bids = set(g["varying_blocks"])

        def key(sol):
            return (
                tuple(sol.assign[b] for b in sorted(bids)),
                tuple(sol.reg_map[r] for r in sorted(rows)),
            )

        a = tbl.solutions[0]
        b = next((s for s in tbl.solutions[1:] if key(s) != key(a)), None)
        g["witnesses"] = [
            _witness(doc, tbl, sol, rows, cfg) for sol in (a, b) if sol is not None
        ]
    tbl.groups = groups


def _witness(doc, tbl, sol, group_rows, cfg):
    bounds = [0.0] + [bd.gy for bd in sol.boundaries] + [
        sum(doc.frames[fid].body_height for fid in tbl.frames)
    ]
    out_bounds = []
    for i, gy in enumerate(bounds):
        page, y = gy_to_page(tbl, doc, gy)
        if i == 0:
            src = "body_top"
        elif i == len(bounds) - 1:
            src = "body_bottom"
        else:
            src = sol.boundaries[i - 1].source
        out_bounds.append({"gy": round(gy, 2), "page": page, "y": round(y, 2), "source": src})
    fields = {}
    for r in sorted(group_rows):
        fields[str(r)] = _row_fields(doc, tbl, sol, r, cfg, text_only=True)
    return {
        "boundaries": out_bounds,
        "assignments": {str(bid): row for bid, row in sorted(sol.assign.items())},
        "region_map": [(-1 if x is None else x) for x in sol.reg_map],
        "group_row_fields": fields,
    }


def _row_blocks(doc, tbl, sol, row, col):
    return [
        doc.blocks[bid]
        for bid, r in sol.assign.items()
        if r == row and doc.blocks[bid].col == col
    ]


def _row_fields(doc, tbl, sol, row, cfg, text_only=False):
    out = {}
    for ci, name in enumerate(tbl.columns):
        if ci == cfg.id_column:
            out[name] = tbl.anchors[row].text
        elif name == cfg.region_column_name:
            k = sol.reg_map[row]
            out[name] = tbl.region_intervals[k].value if k is not None else ""
        else:
            blocks = sorted(_row_blocks(doc, tbl, sol, row, ci), key=lambda b: (b.gy0, b.x0))
            out[name] = assemble_blocks(blocks, cfg)
    return out


# ---------------------------------------------------------------- 记录输出
def _build_records(doc, tbl, sols, anchors, region_col, cfg):
    sol0 = sols[0]
    n = len(anchors)
    group_of = {}
    for gi, g in enumerate(tbl.groups):
        for r in g["rows"]:
            group_of[r] = gi
    bounds0 = [0.0] + [bd.gy for bd in sol0.boundaries] + [
        sum(doc.frames[fid].body_height for fid in tbl.frames)
    ]
    seam_gys = [s.gy for s in tbl.seams]

    for row in range(n):
        anchor = anchors[row]
        isolated = row in group_of
        rec = {
            "row_index": row,
            "id": anchor.text,
            "status": "ok",
            "pages": set(),
            "spans_page_break": any(
                bounds0[row] + cfg.boundary_touch_tol < sg < bounds0[row + 1] - cfg.boundary_touch_tol
                for sg in seam_gys
            ),
            "fields": {},
        }
        if isolated:
            rec["status"] = "isolated"
            rec["isolation_group"] = group_of[row]
        if anchor.text in tbl.row_flags:
            rec["status"] = "to_verify" if not isolated else rec["status"]
            rec["to_verify_reason"] = tbl.row_flags[anchor.text]

        for ci, name in enumerate(tbl.columns):
            if ci == cfg.id_column:
                payload = {
                    "value": anchor.text,
                    "empty": False,
                    "sources": [anchor.src(cfg.coord_digits)],
                }
                rec["pages"].add(anchor.page)
            elif name == cfg.region_column_name:
                payload = _region_field(doc, tbl, sols, row, isolated, cfg)
                for s in payload.get("sources", []):
                    rec["pages"].add(s["page"])
            else:
                payload = _normal_field(doc, tbl, sols, row, ci, isolated, cfg)
                for s in payload.get("sources", []):
                    rec["pages"].add(s["page"])
            if isolated and payload is not None and not payload.get("constant", True):
                payload = None  # 跨解释不唯一 → 不交付该字段
            if payload is not None:
                payload.pop("constant", None)
            rec["fields"][name] = payload
        rec["pages"] = sorted(rec["pages"])
        tbl.records.append(rec)


def _normal_field(doc, tbl, sols, row, col, need_constancy, cfg):
    per_sol = []
    for sol in sols:
        blocks = sorted(_row_blocks(doc, tbl, sol, row, col), key=lambda b: (b.gy0, b.x0))
        per_sol.append(blocks)
    first = per_sol[0]
    constant = all(
        [b.bid for b in bs] == [b.bid for b in first] for bs in per_sol[1:]
    )
    if need_constancy and not constant:
        return {"constant": False}
    if not first:
        return {"value": "", "empty": True, "sources": [], "constant": constant}
    return {
        "value": assemble_blocks(first, cfg),
        "empty": False,
        "sources": [b.src(cfg.coord_digits) for b in first],
        "constant": constant,
    }


def _region_field(doc, tbl, sols, row, need_constancy, cfg):
    keys = [sol.reg_map[row] for sol in sols]
    constant = len(set(keys)) == 1
    if need_constancy and not constant:
        return {"constant": False}
    k = keys[0]
    if k is None:
        return {"value": "", "empty": True, "sources": [], "constant": constant}
    iv = tbl.region_intervals[k]
    blocks = [doc.blocks[bid] for bid in iv.block_bids]
    return {
        "value": iv.value,
        "empty": False,
        "sources": [b.src(cfg.coord_digits) for b in blocks],
        "constant": constant,
    }
