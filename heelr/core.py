"""apply / save / back: read snapshot -> assign -> plan -> execute -> verify."""
from __future__ import annotations

import contextlib
import copy
import dataclasses
from collections import Counter
import fcntl
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import herdr
from .assign import DEFAULT_ROLES, PaneInfo, assign, bind_rule, classify, load_roles, roles_need_cmdline
from .layouts import ROLE_RULES, RULE_MIN, Layout, config_dir, expand_rule, load, user_layout_dir
from .plan import OVERFLOW_LABEL, PARK, PARK_LABEL, Op, plan
from .tree import (Leaf, Node, Split, first_leaf, from_dict, from_herdr_layout, leaves,
                   same_structure, splits_preorder, to_dict)


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    d = Path(base) / "heelr"
    d.mkdir(parents=True, exist_ok=True)
    return d


_held = threading.local()   # lock depth per thread: a second thread must wait, like a second process
LOCK_WAIT = 2.0   # seconds a command waits for another heelr change (auto-tile's are brief)


def layout_lock_path() -> Path | None:
    """The lock other Herdr layout plugins share: <socket>.layout.lock."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    sock = os.environ.get("HERDR_SOCKET_PATH") or os.path.join(base, "herdr", "herdr.sock")
    p = Path(sock + ".layout.lock")
    return p if p.parent.is_dir() else None


class LockBusy(herdr.HerdrError):
    """Another layout change held the lock past the wait."""


def _flock_until(f, deadline: float) -> None:
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockBusy("another layout change is running") from None
            time.sleep(0.05)


@contextlib.contextmanager
def apply_lock(wait: float | None = None, layout: bool = True):
    """One layout change at a time: heelr's own apply.lock, then (layout=True)
    the <socket>.layout.lock other layout plugins use. Re-entrant within a
    process. `wait`: seconds to wait for both (default LOCK_WAIT)."""
    if getattr(_held, "depth", 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    deadline = time.monotonic() + (LOCK_WAIT if wait is None else wait)
    with contextlib.ExitStack() as stack:
        f = stack.enter_context(open(state_dir() / "apply.lock", "w"))
        _flock_until(f, deadline)
        if layout and (lp := layout_lock_path()) is not None:
            g = stack.enter_context(open(lp, "a"))
            _flock_until(g, deadline)
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0


def lock_unless(dry_run: bool):
    return contextlib.nullcontext() if dry_run else apply_lock()


def write_atomic(p: Path, text: str) -> None:
    """Replace p in one step, so a reader never sees half a record."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    try:
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())  # on disk before the rename makes it the record
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ reading

@dataclass
class TabState:
    tab_id: str
    workspace_id: str
    area: tuple[int, int]
    tree: Node
    panes: list[PaneInfo]
    tab_is_focused: bool
    focused_pane: str | None
    overflow_tab: tuple[str, str] | None = None
    workspace_panes: set[str] = field(default_factory=set)
    roles: list[dict] | None = None   # None: the default roles


def read_tab(snap: dict, tab: str | None, roles: list[dict], with_cmdline=None) -> TabState:
    tab_id = tab or snap["focused_tab_id"]
    lay = next((l for l in snap["layouts"] if l["tab_id"] == tab_id), None)
    if lay is None:
        raise herdr.HerdrError(f"no tab {tab_id!r} in snapshot")
    if lay.get("zoomed"):
        raise herdr.HerdrError(f"tab {tab_id} has a zoomed pane; unzoom first")
    tree = from_herdr_layout(lay)
    by_id = {p["pane_id"]: p for p in snap["panes"]}
    need_cmd = roles_need_cmdline(roles)
    get_cmd = with_cmdline or herdr.cmdline
    infos = []
    for leaf in leaves(tree):
        p = by_id.get(leaf.pane, {})
        info = PaneInfo(leaf.pane, agent=p.get("agent"), focused=leaf.pane == lay.get("focused_pane_id"))
        if need_cmd and not info.agent:
            try:
                info.cmdline = get_cmd(leaf.pane)
            except herdr.HerdrError:
                pass
        info.role = classify(info, roles)
        infos.append(info)
    ws = lay["workspace_id"]
    ov = next((t for t in snap["tabs"] if t["workspace_id"] == ws and base_label(t) == OVERFLOW_LABEL
               and t["tab_id"] != tab_id), None)
    ov_pair = None
    if ov:
        ov_pane = next((p["pane_id"] for p in snap["panes"] if p["tab_id"] == ov["tab_id"]), None)
        if ov_pane:
            ov_pair = (ov["tab_id"], ov_pane)
    return TabState(tab_id, ws, (lay["area"]["width"], lay["area"]["height"]), tree, infos,
                    tab_id == snap.get("focused_tab_id"), lay.get("focused_pane_id"), ov_pair,
                    {p["pane_id"] for p in snap["panes"] if p["workspace_id"] == ws}, roles)


