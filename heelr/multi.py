"""Multi-tab layouts: gather panes into labelled tabs by role, then lay out each tab.

Gathering only moves panes between tabs (never closes them); panes no tab
claims go to the park tab. Herdr closes a tab when its last pane leaves, so
the planner simulates tab membership and recreates a destination tab (same
label) if it emptied before its panes arrived.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import core, herdr
from .assign import PaneInfo, classify, roles_need_cmdline
from .layouts import Layout, load
from .plan import Op
from .tree import Node, first_leaf, from_dict, from_herdr_layout, leaves, to_dict


@dataclass
class TabNow:
    tab_id: str
    label: str
    tree: Node
    panes: list[str]


@dataclass
class WorkspaceState:
    ws_id: str
    tabs: list[TabNow]
    info: dict[str, PaneInfo]
    focused_tab: str | None

    def tab_of(self, pane: str) -> str:
        return next(t.tab_id for t in self.tabs if pane in t.panes)

    def by_label(self, label: str) -> TabNow | None:
        return next((t for t in self.tabs if t.label == label), None)

    def all_panes(self) -> list[str]:
        return [p for t in self.tabs for p in t.panes]


@dataclass
class Dest:
    label: str
    tab_id: str | None              # existing tab to fill, or None = create
    panes: list[str] = field(default_factory=list)
    layout: Layout | Node | None = None   # Node = bound tree (used by back)


def read_workspace(snap: dict, ws: str | None, roles: list[dict], with_cmdline=None) -> WorkspaceState:
    ws_id = ws or snap["focused_workspace_id"]
    tabs_meta = sorted((t for t in snap["tabs"] if t["workspace_id"] == ws_id), key=lambda t: t["number"])
    if not tabs_meta:
        raise herdr.HerdrError(f"no workspace {ws_id!r}")
    layouts = {l["tab_id"]: l for l in snap["layouts"]}
    by_id = {p["pane_id"]: p for p in snap["panes"]}
    get_cmd = with_cmdline or herdr.cmdline
    need_cmd = roles_need_cmdline(roles)
    tabs, info = [], {}
    for t in tabs_meta:
        lay = layouts[t["tab_id"]]
        if lay.get("zoomed"):
            raise herdr.HerdrError(f"tab {t['tab_id']} has a zoomed pane; unzoom first")
        tree = from_herdr_layout(lay)
        ids = [l.pane for l in leaves(tree)]
        tabs.append(TabNow(t["tab_id"], core.base_label(t), tree, ids))
        for pid in ids:
            p = by_id.get(pid, {})
            pi = PaneInfo(pid, agent=p.get("agent"), focused=pid == snap.get("focused_pane_id"))
            if need_cmd and not pi.agent:
                try:
                    pi.cmdline = get_cmd(pid)
                except herdr.HerdrError:
                    pass
            pi.role = classify(pi, roles)
            info[pid] = pi
    focused_tab = snap.get("focused_tab_id") if snap.get("focused_workspace_id") == ws_id else None
    return WorkspaceState(ws_id, tabs, info, focused_tab)


# ---------------------------------------------------------------- assignment

def assign_tabs(spec: Layout, ws: WorkspaceState, resolve_layout=load) -> list[Dest]:
    """Decide which pane goes to which tab. Panes already in a tab carrying the
    entry's label are claimed first (so re-applying is stable), then the rest
    of the workspace in tab order. Unclaimed panes go to the park tab."""
    claimed: set[str] = set()
    dests: list[Dest] = []

    def matches(ts, pid):
        return "*" in ts.gather or ws.info[pid].role in ts.gather

    for ts in spec.tabs:
        existing = ws.by_label(ts.label)
        sub = ts.layout if not isinstance(ts.layout, str) else resolve_layout(ts.layout)
        d = Dest(ts.label, existing.tab_id if existing else None, layout=sub)
        if existing:
            for pid in existing.panes:
                if (ts.max is None or len(d.panes) < ts.max) and matches(ts, pid):
                    d.panes.append(pid)
                    claimed.add(pid)
        dests.append(d)
    for ts, d in zip(spec.tabs, dests):
        for pid in ws.all_panes():
            if ts.max is not None and len(d.panes) >= ts.max:
                break
            if pid not in claimed and matches(ts, pid):
                d.panes.append(pid)
                claimed.add(pid)
    rest = [p for p in ws.all_panes() if p not in claimed]
    if rest:
        park = ws.by_label(spec.park_tab)
        dests.append(Dest(spec.park_tab, park.tab_id if park else None, rest, None))
    return [d for d in dests if d.panes]


# ------------------------------------------------------------------ planning

def plan_gather(ws: WorkspaceState, dests: list[Dest]) -> list[Op]:
    """Moves that put every dest's panes into its tab. Tab ids for tabs created
    on the way are symbols `$tab:<label>#n`, resolved at execution."""
    where = {p: t.tab_id for t in ws.tabs for p in t.panes}
    count = {t.tab_id: len(t.panes) for t in ws.tabs}
    ops: list[Op] = []
    n_new = 0
    for d in dests:
        key = d.tab_id
        last = next((p for p in d.panes if where[p] == key), None) if key else None
        flip = 0
        for pid in d.panes:
            if key is not None and where[pid] == key:
                continue
            src = where[pid]
            if key is None or count.get(key, 0) == 0:
                n_new += 1
                key = f"$tab:{d.label}#{n_new}"
                ops.append(Op("gather_new", pid, tab=key, target=d.label))
                count[key] = 0
            else:
                tgt = last or next(p for p, t in where.items() if t == key)
                direction = ("right", "down")[flip % 2]
                flip += 1
                ops.append(Op("gather", pid, tab=key, target=tgt, dir=direction, ratio=0.5))
            count[src] -= 1
            count[key] += 1
            where[pid] = key
            last = pid
        d.tab_id = key
    return ops


def describe(op: Op) -> str:
    if op.kind == "gather_new":
        return f"gather  {op.pane} -> new tab '{op.target}'"
    return f"gather  {op.pane} -> {op.tab} next to {op.target} {op.dir}"


# ----------------------------------------------------------------- executing

def execute_gather(ops: list[Op], ws_id: str) -> dict[str, str]:
    sym: dict[str, str] = {}
    r = lambda x: sym.get(x, x)
    for i, op in enumerate(ops):
        try:
            if op.kind == "gather_new":
                mr = herdr.move(op.pane, new_tab=True, workspace=ws_id, label=op.target)
                sym[op.tab] = mr["pane"]["tab_id"]
            else:
                herdr.move(op.pane, tab=r(op.tab), target=r(op.target), split=op.dir, ratio=op.ratio)
        except herdr.HerdrError as e:
            raise herdr.HerdrError(f"stopped after {i}/{len(ops)} gather moves: {e}\n"
                                   f"Nothing was closed; `heelr back` restores the workspace.") from None
    return sym


def layout_tabs(dests: list[Dest], sym: dict[str, str], roles: list[dict], log=print,
                created: list | None = None) -> tuple[int, int]:
    """Second phase: lay out each destination tab. Returns (problems, tabs changed).
    Panes created for add_missing layouts are appended to `created`."""
    problems = changed = 0
    for d in dests:
        if d.layout is None:
            continue
        tab = sym.get(d.tab_id, d.tab_id)
        snap = herdr.snapshot()
        ts = core.read_tab(snap, tab, roles)
        try:
            if isinstance(d.layout, Layout):
                target, overflow = core.target_for(d.layout, ts)
            else:
                target, overflow = bound_for_tab(d.layout, ts)
            cmd = d.layout.new_pane_command if isinstance(d.layout, Layout) else None
            res = core.run(target, overflow, ts, dry_run=False, command=cmd, last=None, created=created)
        except Exception as e:  # one tab failing shouldn't strand the others
            log(f"  {d.label}: not laid out — {e}")
            problems += 1
            continue
        changed += not res.noop
        state = "unchanged" if res.noop else f"{res.done} step(s)"
        log(f"  {d.label}: {len(ts.panes)} pane(s), {state}" + (f"; drift {res.drift}" if res.drift else ""))
        problems += bool(res.drift)
    return problems, changed


def bound_for_tab(tree: Node, ts: core.TabState) -> tuple[Node, list[str]]:
    """The recorded tree for this tab, plus panes it doesn't know (opened since):
    those go to the overflow tab, as single-tab back does with kept panes."""
    here = {p.pane_id for p in ts.panes}
    t = core.prune(tree, here)
    if t is None or first_leaf(t).pane not in here:
        t = core._reanchor(t, here)
    known = {l.pane for l in leaves(t)}
    return t, [p.pane_id for p in ts.panes if p.pane_id not in known]


# ------------------------------------------------------------- back / state

def _ws_back_path(ws_id: str):
    return core._back_path("workspace_" + ws_id)


def save_ws_back(ws: WorkspaceState) -> str | None:
    """Record the workspace; returns the previous record so a no-op can put it back."""
    p = _ws_back_path(ws.ws_id)
    prev = p.read_text() if p.exists() else None
    core.write_atomic(p, json.dumps({"workspace_id": ws.ws_id, "focused_tab": ws.focused_tab, "tabs": [
        {"tab_id": t.tab_id, "label": t.label, "tree": to_dict(t.tree, with_panes=True)} for t in ws.tabs]},
        indent=1))
    return prev


def add_ws_created(ws_id: str, created: list[dict]) -> None:
    p = _ws_back_path(ws_id)
    if p.exists():
        rec = json.loads(p.read_text())
        rec["created"] = rec.get("created", []) + created
        core.write_atomic(p, json.dumps(rec, indent=1))


def ws_created(ws_id: str) -> list[dict]:
    p = _ws_back_path(ws_id)
    return json.loads(p.read_text()).get("created", []) if p.exists() else []


def restore_ws_back(ws_id: str, prev: str | None) -> None:
    p = _ws_back_path(ws_id)
    if prev is None:
        p.unlink(missing_ok=True)
    else:
        core.write_atomic(p, prev)


def back_dests(ws: WorkspaceState) -> list[Dest]:
    p = _ws_back_path(ws.ws_id)
    if not p.exists():
        raise herdr.HerdrError(f"no previous multi-tab layout recorded for workspace {ws.ws_id}")
    rec = json.loads(p.read_text())
    live = set(ws.all_panes())
    live_tabs = {t.tab_id for t in ws.tabs}
    dests = []
    for t in rec["tabs"]:
        tree = core.prune(from_dict(t["tree"]), live)
        if tree is None:
            continue
        tid = t["tab_id"] if t["tab_id"] in live_tabs else None
        dests.append(Dest(t["label"], tid, [l.pane for l in leaves(tree)], tree))
    if not dests:
        raise herdr.HerdrError("none of the recorded panes still exist")
    return dests


def capture(ws: WorkspaceState, name: str) -> str:
    """Workspace -> multi layout TOML. Each tab gathers its own roles, capped at
    its current pane count, with its current shape as an inline fixed layout."""
    lines = [f"name = {core.toml_str(name)}", 'kind = "multi"', 'park_tab = "overflow"', ""]
    seen = set()
    for t in ws.tabs:
        if t.label == "overflow":
            continue  # its panes land back there as unclaimed
        label = t.label if t.label not in seen else f"{t.label}-{t.tab_id.split(':')[-1]}"
        seen.add(label)
        roles = sorted({ws.info[p].role or "shell" for p in t.panes})
        lines += ["[[tabs]]", f"label = {core.toml_str(label)}",
                  "gather = [" + ", ".join(core.toml_str(r) for r in roles) + "]",
                  f"max = {len(t.panes)}", ""]
        tree = core.copy_tree(t.tree)
        for l in leaves(tree):
            pi = ws.info[l.pane]
            l.role, l.main, l.pane = pi.role, pi.focused, None
        lines += _inline_fixed(tree)
        lines.append("")
    return "\n".join(lines)


def _inline_fixed(tree: Node) -> list[str]:
    from .tree import Split
    out = ["  [tabs.layout]", '  kind = "fixed"', '  on_mismatch = "fill"']

    def emit(n, path, depth):
        ind = "  " * depth
        out.append(f"{ind}[{path}]")
        if isinstance(n, Split):
            out.extend([f'{ind}dir = "{n.dir}"', f"{ind}ratio = {round(n.ratio, 3)}"])
            emit(n.a, path + ".a", depth + 1)
            emit(n.b, path + ".b", depth + 1)
        else:
            if n.role:
                out.append(f"{ind}role = {core.toml_str(n.role)}")
            if n.main:
                out.append(f"{ind}main = true")

    emit(tree, "tabs.layout.root", 2)
    return out
