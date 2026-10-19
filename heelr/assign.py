"""Roles and pane -> slot assignment (R5: role match, then focus, then order)."""
from __future__ import annotations

import copy
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .tree import Leaf, Node, first_leaf, leaves

DEFAULT_ROLES = [
    {"name": "agent", "agent": True},
    {"name": "logs", "cmd": r"kubectl\s+logs|tail\s+-f|journalctl"},
    {"name": "shell", "fallback": True},
]


@dataclass
class PaneInfo:
    pane_id: str
    agent: str | None = None
    cmdline: str = ""
    focused: bool = False
    role: str | None = None


def load_roles(path: Path | None) -> list[dict]:
    if path and path.exists():
        with open(path, "rb") as f:
            return tomllib.load(f).get("roles", DEFAULT_ROLES)
    return DEFAULT_ROLES


def roles_need_cmdline(roles: list[dict]) -> bool:
    return any("cmd" in r for r in roles)


def classify(p: PaneInfo, roles: list[dict]) -> str | None:
    """First matching role wins."""
    for r in roles:
        if r.get("agent") and p.agent:
            return r["name"]
        if "cmd" in r and p.cmdline and re.search(r["cmd"], p.cmdline):
            return r["name"]
        if r.get("fallback"):
            return r["name"]
    return None


class Mismatch(Exception):
    pass


def assign(tree: Node, panes: list[PaneInfo], on_mismatch: str = "refuse") -> tuple[Node, list[str]]:
    """Bind panes (in current tab order) to the tree's leaves.

    Returns (bound tree, overflow pane ids). Leaves left with pane=None are
    new panes to create (only possible with on_mismatch="fill").
    Role is a preference: role-matching panes are placed first, then any
    leftover pane fills any leftover slot.
    """
    tree = copy.deepcopy(tree)
    slots = list(leaves(tree))
    for s in slots:
        s.pane = None
    if len(slots) != len(panes) and on_mismatch != "fill":
        raise Mismatch(f"layout has {len(slots)} slots but the tab has {len(panes)} panes "
                       f"(set on_mismatch = \"fill\" or use a rule layout)")

    free = list(panes)

    def take(pred) -> PaneInfo | None:
        for p in free:
            if pred(p):
                free.remove(p)
                return p
        return None

    def ok(slot: Leaf):
        return lambda p: slot.role is None or p.role == slot.role

    # 1. main slot: focused pane if role-compatible, else first compatible pane
    for s in slots:
        if s.main:
            p = take(lambda p: p.focused and ok(s)(p)) or take(ok(s))
            if p:
                s.pane = p.pane_id
    # 2. role-matched slots in order (role-less slots wait so they can't steal)
    for s in slots:
        if s.pane is None and s.role is not None:
            p = take(ok(s))
            if p:
                s.pane = p.pane_id
    # 3. anything left, in current order
    for s in slots:
        if s.pane is None:
            p = take(lambda p: True)
            if p:
                s.pane = p.pane_id

    # the anchor (first leaf) must be an existing pane: it never moves
    fl = first_leaf(tree)
    if fl.pane is None:
        donor = next((s for s in reversed(slots) if s.pane), None)
        if donor:
            fl.pane, donor.pane = donor.pane, None

    return tree, [p.pane_id for p in free]


def bind_rule(tree: Node, panes: list[PaneInfo], main_role: str | None) -> Node:
    """Rule layouts: main leaf gets the focused pane (if it has main_role, or
    no main_role is set), else the first pane with main_role, else the focused
    pane; everyone else keeps current order. Slots that name a role are
    filled first with panes of that role. Slots left over when there are
    more slots than panes stay unbound (pane=None: create a new pane)."""
    tree = copy.deepcopy(tree)
    slots = list(leaves(tree))
    free = list(panes)
    for s in slots:  # role slots (agents-logs) take panes of that role, in order
        if s.role:
            pick = next((p for p in free if p.role == s.role), None)
            if pick:
                s.pane = pick.pane_id
                free.remove(pick)
    rest = [s for s in slots if not s.role]
    main = next((s for s in rest if s.main), None)
    if main is not None and free:
        pick = None
        foc = next((p for p in free if p.focused), None)
        if foc and (main_role is None or foc.role == main_role):
            pick = foc
        elif main_role:
            pick = next((p for p in free if p.role == main_role), None)
        pick = pick or foc or free[0]
        main.pane = pick.pane_id
        free.remove(pick)
    for s in rest:
        if s is not main and free:
            s.pane = free.pop(0).pane_id
    for s in slots:  # never drop a pane: any left over take any empty slot
        if s.pane is None and free:
            s.pane = free.pop(0).pane_id
    return tree
