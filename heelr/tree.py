"""Binary split trees, mirroring Herdr's own layout model.

A Split's `ratio` is the share of its FIRST child (`a`), matching Herdr's
`--ratio` semantics (verified on 0.9.1: the target/existing pane's share).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Union

DIRS = ("right", "down")


@dataclass
class Leaf:
    pane: str | None = None      # bound pane id; None = unbound slot / new pane
    role: str | None = None
    main: bool = False


@dataclass
class Split:
    dir: str
    ratio: float
    a: "Node"
    b: "Node"

    def __post_init__(self) -> None:
        if self.dir not in DIRS:
            raise ValueError(f"split dir must be one of {DIRS}, got {self.dir!r}")
        if not 0.0 < self.ratio < 1.0:
            raise ValueError(f"split ratio must be in (0, 1), got {self.ratio}")


Node = Union[Leaf, Split]


def leaves(n: Node) -> Iterator[Leaf]:
    if isinstance(n, Leaf):
        yield n
    else:
        yield from leaves(n.a)
        yield from leaves(n.b)


def first_leaf(n: Node) -> Leaf:
    while isinstance(n, Split):
        n = n.a
    return n


def splits_preorder(n: Node) -> Iterator[Split]:
    if isinstance(n, Split):
        yield n
        yield from splits_preorder(n.a)
        yield from splits_preorder(n.b)


# ---------------------------------------------------------------- dict / TOML

def from_dict(d: dict) -> Node:
    if "dir" in d:
        for k in ("a", "b"):
            if k not in d:
                raise ValueError(f"split node missing child {k!r}: {d}")
        return Split(d["dir"], float(d.get("ratio", 0.5)), from_dict(d["a"]), from_dict(d["b"]))
    return Leaf(pane=d.get("pane"), role=d.get("role"), main=bool(d.get("main", False)))


def to_dict(n: Node, with_panes: bool = False) -> dict:
    if isinstance(n, Split):
        return {"dir": n.dir, "ratio": round(n.ratio, 3),
                "a": to_dict(n.a, with_panes), "b": to_dict(n.b, with_panes)}
    d: dict = {}
    if with_panes and n.pane:
        d["pane"] = n.pane
    if n.role:
        d["role"] = n.role
    if n.main:
        d["main"] = True
    return d


def shape(n: Node) -> str:
    """Compact structural string, e.g. `right(.6)[L,down(.5)[L,L]]`."""
    if isinstance(n, Leaf):
        return n.pane or "L"
    return f"{n.dir}({n.ratio:.2f})[{shape(n.a)},{shape(n.b)}]"


def same_structure(x: Node, y: Node, tol: float = 0.05, check_panes: bool = True) -> list[str]:
    """Return a list of differences (empty = match within ratio tolerance)."""
    out: list[str] = []

    def walk(p: Node, q: Node, path: str) -> None:
        if isinstance(p, Leaf) or isinstance(q, Leaf):
            if not (isinstance(p, Leaf) and isinstance(q, Leaf)):
                out.append(f"{path or 'root'}: leaf/split mismatch")
            elif check_panes and p.pane != q.pane:
                out.append(f"{path or 'root'}: pane {p.pane} != {q.pane}")
            return
        if p.dir != q.dir:
            out.append(f"{path or 'root'}: dir {p.dir} != {q.dir}")
            return
        if abs(p.ratio - q.ratio) > tol:
            out.append(f"{path or 'root'}: ratio {p.ratio:.2f} != {q.ratio:.2f}")
        walk(p.a, q.a, path + "a")
        walk(p.b, q.b, path + "b")

    walk(x, y, "")
    return out


# ------------------------------------------------------ Herdr layout -> tree

def _key(r: dict) -> tuple:
    return (r["x"], r["y"], r["width"], r["height"])


def from_herdr_layout(layout: dict) -> Node:
    """Rebuild the split tree of one tab from `pane layout` / snapshot JSON.

    Herdr reports leaf rects and split rects; split ids are positional and not
    relied on. Each region is cut where no pane straddles the cut line.
    """
    panes = [(p["pane_id"], p["rect"]) for p in layout["panes"]]
    splits = {_key(s["rect"]): s for s in layout.get("splits", [])}

    def build(region: dict, members: list) -> Node:
        if len(members) == 1:
            return Leaf(pane=members[0][0])
        s = splits.get(_key(region))
        order = [s["direction"]] if s else []
        order += [d for d in DIRS if d not in order]
        for d in order:
            cut = _find_cut(region, members, d, s["ratio"] if s and s["direction"] == d else 0.5)
            if cut is None:
                continue
            a, b, ra, rb, ratio = cut
            if s and s["direction"] == d:
                ratio = s["ratio"]
            return Split(d, ratio, build(ra, a), build(rb, b))
        raise ValueError(f"cannot decompose region {region} with panes {[m[0] for m in members]}")

    return build(layout["area"], panes)


def _find_cut(region: dict, members: list, d: str, want: float):
    lo, size = (region["x"], region["width"]) if d == "right" else (region["y"], region["height"])
    pos = "x" if d == "right" else "y"
    ext = "width" if d == "right" else "height"
    cands = sorted({r[pos] + r[ext] for _, r in members if lo < r[pos] + r[ext] < lo + size})
    valid = [c for c in cands
             if all(r[pos] + r[ext] <= c or r[pos] >= c for _, r in members)]
    if not valid:
        return None
    c = min(valid, key=lambda c: abs((c - lo) / size - want))
    a = [m for m in members if m[1][pos] < c]
    b = [m for m in members if m[1][pos] >= c]
    ra, rb = dict(region), dict(region)
    ra[ext] = c - lo
    rb[pos], rb[ext] = c, lo + size - c
    return a, b, ra, rb, (c - lo) / size
