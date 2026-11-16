"""数据模型：字块、横线、表框、表、以及求解结果。

坐标约定（公开）：
- 页面坐标：pdfplumber 约定，原点左上，y 向下，字段 (x0, top, x1, bottom)。
- 表内全局纵坐标 gy：同一表的各页表体按页序拼接后的连续纵坐标，
  gy = frame.y_off + (top - frame.body_top)。表体上缘 = 表头分隔线，
  表体下缘 = 表框下缘。gy=0 即该表第一页表体上缘。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    """一个字块：同一视觉行内、水平间隙不超过公开容差的字符序列。

    kind:
      normal   普通属性列字块（参与行归属）
      anchor   编号列中的编号锚点
      marker   续页承接标记（如 Q17（续）），不作为字段值
      stray    编号列中既非编号也非承接标记的异常文字
      fragment 无编号片段（待核实，不归属任何行）
    role:
      body / header / name / ignored
    """

    bid: int
    page: int  # 1 起始页码
    x0: float
    top: float
    x1: float
    bottom: float
    text: str
    frame: int = -1
    col: int = -1
    role: str = "body"
    kind: str = "normal"
    gy0: float = 0.0
    gy1: float = 0.0
    text_id: str = ""  # 承接标记中提取出的编号（如 Q17（续）→ Q17）

    @property
    def gy_center(self) -> float:
        return (self.gy0 + self.gy1) / 2.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2.0

    def src(self, digits: int = 2) -> dict:
        """来源见证：页码 + 原文字块 + 坐标框。"""
        r = lambda v: round(v, digits)
        return {
            "bid": self.bid,
            "page": self.page,
            "bbox": [r(self.x0), r(self.top), r(self.x1), r(self.bottom)],
            "text": self.text,
        }


@dataclass
class HLine:
    """一条水平线（来自 page.edges，含 rect 边）。"""

    page: int
    x0: float
    x1: float
    y: float  # 页面坐标（距页顶）
    frame: int = -1
    role: str = ""  # frame_edge / header_sep / row_candidate / region / fullwidth_body / orphan
    gy: float = 0.0


@dataclass
class Frame:
    """一页上的一个表框。"""

    fid: int
    page: int
    x0: float
    top: float
    x1: float
    bottom: float
    name: str | None = None
    header_sep: float | None = None  # 表头分隔线 y（页面坐标）
    columns: list = field(default_factory=list)  # 列名（有序）
    centers: list = field(default_factory=list)  # 各列标题中心 x
    bounds: list = field(default_factory=list)  # 各列水平区间 (lo, hi)
    table: int = -1
    y_off: float = 0.0  # 该页表体上缘在表内全局纵坐标中的偏移
    marker_bid: int = -1  # 表体上缘的承接标记字块（若有）

    @property
    def body_top(self) -> float:
        return self.header_sep if self.header_sep is not None else self.top

    @property
    def body_bottom(self) -> float:
        return self.bottom

    @property
    def body_height(self) -> float:
        return self.body_bottom - self.body_top


@dataclass
class Seam:
    """同一表相邻两帧（页）之间的接缝。"""

    prev_frame: int
    next_frame: int
    gy: float  # 接缝在全局纵坐标中的位置
    kind: str  # continuation（有承接标记，不强制断行） / hard（强制行界）
    marker_bid: int = -1


@dataclass
class RegionInterval:
    """地区列两条水平短线夹出的跨行区间。"""

    gy_top: float
    gy_bottom: float
    value: str
    block_bids: list
    top_line_idx: int
    bottom_line_idx: int


@dataclass
class Table:
    tid: int
    name: str
    columns: list
    frames: list = field(default_factory=list)  # Frame.fid 列表，按页序
    seams: list = field(default_factory=list)  # Seam
    problems: list = field(default_factory=list)  # 结构问题（致命）
    notes: list = field(default_factory=list)  # 非致命提示（inspect 用）
    to_verify: list = field(default_factory=list)  # 待核实项
    row_flags: dict = field(default_factory=dict)  # 编号 -> 待核实原因
    region_intervals: list = field(default_factory=list)  # RegionInterval
    pending_region: bool = False  # 链入下一帧时地区区间是否开口
    status: str = "ok"  # ok / ambiguous / structural_corruption

    # 求解结果（solver 填充）
    anchors: list = field(default_factory=list)  # Block，按 gy 排序
    solutions: list = field(default_factory=list)  # Solution
    groups: list = field(default_factory=list)  # 隔离组
    records: list = field(default_factory=list)  # 输出行（dict）
    slots: list = field(default_factory=list)  # 槽位证据（inspect 用）


@dataclass
class WorkDoc:
    path: str
    page_count: int = 0
    page_sizes: list = field(default_factory=list)  # [(width, height)] 1 起始页
    blocks: list = field(default_factory=list)  # 全部字块
    hlines: list = field(default_factory=list)
    frames: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    ignored: list = field(default_factory=list)  # 被排除的文字（页眉/页脚/下一表名等）
    problems: list = field(default_factory=list)  # 文档级问题


# 致命结构问题：出现即不再求解，表状态置为 structural_corruption
FATAL_CODES = {
    "unsupported_rotation",
    "frame_overlap",
    "missing_table_name",
    "missing_header_separator",
    "duplicate_column_name",
    "block_crosses_column",
    "unexpected_id_text",
    "duplicate_id",
    "region_value_outside_interval",
    "region_line_crosses_value",
    "unclosed_region_interval",
    "region_continuation_mismatch",
    "marker_mismatch",
    "no_anchors",
    "no_candidate_boundary",
    "no_legal_interpretation",
    "solution_overflow",
}
