"""独立预期：直接从 fixtures 布局常量推导记录值与来源页/框。

本模块不导入提取器的任何求解逻辑；预期完全由"我们画了什么"推出。
测试用它与提取器输出对照。运行 `python -m fixtures.expectations <dir>`
可把预期写入 fixtures/expected/ 供人工核对。
"""
from __future__ import annotations

import json
import os

from . import make_fixtures as mf


def _srcs(layout, tags, pages=None):
    """按 tag 收集来源字块（page, text, bbox），按 (页, top) 阅读序。"""
    out = []
    for pg in layout:
        if pages is not None and pg["page"] not in pages:
            continue
        for t in pg["texts"]:
            if t["tag"] in tags:
                out.append(
                    {
                        "page": pg["page"],
                        "text": t["text"],
                        "bbox": [
                            t["x"],
                            t["top"],
                            t["x"] + mf.text_width(t["text"]),
                            t["top"] + mf.SIZE,
                        ],
                    }
                )
    out.sort(key=lambda s: (s["page"], s["bbox"][1]))
    return out


def _value(srcs):
    return "\n".join(s["text"] for s in srcs)


def _field(layout, tags, pages=None):
    srcs = _srcs(layout, tags, pages)
    return {"value": _value(srcs), "empty": not srcs, "sources": srcs}


def _region(layout, tag):
    return _field(layout, {tag})


def expected_main():
    layout = mf.layout_dict(mf.build_main())
    recs = {}

    def rec(rid, remark_tags, obj_tag, region_tag=None, pages=None):
        f = {
            "编号": _field(layout, {f"{rid}:编号"}, pages),
            "地区": _region(layout, region_tag) if region_tag else
                  {"value": "", "empty": True, "sources": []},
            "对象": _field(layout, {obj_tag}) if obj_tag else
                    {"value": "", "empty": True, "sources": []},
            "备注": _field(layout, remark_tags, pages),
        }
        recs[rid] = {"id": rid, "fields": f}

    rec("Q11", {"Q11:备注"}, "Q11:对象", "region:北部")
    rec("Q12", {"Q12:备注"}, "Q12:对象", "region:北部")
    rec("Q13", {"Q13:备注"}, "Q13:对象")
    rec("Q14", {"Q14:备注"}, "Q14:对象")
    rec("Q15", {"Q15:备注"}, None)
    rec("Q16", {"Q16:备注"}, "Q16:对象")
    rec("Q17", {"Q17:备注", "Q17续:备注"}, "Q17:对象")
    recs["Q17"]["spans_page_break"] = True
    rec("Q18", {"Q18:备注"}, "Q18:对象", "region:南部")
    rec("Q19", {"Q19:备注"}, "Q19:对象", "region:南部")
    return {"table": "表A：人居环境调查长表", "status": "ok", "records": recs}


def expected_three_page():
    layout = mf.layout_dict(mf.build_three_page())
    recs = {
        "Q30": {
            "id": "Q30",
            "spans_page_break": True,
            "fields": {
                "编号": _field(layout, {"Q30:编号"}),
                "地区": {"value": "", "empty": True, "sources": []},
                "对象": _field(layout, {"Q30:对象"}),
                "备注": _field(layout, {"Q30:备注", "Q30续:备注"}),
            },
        },
        "Q31": {
            "id": "Q31",
            "spans_page_break": False,
            "fields": {
                "编号": _field(layout, {"Q31:编号"}),
                "地区": {"value": "", "empty": True, "sources": []},
                "对象": _field(layout, {"Q31:对象"}),
                "备注": _field(layout, {"Q31:备注"}),
            },
        },
    }
    return {"table": "表C：三页承接示例", "status": "ok", "records": recs}


def expected_title_mismatch():
    layout = mf.layout_dict(mf.build_title_mismatch())
    return {
        "表D：核查询问记录": {
            "status": "ok",
            "records": {"Q41": {"status": "to_verify", "备注": "四一备注上半部分"}},
            "to_verify_kinds": ["possibly_unfinished_row"],
        },
        "表X：另一份独立表": {
            "status": "ok",
            "records": {"Q42": {"status": "ok", "备注": "四二备注"}},
            "to_verify_kinds": ["unattached_fragment"],
            "fragment_texts": [t["text"] for t in _srcs(layout, {"float:备注"})],
        },
    }


ALL = {
    "main": expected_main,
    "three_page": expected_three_page,
    "title_mismatch": expected_title_mismatch,
}


def write_all(dirpath):
    os.makedirs(dirpath, exist_ok=True)
    for name, fn in ALL.items():
        with open(os.path.join(dirpath, f"{name}.expected.json"), "w", encoding="utf-8") as fh:
            json.dump(fn(), fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    import sys

    write_all(sys.argv[1] if len(sys.argv) > 1 else "fixtures/expected")
    print("expected written")
