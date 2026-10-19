"""An in-memory Herdr for offline tests of apply / back / pick.

Patched in as `herdr.call` and `herdr.run_in`, so every Herdr request heelr
makes lands here and nothing reaches a real server. Models just enough:
workspaces of tabs, each tab a split tree; `pane move` (including Herdr
0.9.1's same-tab no-op and tabs closing when emptied), `pane split`,
`pane swap`, `pane resize`, `pane close`, `pane layout`, `pane get`,
`pane process-info`, `api snapshot`, `tab focus` and `notification show`.
Swap and resize follow what a throwaway 0.9.3 server did: swap exchanges the
two panes and focuses the source; resize moves the nearest divider on that
side of the pane by `--amount` (default 0.05) and stops at 0.1 / 0.9.

`fail_at = k` makes the k-th mutating request (move/split, 1-based) fail
before it changes anything, to cut an apply part way. `fail_requests` (a set
of (noun, verb) pairs, e.g. ("pane", "close")) fails every such request, and
`fail_run_in` fails `pane run`.

When a tab closes because its last pane left, focus moves to the tab before
it in the workspace (else the one after), as Herdr does; when the focused
pane leaves a tab that stays open, focus moves to that tab's first pane.
"""
from __future__ import annotations

import copy

from heelr import core, herdr
from heelr.tree import Leaf, Node, Split, leaves

AREA = (200, 50)
MUTATING = {("pane", "move"), ("pane", "split"), ("pane", "swap"), ("pane", "resize")}


def _remove(tree: Node, pane: str) -> Node | None:
    if isinstance(tree, Leaf):
        return None if tree.pane == pane else tree
    a, b = _remove(tree.a, pane), _remove(tree.b, pane)
    if a is None:
        return b
    if b is None:
        return a
    return Split(tree.dir, tree.ratio, a, b)


def _insert(tree: Node, target: str, pane: str, d: str, ratio: float) -> Node:
    if isinstance(tree, Leaf):
        return Split(d, ratio, tree, Leaf(pane=pane)) if tree.pane == target else tree
    return Split(tree.dir, tree.ratio, _insert(tree.a, target, pane, d, ratio),
                 _insert(tree.b, target, pane, d, ratio))


def render(tree: Node, focused: str | None = None) -> dict:
    """tree -> Herdr layout JSON (leaf rects + split rects)."""
    panes, splits = [], []

    def walk(n, x, y, w, h):
        rect = {"x": x, "y": y, "width": w, "height": h}
        if isinstance(n, Leaf):
            panes.append({"pane_id": n.pane, "focused": n.pane == focused, "rect": rect})
            return
        splits.append({"direction": n.dir, "ratio": n.ratio, "rect": rect, "id": f"s{len(splits)}"})
        if n.dir == "right":
            aw = min(max(1, round(w * n.ratio)), w - 1)
            walk(n.a, x, y, aw, h)
            walk(n.b, x + aw, y, w - aw, h)
        else:
            ah = min(max(1, round(h * n.ratio)), h - 1)
            walk(n.a, x, y, w, ah)
            walk(n.b, x, y + ah, w, h - ah)

    walk(tree, 0, 0, *AREA)
    return {"area": {"x": 0, "y": 0, "width": AREA[0], "height": AREA[1]},
            "panes": panes, "splits": splits, "zoomed": False}


