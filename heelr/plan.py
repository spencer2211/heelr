"""Pure planner: bound target tree -> ordered Herdr operations.

Herdr 0.9.1 ignores `pane move --target-pane` within the same tab
(`changed:false, reason:same_tab`), so every non-anchor pane is first parked
in a temporary tab, then re-inserted in split pre-order. The park tab closes
itself once empty. Invariant when a split S is processed: S's region is held
by first_leaf(S) alone, so inserting first_leaf(S.b) next to it with S.ratio
reproduces S exactly without disturbing ancestor splits.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from .tree import Leaf, Node, first_leaf, leaves, splits_preorder

PARK = "$park"
OVERFLOW_LABEL = "overflow"
PARK_LABEL = "heelr-park"


@dataclass
class Op:
    kind: str                 # park_new | park | overflow_new | overflow | move | split
    pane: str                 # pane moved, or symbol for a pane created by split
    tab: str | None = None    # destination tab (may be a $symbol)
    target: str | None = None
    dir: str | None = None
    ratio: float | None = None
    focus: bool = False

    def describe(self) -> str:
        if self.kind == "park_new":
            return f"park    {self.pane} -> new tab '{PARK_LABEL}'"
        if self.kind == "overflow_new":
            return f"overflow {self.pane} -> new tab '{OVERFLOW_LABEL}'"
        if self.kind == "split":
            return f"create  {self.pane}: split {self.target} {self.dir} ratio={self.ratio:.3f}"
        return (f"{self.kind:<7} {self.pane} -> {self.tab} next to {self.target} "
                f"{self.dir} ratio={self.ratio:.3f}" + (" (focus)" if self.focus else ""))


def _park_targets(n: int, w: int, h: int) -> list[tuple[int, str]]:
    """For parked panes 1..n-1: (index of pane to split, dir), always splitting
    the largest cell along its longer side (a cell is ~2x taller than wide)."""
    cells = [(w, h)]
    out = []
    for _ in range(n - 1):
        i = max(range(len(cells)), key=lambda k: cells[k][0] * cells[k][1])
        cw, ch = cells[i]
        if cw >= 2 * ch:
            d, a, b = "right", (cw // 2, ch), (cw - cw // 2, ch)
        else:
            d, a, b = "down", (cw, ch // 2), (cw, ch - ch // 2)
        cells[i] = a
        cells.append(b)
        out.append((i, d))
    return out


def plan(target: Node, tab: str, current: list[str], overflow: list[str] = (),
         overflow_tab: tuple[str, str] | None = None, focus_pane: str | None = None,
         area: tuple[int, int] = (200, 50), external: set[str] = frozenset()) -> list[Op]:
    """
    target       tree whose leaves are bound to pane ids (None = create new pane)
    tab          tab being laid out
    current      pane ids currently in the tab (tree order)
    overflow     pane ids that don't fit and go to the overflow tab
    overflow_tab (tab_id, any pane_id in it) if an overflow tab already exists
    focus_pane   pane to re-focus when re-inserted (None = leave focus alone)
    external     pane ids in other tabs that the target pulls in (e.g. `back`
                 reclaiming overflowed panes); they're moved in directly
    """
    target = copy.deepcopy(target)
    bound = [l.pane for l in leaves(target) if l.pane]
    ops: list[Op] = []
    anchor = first_leaf(target).pane
    if anchor is None:
        # the anchor slot is a new pane: create it first (next to any pane in
        # the tab); every existing pane then gets parked and re-inserted
        if not current:
            raise ValueError("tab has no panes")
        anchor = first_leaf(target).pane = "$new0"
        ops.append(Op("split", anchor, tab=tab, target=current[0], dir="right", ratio=0.5))
        current = list(current) + [anchor]
        bound.insert(0, anchor)
    elif anchor not in current:
        raise ValueError(f"anchor {anchor} is not in the tab")
    unknown = set(bound) - set(current) - set(external)
    if unknown:
        raise ValueError(f"target binds panes not in the tab: {sorted(unknown)}")
    if len(set(bound)) != len(bound):
        raise ValueError("target binds a pane twice")
    missing = set(current) - set(bound) - set(overflow)
    if missing:
        raise ValueError(f"panes neither placed nor overflowed: {sorted(missing)}")

    # 1. overflow (never closed, just moved out)
    ov_tab, ov_anchor = overflow_tab or (None, None)
    for p in overflow:
        if ov_tab is None:
            ops.append(Op("overflow_new", p))
            ov_tab, ov_anchor = f"$overflow", p
        else:
            ops.append(Op("overflow", p, tab=ov_tab, target=ov_anchor, dir="right", ratio=0.5))
            ov_anchor = p

    # 2. park every placed pane except the anchor
    parked = [p for p in current if p in bound and p != anchor]
    # (step 0's split must precede parking, or the tab would empty and close)
    if parked:
        ops.append(Op("park_new", parked[0]))
        for (i, d), p in zip(_park_targets(len(parked), *area), parked[1:]):
            ops.append(Op("park", p, tab=PARK, target=parked[i], dir=d, ratio=0.5))

    # 3. re-insert in split pre-order
    n_new = 0
    for s in splits_preorder(target):
        tgt, mover = first_leaf(s.a), first_leaf(s.b)
        if mover.pane is None:
            n_new += 1
            mover.pane = f"$new{n_new}"
            ops.append(Op("split", mover.pane, tab=tab, target=tgt.pane, dir=s.dir, ratio=s.ratio))
        else:
            ops.append(Op("move", mover.pane, tab=tab, target=tgt.pane, dir=s.dir, ratio=s.ratio,
                          focus=mover.pane == focus_pane))
    return ops