SLEEP_MARK = "💤 "   # `heelr sleep` puts this before a sleeping tab's label
DRIFT_MARK = " ↗"    # placement puts this and a count after a tab's label (heelr.marks)
_DRIFT_RE = re.compile(re.escape(DRIFT_MARK) + r"\d+$")


def strip_drift(label: str) -> str:
    """The label without a trailing drift mark (` ↗3`)."""
    return _DRIFT_RE.sub("", label) or label


def base_label(tab: dict) -> str:
    """A tab's label without the sleep and drift marks: what every label comparison uses."""
    return strip_drift((tab.get("label") or "").removeprefix(SLEEP_MARK))


PARK_HELP = (f"If `heelr back` can't restore it, move the panes in {PARK_LABEL} back by hand, "
             f"or close the {PARK_LABEL} tab if it is empty, then apply again.")


def park_tab_open(snap: dict, ws_id: str) -> bool:
    return any(t["workspace_id"] == ws_id and base_label(t) == PARK_LABEL for t in snap["tabs"])


def refuse_if_parked(snap: dict, ws_id: str) -> None:
    """An earlier apply stopped with panes parked. Another apply would record
    that half-done state as the undo point and strand the parked panes."""
    if park_tab_open(snap, ws_id):
        raise herdr.HerdrError(f"a '{PARK_LABEL}' tab is open in this workspace: an earlier apply "
                               f"stopped part way. Run `heelr back` first. {PARK_HELP}")


# ----------------------------------------------------------------- planning

def target_for(layout: Layout, ts: TabState) -> tuple[Node, list[str]]:
    """Bound target tree plus panes that don't fit. With add_missing, slots
    with no pane stay unbound (pane=None) and are created as new shells."""
    if layout.kind == "fixed":
        return assign(layout.root, ts.panes, layout.on_mismatch)
    n = len(ts.panes)
    if layout.rule in ROLE_RULES:
        role = layout.main_role or ROLE_RULES[layout.rule]
        n_role = sum(p.role == role for p in ts.panes)
        total = n
        # Every pane has the role: add one pane for the other band, so the next
        # apply sees both bands filled. Only if the new pane (a shell, or
        # new_pane_command) won't itself have the role: else it joins the full
        # band and every apply adds another. With none of the role it's a
        # plain grid, for the same reason.
        new_role = classify(PaneInfo("$new", cmdline=layout.new_pane_command or ""),
                            ts.roles or DEFAULT_ROLES)
        if layout.add_missing and n_role == n and new_role != role:
            total = n + 1
        tree = expand_rule(layout.rule, total, layout.main_ratio, n_role, role)
        return bind_rule(tree, ts.panes, None), []
    total = max(n, RULE_MIN.get(layout.rule, 1)) if layout.add_missing else n
    tree = expand_rule(layout.rule, total, layout.main_ratio)
    return bind_rule(tree, ts.panes, layout.main_role), []


@dataclass
class Result:
    ops: list[Op] = field(default_factory=list)
    done: int = 0
    noop: bool = False
    drift: list[str] = field(default_factory=list)
    new_panes: dict[str, str] = field(default_factory=dict)


def make_plan(target: Node, ts: TabState, overflow: list[str]) -> list[Op]:
    focus = ts.focused_pane if ts.tab_is_focused and ts.focused_pane != first_leaf(target).pane else None
    current = [p.pane_id for p in ts.panes]
    external = {l.pane for l in leaves(target) if l.pane} - set(current)
    return plan(target, ts.tab_id, current, overflow, ts.overflow_tab, focus, ts.area, external)


# ---------------------------------------------------------------- executing

def execute(ops: list[Op], ts: TabState, res: Result, log=print, on_created=None) -> None:
    """Run the ops. `on_created(pane_id)` is called as soon as each new pane
    exists, so a later failure can't hide it from `back`."""
    sym: dict[str, str] = {}
    r = lambda x: sym.get(x, x)
    for op in ops:
        if op.kind in ("park_new", "overflow_new"):
            label = "heelr-park" if op.kind == "park_new" else OVERFLOW_LABEL
            mr = herdr.move(r(op.pane), new_tab=True, workspace=ts.workspace_id, label=label)
            sym[PARK if op.kind == "park_new" else "$overflow"] = mr["pane"]["tab_id"]
        elif op.kind == "split":
            p = herdr.split(r(op.target), op.dir, op.ratio)
            sym[op.pane] = p["pane_id"]
            res.new_panes[op.pane] = p["pane_id"]
            if on_created:
                on_created(p["pane_id"])
        else:
            mr = herdr.move(r(op.pane), tab=r(op.tab), target=r(op.target), split=op.dir,
                            ratio=op.ratio, focus=op.focus)
            if not mr.get("changed", True):
                raise herdr.HerdrError(f"herdr refused move ({mr.get('reason')}): {op.describe()}")
        res.done += 1


