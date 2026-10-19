"""pane.created events close together are a burst (a restore, a multi-pane
apply): placed together, without moving focus. Shared across hook processes
through a small state file under its own lock."""
from __future__ import annotations

import contextlib
import fcntl
import json

from . import core

KEEP = 10.0
LOOKBACK = 2.0


@contextlib.contextmanager
def _locked():
    with open(core.state_dir() / "burst.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _load() -> list[list]:
    try:
        ev = json.loads((core.state_dir() / "burst.json").read_text())
        return [[str(e[0]), float(e[1])] for e in ev]
    except (OSError, ValueError, TypeError, IndexError):
        return []


def note(pane_id: str, now: float) -> None:
    with _locked():
        ev = [e for e in _load() if e[0] != pane_id] + [[pane_id, now]]
        # keep every chain that ended within KEEP, and the newest chain however long it has run
        groups = _groups(ev)
        keep = [g for g in groups[:-1] if now - g[-1][1] < KEEP] + [groups[-1]]
        core.write_atomic(core.state_dir() / "burst.json", json.dumps([e for g in keep for e in g]))


def _groups(ev: list[list], lookback: float = LOOKBACK) -> list[list[list]]:
    ev = sorted(ev, key=lambda e: e[1])
    groups, cur = [], []
    for e in ev:
        if cur and e[1] - cur[-1][1] > lookback:
            groups.append(cur)
            cur = []
        cur.append(e)
    if cur:
        groups.append(cur)
    return groups


def _cluster(pane_id: str, ev: list[list], lookback: float = LOOKBACK) -> list[list]:
    groups = _groups(ev, lookback)
    return next((g for g in groups if any(e[0] == pane_id for e in g)), [])


def decide(pane_id: str, window: float, now: float, lookback: float = LOOKBACK) -> str:
    with _locked():
        g = _cluster(pane_id, _load(), lookback)
    if len(g) <= 1:
        return "single"
    newest = g[-1]
    if newest[0] == pane_id and now - newest[1] >= window:
        return "burst-last"
    return "burst-wait"


def burst_panes(pane_id: str, lookback: float = LOOKBACK) -> list[str]:
    with _locked():
        return [e[0] for e in _cluster(pane_id, _load(), lookback)]
