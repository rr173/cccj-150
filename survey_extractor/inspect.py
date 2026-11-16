"""inspect：在定行业务行之前，输出全部布局证据供核查。"""
from __future__ import annotations

from .config import Config, DEFAULT_CONFIG
from .extract import extract_pdf, Extraction
from .util import gy_to_page


def inspect_pdf(path: str, cfg: Config | None = None) -> dict:
    ext = extract_pdf(path, cfg)
    return inspect_dict(ext)


def inspect_dict(ext: Extraction) -> dict:
    doc, cfg = ext.doc, ext.cfg
    out_tables = []
    for tbl in doc.tables:
        frames = [doc.frames[fid] for fid in tbl.frames]
        fids = set(tbl.frames)
        anchors = [
            {
                "id": b.text,
                "bid": b.bid,
                "page": b.page,
                "bbox": [round(b.x0, 2), round(b.top, 2), round(b.x1, 2), round(b.bottom, 2)],
                "gy": round(b.gy_center, 2),
            }
            for b in tbl.anchors
        ]
        cand = []
        for ln in doc.hlines:
            if ln.frame in fids and ln.role == "row_candidate":
                page, y = ln.page, ln.y
                cand.append({"page": page, "y": round(y, 2), "gy": round(ln.gy, 2),
                             "x": [round(ln.x0, 2), round(ln.x1, 2)]})
        region_lines = [
            {"page": ln.page, "y": round(ln.y, 2), "gy": round(ln.gy, 2),
             "x": [round(ln.x0, 2), round(ln.x1, 2)]}
            for ln in doc.hlines
            if ln.frame in fids and ln.role == "region"
        ]
        slots = []
        for i, s in enumerate(tbl.slots):
            slots.append(
                {
                    "slot": i,
                    "between": s["between"],
                    "seam": s.get("seam"),
                    "candidates": [
                        {**c, "gy": round(c["gy"], 2)} for c in s.get("candidates", [])
                    ],
                    "choice_count": len(s.get("choices", [])),
                }
            )
        seams = [
            {
                "gy": round(s.gy, 2),
                "between_frames": [s.prev_frame, s.next_frame],
                "kind": s.kind,
                "marker_bid": s.marker_bid if s.marker_bid >= 0 else None,
            }
            for s in tbl.seams
        ]
        fragments = [
            b.src(cfg.coord_digits)
            for b in doc.blocks
            if b.frame in fids and b.kind == "fragment"
        ]
        markers = [
            {"bid": b.bid, "page": b.page, "text": b.text, "id": b.text_id}
            for b in doc.blocks
            if b.frame in fids and b.kind == "marker"
        ]
        out_tables.append(
            {
                "tid": tbl.tid,
                "name": tbl.name,
                "status": tbl.status,
                "columns": list(tbl.columns),
                "frames": [
                    {
                        "fid": f.fid,
                        "page": f.page,
                        "bbox": [round(f.x0, 2), round(f.top, 2), round(f.x1, 2), round(f.bottom, 2)],
                        "table_name": f.name,
                        "columns": list(f.columns),
                        "column_centers": [round(c, 2) for c in f.centers],
                        "column_bounds": [[round(lo, 2), round(hi, 2)] for lo, hi in f.bounds],
                        "body_top": round(f.body_top, 2),
                        "body_bottom": round(f.body_bottom, 2),
                        "y_offset": round(f.y_off, 2),
                    }
                    for f in frames
                ],
                "anchors": anchors,
                "row_candidate_lines": cand,
                "region_lines": region_lines,
                "region_intervals": [
                    {
                        "value": iv.value,
                        "gy": [round(iv.gy_top, 2), round(iv.gy_bottom, 2)],
                        "block_bids": iv.block_bids,
                    }
                    for iv in tbl.region_intervals
                ],
                "seams": seams,
                "markers": markers,
                "fragments": fragments,
                "slots": slots,
                "solution_count": len(tbl.solutions),
                "problems": tbl.problems,
                "notes": tbl.notes,
            }
        )
    return {
        "pdf": doc.path,
        "page_count": doc.page_count,
        "config": cfg.public_dict(),
        "tables": out_tables,
        "ignored_texts": doc.ignored,
        "problems": doc.problems,
    }