def run(target: Node, overflow: list[str], ts: TabState, dry_run: bool, log=print,
        command: str | None = None, last: str | None = "tab", created: list | None = None,
        undo: bool = True) -> Result:
    """`last`: which undo `back` should use from now on (None: the caller,
    a multi-tab apply, records that itself). Every pane created is recorded
    in the tab's undo record at once, and appended to `created` if given.
    `undo=False` (auto-tile) leaves the undo record as it is."""
    res = Result()
    if not overflow and not same_structure(target, ts.tree, tol=0.02):
        res.noop = True
        return res
    res.ops = make_plan(target, ts, overflow)
    if dry_run:
        return res
    with apply_lock():  # from the undo record to the last state write
        if undo:
            save_back(ts)
        if last:  # before anything can fail: back must undo this apply
            set_last(ts.workspace_id, last)
        def note(pid: str) -> None:
            from . import place
            place._note_own(pid)
            entry = {"pane": pid, "command": command}
            add_created(ts.tab_id, [entry])
            if created is not None:
                created.append(entry)

        try:
            execute(res.ops, ts, res, log, on_created=note)
        except herdr.HerdrError as e:
            raise herdr.HerdrError(
                f"stopped after {res.done}/{len(res.ops)} steps: {e}\n"
                f"Nothing was closed. Parked panes are in the 'heelr-park' tab; "
                f"`heelr back` restores the previous layout.") from None
        # verify
        bound = _bind_new(target, res.new_panes)
        actual = from_herdr_layout(herdr.layout(first_leaf(bound).pane))
        res.drift = same_structure(bound, actual, tol=0.05)
        if command:
            for p in res.new_panes.values():
                herdr.run_in(p, command)
    return res


def placed(target: Node, res: Result) -> Node:
    """The target with the panes `run` created bound in."""
    return _bind_new(target, res.new_panes)


def slot_order(tree: Node) -> list[str]:
    """A bound layout's panes in slot order: the main pane(s) first, then
    the rest in tree order. Auto-tile keeps a tab in this order."""
    ls = list(leaves(tree))
    return [l.pane for l in ls if l.main and l.pane] + [l.pane for l in ls if not l.main and l.pane]


def copy_tree(t: Node) -> Node:
    return copy.deepcopy(t)


def _bind_new(target: Node, new_panes: dict[str, str]) -> Node:
    """Planner names created panes $new1.. in split pre-order; mirror that."""
    t = copy_tree(target)
    if first_leaf(t).pane is None:
        first_leaf(t).pane = new_panes.get("$new0")
    n = 0
    for s in splits_preorder(t):
        m = first_leaf(s.b)
        if m.pane is None:
            n += 1
            m.pane = new_panes.get(f"$new{n}")
    return t


# ---------------------------------------------------------------- back state

def _back_path(tab_id: str) -> Path:
    return state_dir() / "back" / (re.sub(r"[^A-Za-z0-9_.-]", "_", tab_id) + ".json")


def save_back(ts: TabState) -> None:
    write_atomic(_back_path(ts.tab_id), json.dumps(
        {"tab_id": ts.tab_id, "tree": to_dict(ts.tree, with_panes=True)}, indent=1))


def add_created(tab_id: str, created: list[dict]) -> None:
    """Note panes this apply created, so `back` can close them again."""
    p = _back_path(tab_id)
    if p.exists():
        rec = json.loads(p.read_text())
        rec["created"] = rec.get("created", []) + created
        write_atomic(p, json.dumps(rec, indent=1))


def created_panes(tab_id: str) -> list[dict]:
    p = _back_path(tab_id)
    return json.loads(p.read_text()).get("created", []) if p.exists() else []


def sort_created(created: list[dict], live: set[str], log=print) -> tuple[list[str], list[str]]:
    """Split the live panes an apply created into (to close, still in use).
    Closable: an idle shell, or still running only the command heelr started.
    Asks Herdr; changes nothing."""
    close, used = [], []
    for c in created:
        pid = c["pane"]
        if pid not in live:
            continue
        if _closable(pid, c.get("command"), log):
            close.append(pid)
        else:
            log(f"keeping {pid} (it's running something heelr didn't start); it goes to the overflow tab")
            used.append(pid)
    return close, used


