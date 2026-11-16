"""主提取流程：PDF → 关系行（JSON 可序列化）。"""
from __future__ import annotations

from .config import Config, DEFAULT_CONFIG
from .model import WorkDoc
from .ingest import read_pdf
from .layout import layout_document
from .chain import chain_frames
from .solver import solve_table
from .util import gy_to_page


class Extraction:
    def __init__(self, doc: WorkDoc, cfg: Config):
        self.doc = doc
        self.cfg = cfg

    # ------------------------------------------------------------ JSON
    def to_dict(self) -> dict:
        cfg, doc = self.cfg, self.doc
        tables = []
        for tbl in doc.tables:
            frames = [doc.frames[fid] for fid in tbl.frames]
            t = {
                "tid": tbl.tid,
                "name": tbl.name,
                "columns": list(tbl.columns),
                "pages": sorted({f.page for f in frames}),
                "status": tbl.status,
                "records": tbl.records,
                "isolations": [
                    {
                        "group_id": gi,
                        "rows": g["rows"],
                        "row_ids": [tbl.anchors[r].text for r in g["rows"]],
                        "varying_blocks": g["varying_blocks"],
                        "witnesses": g.get("witnesses", []),
                    }
                    for gi, g in enumerate(tbl.groups)
                ],
                "to_verify": tbl.to_verify,
                "problems": tbl.problems,
                "region_intervals": [
                    {
                        "value": iv.value,
                        "gy": [round(iv.gy_top, 2), round(iv.gy_bottom, 2)],
                        "endpoints": [
                            {"page": gy_to_page(tbl, doc, iv.gy_top)[0],
                             "y": round(gy_to_page(tbl, doc, iv.gy_top)[1], 2)},
                            {"page": gy_to_page(tbl, doc, iv.gy_bottom)[0],
                             "y": round(gy_to_page(tbl, doc, iv.gy_bottom)[1], 2)},
                        ],
                        "block_bids": iv.block_bids,
                    }
                    for iv in tbl.region_intervals
                ],
            }
            tables.append(t)
        return {
            "pdf": doc.path,
            "page_count": doc.page_count,
            "config": cfg.public_dict(),
            "tables": tables,
            "ignored_texts": doc.ignored,
            "problems": doc.problems,
        }


def extract_pdf(path: str, cfg: Config | None = None) -> Extraction:
    cfg = cfg or DEFAULT_CONFIG
    doc = read_pdf(path, cfg)
    layout_document(doc, cfg)
    chain_frames(doc, cfg)
    for tbl in doc.tables:
        solve_table(doc, tbl, cfg)
    return Extraction(doc, cfg)
