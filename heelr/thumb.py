"""Small box-drawing thumbnails of layout trees, for the picker."""
from __future__ import annotations

from .layouts import Layout, ROLE_RULES, expand_rule
from .tree import Leaf, Node, Split

# (up, down, left, right) -> character
_BOX = {
    (0, 1, 0, 1): "╭", (0, 1, 1, 0): "╮", (1, 0, 0, 1): "╰", (1, 0, 1, 0): "╯",
    (1, 1, 0, 0): "│", (0, 0, 1, 1): "─", (1, 1, 0, 1): "├", (1, 1, 1, 0): "┤",
    (0, 1, 1, 1): "┬", (1, 0, 1, 1): "┴", (1, 1, 1, 1): "┼",
    (1, 0, 0, 0): "│", (0, 1, 0, 0): "│", (0, 0, 1, 0): "─", (0, 0, 0, 1): "─",
}
FILL = "░"

# pane counts used to draw rule layouts
_SAMPLE_N = {"main-left-grid": 5, "main-center": 5, "two-mains": 5, "agents-logs": 5, "even-columns": 3, "even-rows": 3}


def _rects(n: Node, x=0.0, y=0.0, w=1.0, h=1.0):
    if isinstance(n, Leaf):
        yield n, x, y, x + w, y + h
        return
    if n.dir == "right":
        yield from _rects(n.a, x, y, w * n.ratio, h)
        yield from _rects(n.b, x + w * n.ratio, y, w * (1 - n.ratio), h)
    else:
        yield from _rects(n.a, x, y, w, h * n.ratio)
        yield from _rects(n.b, x, y + h * n.ratio, w, h * (1 - n.ratio))


def draw(tree: Node, w: int = 11, h: int = 5) -> list[str]:
    """w x h characters; pane borders share lines. Main/role panes are shaded."""
    hs, vs, fill = set(), set(), set()
    for leaf, fx0, fy0, fx1, fy1 in _rects(tree):
        x0, x1 = round(fx0 * (w - 1)), round(fx1 * (w - 1))
        y0, y1 = round(fy0 * (h - 1)), round(fy1 * (h - 1))
        for x in range(x0, x1):
            hs.update({(x, y0), (x, y1)})
        for y in range(y0, y1):
            vs.update({(x0, y), (x1, y)})
        if leaf.main or leaf.role:
            fill.update((x, y) for x in range(x0 + 1, x1) for y in range(y0 + 1, y1))
    rows = []
    for y in range(h):
        row = ""
        for x in range(w):
            k = ((x, y - 1) in vs, (x, y) in vs, (x - 1, y) in hs, (x, y) in hs)
            k = tuple(int(b) for b in k)
            row += _BOX.get(k, FILL if (x, y) in fill else " ")
        rows.append(row)
    return rows


def for_layout(lay: Layout, w: int = 11, h: int = 5) -> list[str]:
    if lay.kind == "multi":
        mid = f"{len(lay.tabs)} tabs".center(w - 2)[: w - 2]
        blank = " " * (w - 2)
        body = [blank] * (h - 2)
        body[(h - 2) // 2] = mid
        return ["╭" + "─" * (w - 2) + "╮"] + ["│" + r + "│" for r in body] + ["╰" + "─" * (w - 2) + "╯"]
    if lay.kind == "fixed":
        return draw(lay.root, w, h)
    n = _SAMPLE_N.get(lay.rule, 4)
    role = (lay.main_role or ROLE_RULES[lay.rule]) if lay.rule in ROLE_RULES else "agent"
    return draw(expand_rule(lay.rule, n, lay.main_ratio, None, role), w, h)