def close_created(created: list[dict], live: set[str], dry_run: bool, log=print) -> list[str]:
    """Close panes an apply created, if they're idle shells or still running
    only the command heelr started. Returns the ids closed (or to close)."""
    closed, _ = sort_created(created, live, log)
    if not dry_run:
        for pid in closed:
            herdr.call("pane", "close", pid)
    return closed


def without(ts: TabState, out: set[str]) -> TabState:
    """The tab as if the panes in `out` had already left it."""
    if not out & {p.pane_id for p in ts.panes}:
        return ts
    keep = {p.pane_id for p in ts.panes} - out
    return dataclasses.replace(ts, panes=[p for p in ts.panes if p.pane_id in keep],
                               tree=prune(ts.tree, keep) or ts.tree)


def _closable(pane: str, command: str | None, log=print) -> bool:
    try:
        info = herdr.call("pane", "process-info", "--pane", pane)["process_info"]
    except herdr.HerdrError:
        return False
    fg = info.get("foreground_processes") or []
    pgid, shell = info.get("foreground_process_group_id"), info.get("shell_pid")
    if pgid is None or shell is None:  # can't tell what runs there: don't guess
        log(f"warning: Herdr gave no process ids for {pane}; can't tell if it's in use")
        return False
    if pgid == shell:
        return True  # the shell itself is in the foreground: nothing running
    if not command or not fg:
        return False
    # Herdr lists the whole foreground group: the command's own stages plus any children they
    # spawned. Every stage must be there, and the group leader must be one of them.
    running = Counter(_norm(f.get("cmdline", "")) for f in fg)
    stages = Counter(_norm(s) for s in command.split("|"))
    lead = next((f for f in fg if f.get("pid") == pgid), fg[0])
    return not stages - running and _norm(lead.get("cmdline", "")) in stages


def _norm(cmdline: str) -> str:
    return " ".join(cmdline.split())


def set_last(ws_id: str, kind: str | None) -> str | None:
    """Remember whether the workspace's last heelr change was a tab or multi-tab
    one. Returns the previous value; None removes the record."""
    p = _back_path("last_" + ws_id)
    prev = p.read_text().strip() if p.exists() else None
    if kind is None:
        p.unlink(missing_ok=True)
    else:
        write_atomic(p, kind)
    return prev


def get_last(ws_id: str) -> str:
    p = _back_path("last_" + ws_id)
    return p.read_text().strip() if p.exists() else "tab"


# ----------------------------------------------------------- promote / nudge

def largest_pane(layout: dict) -> str:
    """The tab's main pane for promote: the largest, ties going to the top-left one."""
    def key(p):
        r = p["rect"]
        return (-r["width"] * r["height"], r["y"], r["x"])
    return min(layout["panes"], key=key)["pane_id"]


def resize_steps(current: Node, target: Node) -> list[tuple[str, str, float]]:
    """(pane, direction, amount) for `herdr pane resize` to give `current` the ratios of
    `target`, a tree of the same shape. Herdr moves the nearest divider on that side of
    the pane, so each split is driven from a leaf that touches its divider."""
    out = []

    def walk(c: Node, t: Node) -> None:
        if isinstance(c, Leaf) or isinstance(t, Leaf):
            return
        d = t.ratio - c.ratio
        if abs(d) > 0.005:
            if d > 0:  # divider moves right/down: from the a side's edge leaf
                n = c.a
                while isinstance(n, Split):
                    n = n.b if n.dir == c.dir else n.a
                out.append((n.pane, "right" if c.dir == "right" else "down", d))
            else:      # left/up: from the b side's edge leaf
                out.append((first_leaf(c.b).pane, "left" if c.dir == "right" else "up", -d))
        walk(c.a, t.a)
        walk(c.b, t.b)

    walk(current, target)
    return out


# ---------------------------------------------------------------- tab record

def _tab_record_path(tab_id: str) -> Path:
    return state_dir() / "tabs" / (re.sub(r"[^A-Za-z0-9_-]", "_", tab_id) + ".json")