class FakeHerdr:
    version = "0.9.1"

    def __init__(self):
        self.tabs: dict[str, dict] = {}     # tab_id -> {ws, label, number, tree}
        self.agents: dict[str, str] = {}    # pane -> agent name
        self.procs: dict[str, dict] = {}    # pane -> process_info
        self.focused_tab: str | None = None
        self.focused_pane: str | None = None
        self.workspaces: dict[str, str] = {}   # ws_id -> label
        self.cwd: dict[str, str] = {}          # pane -> folder
        self.n_ws = 0
        self.n_pane = self.n_tab = 0
        self.calls: list[tuple] = []
        self.ran: list[tuple[str, str]] = []
        self.notifications: list[str] = []
        self.fail_at: int | None = None
        self.fail_requests: set[tuple[str, str]] = set()
        self.fail_run_in = False
        self.mutations = 0
        self.on_call = None                 # hook(args) run before each request

    # ------------------------------------------------------------ building
    def add_workspace(self, label: str) -> str:
        self.n_ws += 1
        ws = f"w{self.n_ws}"
        self.workspaces[ws] = label
        return ws

    def add_tab(self, ws: str, label: str, n_panes: int = 1, d: str = "right",
                agents: tuple[int, ...] = (), cwd: str | None = None) -> tuple[str, list[str]]:
        self.workspaces.setdefault(ws, ws)
        self.n_tab += 1
        tid = f"{ws}:t{self.n_tab}"
        ids = [self._new_pane(ws) for _ in range(n_panes)]
        for pid in ids:
            self._default_proc(pid)
            self.cwd[pid] = cwd or "/"
        tree: Node = Leaf(pane=ids[-1])
        for i in range(n_panes - 2, -1, -1):
            tree = Split(d, round(1 / (n_panes - i), 3), Leaf(pane=ids[i]), tree)
        number = 1 + sum(t["ws"] == ws for t in self.tabs.values())
        self.tabs[tid] = {"ws": ws, "label": label, "number": number, "tree": tree}
        for i in agents:
            self.agents[ids[i]] = "claude"
        if self.focused_tab is None:
            self.focused_tab, self.focused_pane = tid, ids[0]
        return tid, ids

    def _new_pane(self, ws: str) -> str:
        self.n_pane += 1
        return f"{ws}:p{self.n_pane}"

    def _default_proc(self, pid: str) -> None:
        n = int(pid.rsplit("p", 1)[1])
        self.procs[pid] = {"shell_pid": 1000 + n, "foreground_process_group_id": 1000 + n,
                           "foreground_processes": [{"cmdline": "bash"}]}

    def _renumber(self, pane: str, ws: str) -> str:
        new = self._new_pane(ws)
        for d in (self.agents, self.cwd, self.procs):
            if pane in d:
                d[new] = d.pop(pane)
        if self.focused_pane == pane:
            self.focused_pane = new
        return new

    def busy(self, pane: str, cmd: str = "vim notes") -> None:
        self.procs[pane] = {"shell_pid": 1, "foreground_process_group_id": 2,
                            "foreground_processes": [{"cmdline": cmd}]}

    # ------------------------------------------------------------- queries
    def tab_of(self, pane: str) -> str | None:
        return next((t for t, v in self.tabs.items() if pane in self.panes(t)), None)

    def panes(self, tab: str) -> list[str]:
        return [l.pane for l in leaves(self.tabs[tab]["tree"])]

    def tree(self, tab: str) -> Node:
        return copy.deepcopy(self.tabs[tab]["tree"])

    def labels(self) -> list[str]:
        """Tab names, without placement's drift marks (` ↗N`)."""
        return [core.strip_drift(v["label"]) for v in self.tabs.values()]

    def all_panes(self) -> set[str]:
        return {p for t in self.tabs for p in self.panes(t)}

    def snapshot(self) -> dict:
        layouts, panes, tabs = [], [], []
        for tid, t in self.tabs.items():
            lay = render(t["tree"], self.focused_pane)
            lay.update(tab_id=tid, workspace_id=t["ws"],
                       focused_pane_id=self.focused_pane if self.focused_pane in self.panes(tid)
                       else self.panes(tid)[0])
            layouts.append(lay)
            tabs.append({"tab_id": tid, "workspace_id": t["ws"], "label": t["label"],
                         "number": t["number"], "pane_count": len(self.panes(tid))})
            for p in self.panes(tid):
                panes.append({"pane_id": p, "tab_id": tid, "workspace_id": t["ws"],
                              "agent": self.agents.get(p), "cwd": self.cwd.get(p, "/"),
                              "foreground_cwd": self.cwd.get(p, "/")})
        ws = self.tabs[self.focused_tab]["ws"] if self.focused_tab in self.tabs else None
        workspaces = [{"workspace_id": w, "label": l, "number": i + 1}
                      for i, (w, l) in enumerate(self.workspaces.items())]
        return {"workspaces": workspaces, "version": self.version, "focused_tab_id": self.focused_tab,
                "focused_pane_id": self.focused_pane, "focused_workspace_id": ws,
                "layouts": layouts, "panes": panes, "tabs": tabs}

    # ------------------------------------------------------------ mutation
    def _take(self, pane: str) -> str:
        tid = self.tab_of(pane)
        if tid is None:
            raise herdr.HerdrError(f"no pane {pane}")
        rest = _remove(self.tabs[tid]["tree"], pane)
        if rest is None:
            closed = self.tabs.pop(tid)       # Herdr closes an emptied tab
            if self.focused_tab == tid:       # ...and focuses a neighbour
                same = sorted(((v["number"], t) for t, v in self.tabs.items() if v["ws"] == closed["ws"]))
                before = [t for n, t in same if n < closed["number"]]
                after = [t for n, t in same if n > closed["number"]]
                self.focused_tab = (before[-1:] or after[:1] or [None])[0]
                self.focused_pane = self.panes(self.focused_tab)[0] if self.focused_tab else None
        else:
            self.tabs[tid]["tree"] = rest
            if self.focused_pane == pane:
                self.focused_pane = self.panes(tid)[0] if self.focused_tab == tid else self.focused_pane
        return tid

    def _opt(self, args, name, default=None):
        return args[args.index(name) + 1] if name in args else default

    def call(self, *args: str, timeout: float | None = None) -> dict:  # same signature as herdr.call
        self.calls.append(args)
        if self.on_call:
            self.on_call(args)
        if args[:2] in self.fail_requests:
            raise herdr.HerdrError(f"injected failure: {' '.join(args)}")
        if args[:2] in MUTATING:
            self.mutations += 1
            if self.fail_at is not None and self.mutations == self.fail_at:
                raise herdr.HerdrError(f"injected failure on request {self.mutations}: {' '.join(args)}")
        if args[:2] == ("api", "snapshot"):
            return {"snapshot": self.snapshot()}
        if args[:2] == ("pane", "process-info"):
            pane = args[-1]
            if pane not in self.all_panes():
                raise herdr.HerdrError(f"no pane {pane}")
            return {"process_info": dict(self.procs[pane])}
        if args[:2] == ("pane", "layout"):
            tid = self.tab_of(self._opt(args, "--pane"))
            return {"layout": dict(render(self.tabs[tid]["tree"]), tab_id=tid)}
        if args[:2] == ("pane", "get"):
            return {"pane": {"pane_id": args[2], "tab_id": self.tab_of(args[2])}}
        if args[:2] == ("pane", "close"):
            self._take(args[2])
            return {}
        if args[:2] == ("pane", "split"):
            pane, d, ratio = args[2], self._opt(args, "--direction"), float(self._opt(args, "--ratio"))
            tid = self.tab_of(pane)
            if tid is None:
                raise herdr.HerdrError(f"no pane {pane}")
            new = self._new_pane(self.tabs[tid]["ws"])
            self._default_proc(new)
            self.cwd[new] = self.cwd.get(pane, "/")
            self.tabs[tid]["tree"] = _insert(self.tabs[tid]["tree"], pane, new, d, ratio)
            return {"pane": {"pane_id": new, "tab_id": tid}}
        if args[:2] == ("pane", "move"):
            return {"move_result": self._move(args)}
        if args[:2] == ("pane", "swap"):
            return {"swap": self._swap(self._opt(args, "--source-pane"), self._opt(args, "--target-pane"))}
        if args[:2] == ("pane", "resize"):
            return {"resize": self._resize(self._opt(args, "--pane"), self._opt(args, "--direction"),
                                           float(self._opt(args, "--amount", "0.05")))}
        if args[:2] == ("tab", "rename"):
            if args[2] not in self.tabs:
                raise herdr.HerdrError(f"no tab {args[2]}")
            self.tabs[args[2]]["label"] = args[3]
            return {"tab": {"tab_id": args[2], "label": args[3]}}
        if args[:2] == ("tab", "focus"):
            self.focused_tab = args[2]
            return {}
        if args[:2] == ("notification", "show"):
            self.notifications.append(" ".join(args[2:]))
            return {}
        if args[:2] == ("tab", "create"):
            ws = self._opt(args, "--workspace") or self.tabs[self.focused_tab]["ws"]
            if ws not in self.workspaces:
                raise herdr.HerdrError(f"no workspace {ws}")
            tid, (pid,) = self.add_tab(ws, self._opt(args, "--label", ""), 1,
                                       cwd=self._opt(args, "--cwd"))
            return {"tab": {"tab_id": tid, "label": self.tabs[tid]["label"], "workspace_id": ws},
                    "root_pane": {"pane_id": pid, "tab_id": tid, "workspace_id": ws}}
        if args[:2] == ("workspace", "create"):
            ws = self.add_workspace(self._opt(args, "--label", ""))
            tid, (pid,) = self.add_tab(ws, "1", 1, cwd=self._opt(args, "--cwd"))
            return {"workspace": {"workspace_id": ws, "label": self.workspaces[ws]},
                    "root_pane": {"pane_id": pid, "tab_id": tid, "workspace_id": ws}}
        raise AssertionError(f"fake herdr: unexpected request {args}")

    def _move(self, args) -> dict:
        pane = args[2]
        src = self.tab_of(pane)
        if src is None:
            raise herdr.HerdrError(f"no pane {pane}")
        src_ws = self.tabs[src]["ws"]
        was_focused = self.focused_pane == pane   # focus follows a moved focused pane
        if "--workspace" in args and "--new-tab" not in args:
            raise herdr.HerdrError("--workspace needs --new-tab")
        if "--new-tab" in args:
            ws = self._opt(args, "--workspace", src_ws)
            if ws not in self.workspaces:
                raise herdr.HerdrError(f"no workspace {ws}")
            self._take(pane)
            if ws != src_ws:
                pane = self._renumber(pane, ws)
            self.n_tab += 1
            tid = f"{ws}:t{self.n_tab}"
            number = 1 + max((t["number"] for t in self.tabs.values() if t["ws"] == ws), default=0)
            self.tabs[tid] = {"ws": ws, "label": self._opt(args, "--label", ""), "number": number,
                              "tree": Leaf(pane=pane)}
            if "--no-focus" not in args or was_focused:
                self.focused_tab, self.focused_pane = tid, pane
            return {"changed": True, "pane": {"pane_id": pane, "tab_id": tid}}
        tab, target = self._opt(args, "--tab"), self._opt(args, "--target-pane")
        d, ratio = self._opt(args, "--split"), float(self._opt(args, "--ratio", "0.5"))
        if tab == src:
            return {"changed": False, "reason": "same_tab"}
        if tab not in self.tabs or target not in self.panes(tab):
            raise herdr.HerdrError(f"bad move target {tab} {target}")
        self._take(pane)
        if self.tabs[tab]["ws"] != src_ws:
            pane = self._renumber(pane, self.tabs[tab]["ws"])
        self.tabs[tab]["tree"] = _insert(self.tabs[tab]["tree"], target, pane, d, ratio)
        if "--no-focus" not in args or was_focused:
            self.focused_tab, self.focused_pane = tab, pane
        return {"changed": True, "pane": {"pane_id": pane, "tab_id": tab}}

    def _swap(self, src: str, dst: str) -> dict:
        tid = self.tab_of(src)
        if tid is None or self.tab_of(dst) != tid:
            raise herdr.HerdrError(f"bad swap {src} {dst}")
        for l in leaves(self.tabs[tid]["tree"]):
            l.pane = {src: dst, dst: src}.get(l.pane, l.pane)
        self.focused_tab, self.focused_pane = tid, src
        return {"changed": True, "focused_pane_id": src}

    def _resize(self, pane: str, direction: str, amount: float) -> dict:
        tid = self.tab_of(pane)
        if tid is None:
            raise herdr.HerdrError(f"no pane {pane}")
        axis = "right" if direction in ("left", "right") else "down"
        grow = direction in ("right", "down")      # the divider moves right/down: ratio up

        def path(n, trail):
            if isinstance(n, Leaf):
                return trail if n.pane == pane else None
            return path(n.a, trail + [(n, "a")]) or path(n.b, trail + [(n, "b")])

        for s, side in reversed(path(self.tabs[tid]["tree"], [])):
            if s.dir == axis and side == ("a" if grow else "b"):
                s.ratio = min(0.9, max(0.1, s.ratio + (amount if grow else -amount)))
                return {"changed": True}
        return {"changed": False}

    def run_in(self, pane: str, command: str) -> None:
        if self.fail_run_in:
            raise herdr.HerdrError(f"injected failure: pane run {pane}")
        self.ran.append((pane, command))
