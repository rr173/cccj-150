"""命令行入口：inspect / extract / explain。"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .config import Config
from .extract import extract_pdf
from .inspect import inspect_dict
from .explain import explain_record
from .debug import render_debug


def _config_from_args(args) -> Config:
    cfg = Config()
    if getattr(args, "id_pattern", None):
        cfg.id_pattern = args.id_pattern
    if getattr(args, "region_column", None):
        cfg.region_column_name = args.region_column
    if getattr(args, "id_column", None) is not None:
        cfg.id_column = args.id_column
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="survey_extractor",
        description="调查长表采集器：PDF（带文字层）→ 关系行，附页码/原文字块/坐标",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("pdf")
        p.add_argument("--id-pattern", help="编号正则（默认 Q\\d+）")
        p.add_argument("--region-column", help="地区列名（默认 地区）")
        p.add_argument("--id-column", type=int, help="编号列序号（默认 0）")

    p_ins = sub.add_parser("inspect", help="输出布局证据（定行之前）")
    common(p_ins)
    p_ins.add_argument("-o", "--out", help="证据 JSON 输出路径")

    p_ext = sub.add_parser("extract", help="提取关系行")
    common(p_ext)
    p_ext.add_argument("-o", "--out", help="结果 JSON 输出路径")
    p_ext.add_argument("--debug-dir", help="输出带来源框的调试页到该目录")

    p_exp = sub.add_parser("explain", help="按记录解释")
    common(p_exp)
    p_exp.add_argument("--record", required=True, help="记录编号，如 Q17")
    p_exp.add_argument("--table", help="表名（多表时指定）")

    args = ap.parse_args(argv)
    cfg = _config_from_args(args)

    if args.cmd == "inspect":
        ext = extract_pdf(args.pdf, cfg)
        data = inspect_dict(ext)
        text = json.dumps(data, ensure_ascii=False, indent=1)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"布局证据已写入 {args.out}")
        else:
            print(text)
        return 0

    if args.cmd == "extract":
        ext = extract_pdf(args.pdf, cfg)
        data = ext.to_dict()
        text = json.dumps(data, ensure_ascii=False, indent=1)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"提取结果已写入 {args.out}")
        else:
            print(text)
        if args.debug_dir:
            written = render_debug(ext, args.debug_dir)
            print(f"调试页已写入 {args.debug_dir}（{len(written)} 个文件）")
        n_bad = sum(1 for t in data["tables"] if t["status"] != "ok")
        return 1 if n_bad else 0

    if args.cmd == "explain":
        print(explain_record(args.pdf, args.record, table_name=args.table, cfg=cfg))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
