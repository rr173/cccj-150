"""explain：按记录解释它是怎么被采出来的。"""
from __future__ import annotations

from .config import Config, DEFAULT_CONFIG
from .extract import extract_pdf
from .util import gy_to_page


def explain_record(path: str, record_id: str, table_name: str | None = None,
                   cfg: Config | None = None) -> str:
    ext = extract_pdf(path, cfg)
    doc, c = ext.doc, ext.cfg
    for tbl in doc.tables:
        if table_name and tbl.name != table_name:
            continue
        for row, anchor in enumerate(tbl.anchors):
            if anchor.text == record_id:
                return _explain(ext, tbl, row)
    return f"未找到记录 {record_id}" + (f"（表 {table_name}）" if table_name else "")


def _explain(ext, tbl, row: int) -> str:
    doc, cfg = ext.doc, ext.cfg
    anchor = tbl.anchors[row]
    rec = next((r for r in tbl.records if r["row_index"] == row), None)
    L = []
    L.append(f"表「{tbl.name}」 记录 {anchor.text}（行序 {row}，状态 {rec['status'] if rec else '无'}）")
    L.append(f"编号锚点：第 {anchor.page} 页 bbox={_bb(anchor)}")
    if tbl.solutions:
        sol = tbl.solutions[0]
        bounds = [0.0] + [b.gy for b in sol.boundaries] + [
            sum(doc.frames[fid].body_height for fid in tbl.frames)
        ]
        for tag, gy in (("上行界", bounds[row]), ("下行界", bounds[row + 1])):
            page, y = gy_to_page(tbl, doc, gy)
            L.append(f"{tag}：gy={gy:.1f}（第 {page} 页 y={y:.1f}）")
    for s in tbl.seams:
        lo = anchor.gy0 - 1e6  # 仅用于展示：该记录是否跨接缝
    spans = []
    if tbl.solutions and rec and rec.get("spans_page_break"):
        for s in tbl.seams:
            pf, nf = doc.frames[s.prev_frame], doc.frames[s.next_frame]
            spans.append(f"第 {pf.page}→{nf.page} 页（{s.kind}）")
    if spans:
        L.append("跨页承接：" + "、".join(spans))
        for s in tbl.seams:
            if s.kind == "continuation" and s.marker_bid >= 0:
                m = doc.blocks[s.marker_bid]
                L.append(f"  承接标记「{m.text}」bid={m.bid} 第 {m.page} 页（不作为字段值）")
    if rec:
        for name, payload in rec["fields"].items():
            if payload is None:
                L.append(f"字段「{name}」：不唯一（隔离中），见隔离组见证")
                continue
            if payload.get("empty"):
                L.append(f"字段「{name}」：空单元格（确认无字块）")
                continue
            L.append(f"字段「{name}」= {payload['value']!r}")
            for s_ in payload["sources"]:
                L.append(f"    来源 bid={s_['bid']} 第 {s_['page']} 页 bbox={s_['bbox']} 原文 {s_['text']!r}")
    gi = rec.get("isolation_group") if rec else None
    if gi is not None:
        g = tbl.groups[gi]
        L.append(f"隔离组 #{gi}：行 {g['rows']}，变动字块 {g['varying_blocks']}")
        L.append("  两种完整合法解释见 extract 结果 isolations[].witnesses")
    if rec and rec.get("to_verify_reason"):
        L.append(f"待核实：{rec['to_verify_reason']}")
    if tbl.problems:
        L.append("结构问题：")
        for p in tbl.problems:
            L.append(f"  [{p['code']}] {p['message']}")
    return "\n".join(L)


def _bb(b):
    return [round(b.x0, 2), round(b.top, 2), round(b.x1, 2), round(b.bottom, 2)]