def tab_record(tab_id: str) -> dict:
    """What heelr knows about a tab: {"layout": last applied, "ratios": {layout: nudged
    main_ratio}, "panes": [slot order], "auto": bool}. Missing or unreadable: {}.
    Fields of the wrong type are dropped."""
    try:
        rec = json.loads(_tab_record_path(tab_id).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(rec, dict):
        return {}
    out = {}
    if isinstance(rec.get("layout"), str):
        out["layout"] = rec["layout"]
    if isinstance(rec.get("ratios"), dict):
        out["ratios"] = rec["ratios"]
    panes = rec.get("panes")
    if isinstance(panes, list) and all(isinstance(p, str) for p in panes):
        out["panes"] = panes        # slot order, main first (auto-tile)
    if isinstance(rec.get("auto"), bool):
        out["auto"] = rec["auto"]   # False: auto-tile switched off for this tab
    return out


def update_tab_record(tab_id: str, **fields) -> None:
    rec = tab_record(tab_id)
    rec.update(fields)
    write_atomic(_tab_record_path(tab_id), json.dumps(rec, indent=1))


def forget_tab_layout(tab_id: str) -> None:
    """The tab no longer has the layout heelr last applied (back, or a
    multi-tab apply): drop it, keep the nudged ratios."""
    rec = tab_record(tab_id)
    if rec.pop("layout", None) is not None:
        write_atomic(_tab_record_path(tab_id), json.dumps(rec, indent=1))


def tab_ratio(tab_id: str, layout: str) -> float | None:
    """The tab's nudged main_ratio for `layout`, if it has a valid one."""
    r = tab_record(tab_id).get("ratios", {}).get(layout)
    return float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) and 0 < r < 1 else None


def load_back(ts: TabState) -> Node:
    p = _back_path(ts.tab_id)
    if not p.exists():
        raise herdr.HerdrError(f"no previous layout recorded for tab {ts.tab_id}")
    tree = from_dict(json.loads(p.read_text())["tree"])
    here = {x.pane_id for x in ts.panes}
    tree = prune(tree, ts.workspace_panes)   # drop closed panes, reclaim moved-away ones
    if tree is None:
        raise herdr.HerdrError("none of the recorded panes still exist")
    # The first leaf may be in another tab (heelr-park after a cut apply, or
    # moved away by hand): the caller brings it in first. Swapping another pane
    # into its slot would restore the wrong positions.
    extra = here - {l.pane for l in leaves(tree)}
    if extra:
        raise herdr.HerdrError(f"tab has panes the recorded layout doesn't know: {sorted(extra)}; "
                               f"apply a layout instead")
    return tree


def _reanchor(tree: Node | None, here: set[str]) -> Node:
    """The first leaf stays put during apply, so it must already be in the tab.
    Swap the first leaf's pane with a leaf whose pane is here."""
    if tree is None:
        raise herdr.HerdrError("none of the recorded panes still exist")
    ls = list(leaves(tree))
    swap = next((l for l in ls if l.pane in here), None)
    if swap is None:
        raise herdr.HerdrError("none of the recorded panes are still in this tab")
    ls[0].pane, swap.pane = swap.pane, ls[0].pane
    return tree


def prune(n: Node, live: set[str]) -> Node | None:
    if isinstance(n, Leaf):
        return n if n.pane in live else None
    a, b = prune(n.a, live), prune(n.b, live)
    if a and b:
        return Split(n.dir, n.ratio, a, b)
    return a or b


# --------------------------------------------------------------------- save

def toml_str(s: str) -> str:
    """A TOML basic string: quotes, backslashes and control characters escaped."""
    out = []
    for ch in s:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def check_layout_name(name: str) -> None:
    """A layout name becomes <layouts dir>/<name>.toml: it must be a plain file name."""
    if not name or name.startswith(".") or any(c in name for c in "/\\\0"):
        raise ValueError(f"layout name {name!r} must be a plain file name: not empty, "
                         f"no '/' or '\\', not starting with '.'")


def to_toml(name: str, tree: Node) -> str:
    lines = [f"name = {toml_str(name)}", 'kind = "fixed"', 'on_mismatch = "refuse"   # refuse | fill', ""]

    def emit(n: Node, path: str, depth: int) -> None:
        ind = "  " * depth
        lines.append(f"{ind}[{path}]")
        if isinstance(n, Split):
            lines.append(f'{ind}dir = "{n.dir}"')
            lines.append(f"{ind}ratio = {round(n.ratio, 3)}")
            emit(n.a, path + ".a", depth + 1)
            emit(n.b, path + ".b", depth + 1)
        else:
            if n.role:
                lines.append(f"{ind}role = {toml_str(n.role)}")
            if n.main:
                lines.append(f"{ind}main = true")

    emit(tree, "root", 0)
    return "\n".join(lines) + "\n"


def capture(ts: TabState) -> Node:
    t = copy.deepcopy(ts.tree)
    info = {p.pane_id: p for p in ts.panes}
    for l in leaves(t):
        p = info[l.pane]
        l.role, l.main, l.pane = p.role, p.focused, None
    return t
