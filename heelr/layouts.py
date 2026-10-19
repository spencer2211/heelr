"""Layout files: loading, presets, and rule expansion."""
from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .tree import Leaf, Node, Split, from_dict, leaves

PRESET_DIR = Path(__file__).parent / "presets"
RULES = ("main-left", "main-top", "main-right", "main-bottom", "main-left-grid",
         "main-center", "two-mains", "agents-logs", "grid", "even-columns", "even-rows")
ROLE_RULES = {"agents-logs": "agent"}   # rules that split panes by role (default role)
# fewest panes a rule needs to show its shape; add_missing tops up to this.
# grid has no entry: any pane count is a valid grid.
RULE_MIN = {"main-left": 2, "main-top": 2, "main-right": 2, "main-bottom": 2,
            "main-left-grid": 2, "main-center": 2, "two-mains": 3, "even-columns": 2, "even-rows": 2}
KINDS = ("fixed", "rule", "multi")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "heelr"


def user_layout_dir() -> Path:
    return config_dir() / "layouts"


@dataclass
class Layout:
    name: str
    kind: str
    source: Path | None = None
    # fixed
    root: Node | None = None
    on_mismatch: str = "refuse"
    # rule
    rule: str = ""
    main_ratio: float = 0.6
    main_role: str | None = None
    # fixed + rule: create new panes for empty slots (fixed: same as on_mismatch="fill")
    add_missing: bool = False
    new_pane_command: str | None = None     # run in each created pane (default: plain shell)
    raw: dict = field(default_factory=dict)
    # multi
    park_tab: str = "overflow"
    tabs: list["TabSpec"] = field(default_factory=list)


@dataclass
class TabSpec:
    label: str
    gather: list[str]               # roles pulled in from any tab ("*" = any role)
    max: int | None = None          # cap on panes gathered
    layout: "Layout | str | None" = None   # name, inline Layout, or None (leave as is)


def parse(data: dict, source: Path | None = None) -> Layout:
    name = data.get("name") or (source.stem if source else "")
    kind = data.get("kind")
    if kind not in KINDS:
        raise ValueError(f"{name}: kind must be one of {KINDS}, got {kind!r}")
    lay = Layout(name=name, kind=kind, source=source, raw=data)
    lay.add_missing = data.get("add_missing", False)
    if not isinstance(lay.add_missing, bool):
        raise ValueError(f"{name}: add_missing must be true or false")
    lay.new_pane_command = data.get("new_pane_command")
    if kind == "fixed":
        if "root" not in data:
            raise ValueError(f"{name}: fixed layout needs a [root] table")
        lay.root = from_dict(data["root"])
        lay.on_mismatch = data.get("on_mismatch", "fill" if lay.add_missing else "refuse")
        if lay.on_mismatch not in ("refuse", "fill"):
            raise ValueError(f"{name}: on_mismatch must be refuse|fill")
        if lay.add_missing and lay.on_mismatch == "refuse":
            raise ValueError(f"{name}: add_missing = true conflicts with on_mismatch = \"refuse\"")
        lay.add_missing = lay.on_mismatch == "fill"
    elif kind == "multi":
        lay.park_tab = data.get("park_tab", "overflow")
        tabs = data.get("tabs") or []
        if not tabs:
            raise ValueError(f"{name}: multi layout needs at least one [[tabs]] entry")
        labels = [t.get("label") for t in tabs]
        if not all(labels) or len(set(labels)) != len(labels) or lay.park_tab in labels:
            raise ValueError(f"{name}: every [[tabs]] needs a unique label, distinct from park_tab")
        for t in tabs:
            gather = t.get("gather")
            if not gather or not isinstance(gather, list):
                raise ValueError(f"{name}: tab {t['label']!r} needs gather = [roles]")
            sub = t.get("layout")
            if isinstance(sub, dict):
                sub = parse({"name": f"{name}/{t['label']}", **sub})
                if sub.kind == "multi":
                    raise ValueError(f"{name}: a tab's layout can't itself be multi")
            mx = t.get("max")
            if mx is not None and (not isinstance(mx, int) or mx < 1):
                raise ValueError(f"{name}: tab {t['label']!r} max must be a positive integer")
            lay.tabs.append(TabSpec(t["label"], list(gather), mx, sub))
    else:
        lay.rule = data.get("rule", "")
        if lay.rule not in RULES:
            raise ValueError(f"{name}: rule must be one of {RULES}, got {lay.rule!r}")
        lay.main_ratio = float(data.get("main_ratio", 0.6))
        if not 0.0 < lay.main_ratio < 1.0:
            raise ValueError(f"{name}: main_ratio must be in (0, 1)")
        lay.main_role = data.get("main_role")
    return lay


