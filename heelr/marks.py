"""Drift marks: `<label> ↗N` on each tab holding N panes that belong in another tab.

Best effort and silent: a refresh never fails, never notifies, never takes the layout lock, and
renames a tab only when its mark changes. Every label comparison ignores the mark
(core.base_label), so placement, sleep, layouts and tab-autoname see the same names as before.
"""
from __future__ import annotations

import fcntl
import json
import time
from collections import Counter

from . import core, herdr, place
from .plan import PARK_LABEL
from . import key as keymod
from .assign import load_roles
from .layouts import config_dir

BUDGET = 5.0         # seconds for one refresh, classify scripts included; past it nothing is renamed
LOCK_WAIT = 3.0      # one refresh at a time; one that can't wait leaves marks.pending for the holder
RERUNS = 2           # how often the holder runs again for refreshes that gave up waiting
STATE = "marks.json"  # tab_id -> the label heelr last wrote, so turning placement off can undo it


def marked(label: str, n: int) -> str:
    """`label` with its drift mark set to n (no mark for 0). A sleep mark in front stays."""
    base = core.strip_drift(label)
    return f"{base}{core.DRIFT_MARK}{n}" if n else base


def counts(snap: dict, cfg: keymod.PlacementConfig, roles: list[dict]) -> Counter:
    """Drifted panes per tab, the focused one included, so a count never changes with the focus
    (`sort` still leaves the focused pane where it is)."""
    tab_of = {p["pane_id"]: p["tab_id"] for p in snap["panes"]}
    return Counter(tab_of[p] for p in place.drifted(snap, cfg, roles))


def refresh(allow_untested: bool = False) -> None:
    try:
        if not keymod.load_config().enabled and not (core.state_dir() / STATE).exists():
            return                           # placement off and no marks of heelr's left: nothing to do
    except Exception:
        return
    queued = keymod.take_failures()          # the caller's classify failures stay theirs to report
    try:
        pending = core.state_dir() / "marks.pending"
        with open(core.state_dir() / "marks.lock", "w") as f:
            deadline = time.monotonic() + LOCK_WAIT
            while True:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        pending.touch()      # the holder may be working from an older snapshot
                        return
                    time.sleep(0.05)
            for _ in range(1 + RERUNS):     # so the last event's state wins
                pending.unlink(missing_ok=True)
                _refresh(allow_untested)
                if not pending.exists():
                    break
    except Exception:
        pass
    finally:
        keymod.set_deadline(None)
        keymod.take_failures()               # a refresh stays silent, like the old drift-status
        keymod.FAILURES.extend(queued)


def _refresh(allow_untested: bool) -> None:
    deadline = time.monotonic() + BUDGET
    cfg = keymod.load_config()
    written = _load()
    snap = herdr.snapshot()
    herdr.check_version(snap, allow_untested)
    if not cfg.enabled:
        want = {t["tab_id"]: core.strip_drift(t.get("label") or "") for t in snap["tabs"]
                if written.get(t["tab_id"]) == t.get("label")}      # only marks heelr wrote
    else:
        keymod.set_deadline(deadline)        # a classify script gets only what is left
        n = counts(snap, cfg, load_roles(config_dir() / "roles.toml"))
        if time.monotonic() > deadline:
            return
        # never the park tab: its panes are mid-apply, and tab colours skip it by name
        want = {t["tab_id"]: marked(t.get("label") or "", 0 if core.base_label(t) == PARK_LABEL else n[t["tab_id"]])
                for t in snap["tabs"]}
    now = {t["tab_id"]: t.get("label") or "" for t in snap["tabs"]}
    todo = {tid: new for tid, new in want.items() if new != now[tid]}
    if todo:
        # a tab renamed meanwhile (tab-autoname, by hand) is left for the next refresh
        fresh = {t["tab_id"]: t.get("label") or "" for t in herdr.snapshot()["tabs"]}
        for tid, new in todo.items():
            if fresh.get(tid) != now[tid]:
                continue
            try:
                herdr.call("tab", "rename", tid, new)
            except herdr.HerdrError:
                continue
            now[tid] = new
    live = {tid: lab for tid, lab in now.items() if cfg.enabled and lab != core.strip_drift(lab)}
    if not live:
        (core.state_dir() / STATE).unlink(missing_ok=True)
    elif live != written:
        core.write_atomic(core.state_dir() / STATE, json.dumps(live))


def _load() -> dict:
    try:
        d = json.loads((core.state_dir() / STATE).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}
