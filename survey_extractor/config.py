"""公开的配置与坐标容差约定。

所有数值单位均为 PDF 点（1/72 英寸）。坐标系沿用 pdfplumber 约定：
原点在每个页面的左上角，x 向右、y 向下（字段名 top/bottom 即"距页顶的距离"）。
表内全局纵坐标 gy 的定义见 layout/chain：把同一表各页表体按页序纵向拼接，
gy = 页偏移 + (top - 该页表体上缘)。

本模块中的每一项都是"公开约定"：提取器只依据这些规则工作，
不悄悄改字、不推断数字。任何一项都可通过 CLI 覆盖。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


@dataclass
class Config:
    # ---- 字块归并规则（公开） ----
    line_y_tol: float = 2.0
    """同一视觉行：字符纵向中心与当前行均值之差不超过该值则归入同一行。"""
    char_gap_tol: float = 2.0
    """同一行内相邻字符水平间隙超过该值则切分为两个字块。"""
    header_cluster_gap: float = 4.0
    """表头带内字块横向聚类间隙：间隙不超过该值视为同一列标题。"""

    # ---- 布局判定容差（公开） ----
    col_cross_tol: float = 1.5
    """字块允许越过列边界的最大量；超过即报结构问题 block_crosses_column。"""
    boundary_touch_tol: float = 1.0
    """行界与字块边缘允许接触的量；行界穿过字块内部超过该量即为非法候选。"""
    line_x_tol: float = 2.0
    """判定一条水平短线落在某列横向范围内的容差。"""
    line_up_tol: float = 2.0
    """地区区间端点与选中行界/表体端点的对齐容差。"""
    name_max_dist: float = 48.0
    """表名行下缘距表框上缘的最大距离；最近的该行即表名，更高的行视为页眉。"""
    frame_min_width: float = 100.0
    frame_min_height: float = 30.0
    """表框最小尺寸：小于该尺寸的矩形不视为表框。"""
    full_width_ratio: float = 0.9
    """横线覆盖表宽比例达到该值视为通栏线（表头分隔线/表框边）。"""
    marker_band: float = 60.0
    """续页承接标记必须出现在表体上缘向下该范围内。"""

    # ---- 业务约定（公开） ----
    id_column: int = 0
    """对象编号列的列序号（从 0 计）。"""
    region_column_name: str = "地区"
    """跨行地区列的列名；表中没有该列则不启用地区区间解析。"""
    id_pattern: str = r"Q\d+"
    """编号锚点的正则（整体匹配）。"""
    marker_suffixes: tuple = ("（续）", "(续)", "(continued)", "(Continued)")
    """续页承接标记的后缀：编号 + 后缀，例如 Q17（续）。"""
    max_solutions: int = 2000
    """合法解释枚举上限；超过即报 solution_overflow（防止指数爆炸时静默）。"""

    # ---- 输出 ----
    coord_digits: int = 2
    """输出 JSON 中坐标保留的小数位数。"""

    def id_re(self) -> "re.Pattern":
        return re.compile(rf"^(?:{self.id_pattern})$")

    def marker_re(self) -> "re.Pattern":
        suf = "|".join(re.escape(s) for s in self.marker_suffixes)
        return re.compile(rf"^\s*({self.id_pattern})\s*(?:{suf})\s*$")

    def public_dict(self) -> dict:
        d = asdict(self)
        d["marker_suffixes"] = list(self.marker_suffixes)
        return d


DEFAULT_CONFIG = Config()