def load_file(path: Path) -> Layout:
    with open(path, "rb") as f:
        return parse(tomllib.load(f), path)


def discover(user_dir: Path | None = None) -> dict[str, Path]:
    """name -> file; user files override presets of the same name."""
    found: dict[str, Path] = {}
    for d in (PRESET_DIR, user_dir or user_layout_dir()):
        if d.is_dir():
            for p in sorted(d.glob("*.toml")):
                found[p.stem] = p
    return found


def load(name: str, user_dir: Path | None = None) -> Layout:
    p = Path(name)
    if p.suffix == ".toml" and p.exists():
        return load_file(p)
    found = discover(user_dir)
    if name not in found:
        raise KeyError(f"no layout named {name!r} (have: {', '.join(sorted(found)) or 'none'})")
    return load_file(found[name])


# ----------------------------------------------------------- rule expansion

def _chain(n: int, d: str) -> Node:
    """n equal slices along direction d."""
    if n == 1:
        return Leaf()
    return Split(d, 1.0 / n, Leaf(), _chain(n - 1, d))


def _grid(n: int) -> Node:
    """Near-square grid: ceil(sqrt(n)) columns, filled column by column."""
    cols = math.ceil(math.sqrt(n))
    sizes = [n // cols + (1 if i < n % cols else 0) for i in range(cols)]
    sizes = [s for s in sizes if s]

    def columns(ss: list[int]) -> Node:
        col = _chain(ss[0], "down")
        if len(ss) == 1:
            return col
        return Split("right", 1.0 / len(ss), col, columns(ss[1:]))

    return columns(sizes)


def expand_rule(rule: str, n: int, main_ratio: float = 0.6,
                n_role: int | None = None, role: str = "agent") -> Node:
    """Tree with n unbound leaves; the main leaf (if any) has main=True.

    `main_ratio` is the main area's share. For role rules (agents-logs),
    `n_role` is how many panes have `role`; those slots carry that role and
    the rest none. Default: all but one.
    """
    if n < 1:
        raise ValueError("need at least one pane")
    if n == 1:
        return Leaf(main=rule not in ("grid", "even-columns", "even-rows", "agents-logs"))
    if rule in ("main-left", "main-top"):
        outer, inner = ("right", "down") if rule == "main-left" else ("down", "right")
        return Split(outer, main_ratio, Leaf(main=True), _chain(n - 1, inner))
    if rule in ("main-right", "main-bottom"):
        outer, inner = ("right", "down") if rule == "main-right" else ("down", "right")
        return Split(outer, 1.0 - main_ratio, _chain(n - 1, inner), Leaf(main=True))
    if rule == "main-left-grid":
        return Split("right", main_ratio, Leaf(main=True), _grid(n - 1))
    if rule == "main-center":   # left stack, main, right stack; the left gets the extra pane
        k = math.ceil((n - 1) / 2)
        if n == 2:
            return Split("right", 1.0 - main_ratio, Leaf(), Leaf(main=True))
        side = (1.0 - main_ratio) / 2
        return Split("right", side, _chain(k, "down"),
                     Split("right", main_ratio / (main_ratio + side), Leaf(main=True),
                           _chain(n - 1 - k, "down")))
    if rule == "two-mains":
        top = Split("right", 0.5, Leaf(main=True), Leaf(main=True))
        return top if n == 2 else Split("down", main_ratio, top, _chain(n - 2, "right"))
    if rule == "agents-logs":
        k = n - 1 if n_role is None else n_role
        if k <= 0 or k >= n:  # only one kind present: plain grid
            return _grid(n)
        top = _grid(k)
        for l in leaves(top):
            l.role = role
        return Split("down", main_ratio, top, _chain(n - k, "right"))
    if rule == "even-columns":
        return _chain(n, "right")
    if rule == "even-rows":
        return _chain(n, "down")
    if rule == "grid":
        return _grid(n)
    raise ValueError(f"unknown rule {rule!r}")
