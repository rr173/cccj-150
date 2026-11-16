"""共享小工具。"""
from __future__ import annotations


def assemble_blocks(blocks, cfg) -> str:
    """字段拼接（公开规则）：
    同一视觉行内的字块按 x 序直接相连；行与行之间以 \\n 相连。
    不增删任何字符；每个字块的来源单独保留在 sources 中。
    """
    lines = []
    for b in sorted(blocks, key=lambda b: (b.gy0, b.x0)):
        if lines and abs(b.gy0 - lines[-1][0].gy0) <= cfg.line_y_tol:
            lines[-1].append(b)
        else:
            lines.append([b])
    return "\n".join("".join(x.text for x in sorted(ln, key=lambda b: b.x0)) for ln in lines)


def gy_to_page(table, doc, gy: float):
    """全局纵坐标 → (页码, 页面 y)。表体按页序拼接的逆映射。"""
    acc_frames = [doc.frames[fid] for fid in table.frames]
    for f in acc_frames:
        if f.y_off - 1e-6 <= gy <= f.y_off + f.body_height + 1e-6:
            return f.page, gy - f.y_off + f.body_top
    f = acc_frames[-1]
    return f.page, gy - f.y_off + f.body_top
