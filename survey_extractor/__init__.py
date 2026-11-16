"""调查长表采集器：把支持范围内的 PDF 长表转成关系行。

入口：
- extract_pdf(path)  → 关系行 + 来源见证
- inspect_pdf(path)  → 布局证据（定行之前）
- explain_record(path, id) → 按记录解释
"""
from .config import Config, DEFAULT_CONFIG
from .extract import extract_pdf, Extraction
from .inspect import inspect_pdf
from .explain import explain_record
from .debug import render_debug

__all__ = [
    "Config",
    "DEFAULT_CONFIG",
    "extract_pdf",
    "Extraction",
    "inspect_pdf",
    "explain_record",
    "render_debug",
]
