# -*- coding: utf-8 -*-
"""调查长表采集器测试套件。

覆盖：主例（两种列宽/页脚/页眉/跨行地区/折行备注/跨页未完行）、
绘字顺序不变性、行界歧义、同文异源、缺编号、地区区间冲突、
续页标题不符、三页连续承接，以及 inspect/explain/debug 入口。
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fixtures import make_fixtures as mf
from fixtures import expectations as exp
from survey_extractor import extract_pdf, inspect_pdf, explain_record, render_debug

BBOX_TOL = 4.0


@pytest.fixture(scope="session")
def fx(tmp_path_factory):
    """生成全部合成材料（可复跑）。"""
    out = tmp_path_factory.mktemp("fixtures")
    paths = mf.build_all(str(out))
    return paths


def _table(result, name=None, index=0):
    for t in result["tables"]:
        if name is None or t["name"] == name:
            return t
    raise AssertionError(f"表 {name} 不存在")


def _records_by_id(table):
    return {r["id"]: r for r in table["records"]}


def _assert_field(actual, expected, label):
    """字段值 + 来源（页码/原文/坐标框）对照。"""
    assert actual is not None, f"{label}: 字段缺失"
    assert actual["value"] == expected["value"], f"{label}: 值 {actual['value']!r} != {expected['value']!r}"
    assert actual["empty"] == expected["empty"], f"{label}: empty 标记不一致"
    assert len(actual["sources"]) == len(expected["sources"]), (
        f"{label}: 来源数 {len(actual['sources'])} != {len(expected['sources'])}"
    )
    for i, (a, e) in enumerate(zip(actual["sources"], expected["sources"])):
        assert a["page"] == e["page"], f"{label} 来源{i}: 页码 {a['page']} != {e['page']}"
        assert a["text"] == e["text"], f"{label} 来源{i}: 原文 {a['text']!r} != {e['text']!r}"
        for j in range(4):
            assert abs(a["bbox"][j] - e["bbox"][j]) <= BBOX_TOL, (
                f"{label} 来源{i}: bbox[{j}] {a['bbox'][j]} 与预期 {e['bbox'][j]} 超出容差"
            )


# ---------------------------------------------------------------- 主例
def test_main_extraction(fx):
    result = extract_pdf(fx["main"]).to_dict()
    t = _table(result, "表A：人居环境调查长表")
    expected = exp.expected_main()
    assert t["status"] == expected["status"]
    assert t["pages"] == [1, 2]
    assert t["columns"] == ["编号", "地区", "对象", "备注"]
    recs = _records_by_id(t)
    assert set(recs) == set(expected["records"])
    for rid, erec in expected["records"].items():
        actual = recs[rid]
        assert actual["status"] == "ok", f"{rid} 状态 {actual['status']}"
        for col, efield in erec["fields"].items():
            _assert_field(actual["fields"][col], efield, f"{rid}.{col}")
        if erec.get("spans_page_break"):
            assert actual["spans_page_break"], f"{rid} 应跨页"
            assert actual["pages"] == [1, 2], f"{rid} 来源页应为 [1,2]"


def test_main_q17_complete_q18_clean(fx):
    """主例关键断言：Q17 备注跨页完整，Q18 从该页中部开始且不受污染。"""
    result = extract_pdf(fx["main"]).to_dict()
    recs = _records_by_id(_table(result))
    q17 = recs["Q17"]["fields"]["备注"]
    assert q17["value"] == "第一部分记录：房屋结构完好\n院落排水需要整修\n杂物堆放已现场清理\n后续电话回访确认"
    assert [s["page"] for s in q17["sources"]] == [1, 1, 2, 2]
    q18 = recs["Q18"]["fields"]["备注"]
    assert q18["value"] == "新开工地已备案"
    assert all(s["page"] == 2 for s in q18["sources"])
    # 承接标记不是字段值
    for r in recs.values():
        for f in r["fields"].values():
            if f:
                assert "（续）" not in f["value"]


def test_region_span_and_empty_cells(fx):
    result = extract_pdf(fx["main"]).to_dict()
    recs = _records_by_id(_table(result))
    assert recs["Q11"]["fields"]["地区"]["value"] == "北部"
    assert recs["Q12"]["fields"]["地区"]["value"] == "北部"  # 跨行地区继承
    assert recs["Q13"]["fields"]["地区"]["empty"] is True
    assert recs["Q18"]["fields"]["地区"]["value"] == "南部"
    assert recs["Q19"]["fields"]["地区"]["value"] == "南部"
    # 空单元格与被隔离/未提供的值区分
    assert recs["Q15"]["fields"]["对象"] == {"value": "", "empty": True, "sources": []}
    assert recs["Q12"]["fields"]["备注"]["empty"] is True


def test_footer_header_ignored(fx):
    result = extract_pdf(fx["main"]).to_dict()
    t = _table(result)
    blob = json.dumps(t["records"], ensure_ascii=False)
    assert "- 1 -" not in blob and "- 2 -" not in blob
    assert "调查长表汇编" not in blob
    ignored_texts = {x["text"] for x in result["ignored_texts"]}
    assert "- 1 -" in ignored_texts and "- 2 -" in ignored_texts
    assert "调查长表汇编（内部资料）" in ignored_texts


def test_column_width_independence(fx):
    """两页列宽不同但列名次序相同，字段身份不变。"""
    info = inspect_pdf(fx["main"])
    t = info["tables"][0]
    b1 = t["frames"][0]["column_bounds"]
    b2 = t["frames"][1]["column_bounds"]
    assert b1 != b2, "两页列宽应不同（fixture 设计）"
    assert t["frames"][0]["columns"] == t["frames"][1]["columns"]
    result = extract_pdf(fx["main"]).to_dict()
    recs = _records_by_id(_table(result))
    assert recs["Q18"]["fields"]["对象"]["value"] == "郑某"  # 第二页列宽下仍正确归列


def test_draw_order_invariance(fx):
    """改变 PDF 内部绘字顺序、保持视觉布局，结果必须相同。"""
    a = extract_pdf(fx["main"]).to_dict()
    b = extract_pdf(fx["draw_order"]).to_dict()
    assert json.dumps(a["tables"], ensure_ascii=False, sort_keys=True) == json.dumps(
        b["tables"], ensure_ascii=False, sort_keys=True
    )


# ---------------------------------------------------------------- 歧义与结构损坏
def test_ambiguous_boundary_witness(fx):
    """一段文字落在两条合法候选行界之间 → 隔离 + 两份完整见证，其余行照出。"""
    result = extract_pdf(fx["ambiguous"]).to_dict()
    t = _table(result)
    assert t["status"] == "ambiguous"
    assert len(t["isolations"]) == 1
    g = t["isolations"][0]
    assert g["row_ids"] == ["Q61", "Q62"]
    assert len(g["witnesses"]) == 2
    wa, wb = g["witnesses"]
    float_bid = str(g["varying_blocks"][0])
    # 两种解释的归属不同
    assert wa["assignments"][float_bid] != wb["assignments"][float_bid]
    # 见证完整可复算：全部行界 + 全部字块归属
    assert len(wa["boundaries"]) == 4  # 表体上缘 + 2 槽位 + 表体下缘
    assert len(wa["assignments"]) > 0
    fa = wa["group_row_fields"]
    fb = wb["group_row_fields"]
    assert fa != fb
    assert "待补充材料" in json.dumps(fa, ensure_ascii=False)
    assert "待补充材料" in json.dumps(fb, ensure_ascii=False)
    # 其余可唯一确定的行仍输出
    recs = _records_by_id(t)
    assert recs["Q63"]["status"] == "ok"
    assert recs["Q63"]["fields"]["备注"]["value"] == "六三备注"
    assert recs["Q61"]["status"] == "isolated"
    assert recs["Q61"]["fields"]["备注"] is None  # 被隔离 ≠ 空单元格
    assert recs["Q61"]["fields"]["对象"]["value"] == "甲某"  # 唯一确定的字段仍给出


def test_same_text_different_source_still_ambiguous(fx):
    """两份解释拼出的文本碰巧相同，只要来源归属不同也算歧义。"""
    result = extract_pdf(fx["same_text"]).to_dict()
    t = _table(result)
    assert t["status"] == "ambiguous"
    g = t["isolations"][0]
    wa, wb = g["witnesses"]
    float_bid = str(g["varying_blocks"][0])
    assert wa["assignments"][float_bid] != wb["assignments"][float_bid]
    # 两种解释拼出的整表文本相同（多重集合意义下），但归属不同
    def remark_multiset(w):
        return sorted(f["备注"] for f in w["group_row_fields"].values())

    assert remark_multiset(wa) == remark_multiset(wb)
    assert wa["group_row_fields"] != wb["group_row_fields"]


def test_missing_id_becomes_isolation_not_invention(fx):
    """缺编号：不臆造编号；无编号可视行造成两种合法归属 → 隔离。"""
    result = extract_pdf(fx["missing_id"]).to_dict()
    t = _table(result)
    assert t["status"] == "ambiguous"
    ids = [r["id"] for r in t["records"]]
    assert ids == ["Q71", "Q72", "Q73"], "不得臆造编号"
    g = t["isolations"][0]
    assert g["row_ids"] == ["Q71", "Q72"]
    wa, wb = g["witnesses"]
    float_bid = str(g["varying_blocks"][0])
    assert wa["assignments"][float_bid] != wb["assignments"][float_bid]
    assert _records_by_id(t)["Q73"]["status"] == "ok"


def test_region_conflict_is_structural_not_ambiguous(fx):
    """地区区间冲突 → 结构损坏，与多解（歧义）分别报告。"""
    result = extract_pdf(fx["region_conflict"]).to_dict()
    t = _table(result)
    assert t["status"] == "structural_corruption"
    codes = {p["code"] for p in t["problems"]}
    assert "no_legal_interpretation" in codes
    assert t["isolations"] == []
    assert t["records"] == []


# ---------------------------------------------------------------- 承接
def test_title_mismatch_no_join(fx):
    """续页标题不符 → 不承接；上页未完范围与下页无编号片段列待核实。"""
    result = extract_pdf(fx["title_mismatch"]).to_dict()
    assert len(result["tables"]) == 2, "表名不符不得接入前表"
    expected = exp.expected_title_mismatch()
    t1 = _table(result, "表D：核查询问记录")
    t2 = _table(result, "表X：另一份独立表")
    q41 = _records_by_id(t1)["Q41"]
    assert q41["status"] == "to_verify"
    assert q41["fields"]["备注"]["value"] == expected["表D：核查询问记录"]["records"]["Q41"]["备注"]
    assert t1["to_verify"][0]["kind"] == "possibly_unfinished_row"
    frags = [v for v in t2["to_verify"] if v["kind"] == "unattached_fragment"]
    assert frags, "下页无编号片段应列待核实"
    frag_texts = [b["text"] for b in frags[0]["blocks"]]
    assert frag_texts == expected["表X：另一份独立表"]["fragment_texts"]
    q42 = _records_by_id(t2)["Q42"]
    assert q42["status"] == "ok"
    assert q42["fields"]["备注"]["value"] == "四二备注"
    # 片段没有被凭空接到最近记录
    assert "遗留" not in json.dumps(t1["records"], ensure_ascii=False)
    assert "遗留" not in json.dumps(t2["records"], ensure_ascii=False)


def test_three_page_continuation(fx):
    """三页连续承接：Q30 备注跨三页完整，Q31 不受污染。"""
    result = extract_pdf(fx["three_page"]).to_dict()
    t = _table(result)
    assert t["status"] == "ok"
    expected = exp.expected_three_page()
    recs = _records_by_id(t)
    q30 = recs["Q30"]
    assert q30["spans_page_break"] is True
    assert q30["pages"] == [1, 2, 3]
    for col, efield in expected["records"]["Q30"]["fields"].items():
        _assert_field(q30["fields"][col], efield, f"Q30.{col}")
    q31 = recs["Q31"]
    assert q31["pages"] == [3]
    assert q31["fields"]["备注"]["value"] == "三一备注"
    assert "三页备注" not in q31["fields"]["备注"]["value"]


# ---------------------------------------------------------------- 入口
def test_inspect_entry(fx):
    info = inspect_pdf(fx["main"])
    t = info["tables"][0]
    assert t["anchors"], "inspect 应列出编号锚点"
    assert t["row_candidate_lines"], "inspect 应列出候选行界"
    assert t["slots"], "inspect 应列出槽位证据"
    assert any(s["seam"] == "continuation" for s in t["slots"] if s.get("seam"))
    assert t["region_intervals"], "inspect 应列出地区区间"
    assert t["markers"], "inspect 应列出承接标记"
    assert info["config"]["line_y_tol"] == 2.0  # 公开容差随证据输出


def test_explain_entry(fx):
    text = explain_record(fx["main"], "Q17")
    assert "承接标记" in text and "Q17（续）" in text
    assert "bid=" in text and "bbox=" in text
    assert "杂物堆放已现场清理" in text
    text2 = explain_record(fx["ambiguous"], "Q61")
    assert "隔离组" in text2


def test_debug_pages_saved(fx, tmp_path):
    ext = extract_pdf(fx["main"])
    out = tmp_path / "debug"
    written = render_debug(ext, str(out))
    pdfs = [p for p in written if p.endswith(".pdf")]
    assert len(pdfs) >= 2, "每个表框页一份调试页"
    for p in pdfs:
        with open(p, "rb") as fh:
            assert fh.read(4) == b"%PDF"
    assert (out / "index.json").exists()


def test_cli_entries(fx, tmp_path):
    env = dict(os.environ, PYTHONPATH=ROOT)
    out_json = tmp_path / "r.json"
    r = subprocess.run(
        [sys.executable, "-m", "survey_extractor", "extract", fx["main"], "-o", str(out_json)],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["tables"][0]["status"] == "ok"
    r2 = subprocess.run(
        [sys.executable, "-m", "survey_extractor", "explain", fx["main"], "--record", "Q17"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert "Q17（续）" in r2.stdout
    r3 = subprocess.run(
        [sys.executable, "-m", "survey_extractor", "extract", fx["region_conflict"],
         "-o", str(tmp_path / "bad.json")],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert r3.returncode == 1, "结构损坏应以非零退出"


def test_expected_files_not_stale(fx):
    """fixtures/expected 中独立写出的预期与当前布局常量保持一致（防漂移）。"""
    for name, fn in exp.ALL.items():
        committed = json.load(
            open(os.path.join(ROOT, "fixtures", "expected", f"{name}.expected.json"),
                 encoding="utf-8")
        )
        assert committed == fn(), f"{name}.expected.json 已过期，请重跑 python -m fixtures.expectations"
