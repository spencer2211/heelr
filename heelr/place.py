"""Put panes where their key says: plan the moves, run them, undo them."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from . import core, facts, herdr, sleep
from .key import UNSORTED, Key, PlacementConfig, pane_key

# A hook can reach the lock burst_window (max 5 s) + AUTO_WAIT (5 s) after its event, plus snapshots and
# up to 1 s of classify per pane; creators note their panes while holding the lock, so 30 s outlasts that.
OWN_TTL = 30.0


@dataclass(frozen=True)
class Dest:
    workspace: str
    tab: str
    workspace_id: str | None = None      # exact target when labels are not unique (pull)


@dataclass(frozen=True)
class Move:
    pane: str
    dest: Dest
    focus: bool


def dest_for(k: Key, cfg: PlacementConfig) -> Dest | None:
    if k.task == UNSORTED:
        return None
    f = {"repo": k.repo, "task": k.task}          # role never decides a destination
    try:
        return Dest(cfg.workspace.format(**f), cfg.tab.format(**f))
    except (KeyError, IndexError, ValueError, AttributeError):
        return Dest(k.repo, k.task)               # a bad template must not crash the hook


# ------------------------------------------------------------------ records

def _json(name: str, default):
    try:
        return json.loads((core.state_dir() / name).read_text())
    except (OSError, ValueError):
        return default


def _put(name: str, data) -> None:
    core.write_atomic(core.state_dir() / name, json.dumps(data, indent=1))


def _heelr_tabs() -> dict:
    return _json("place-tabs.json", {})        # tab_id -> {"repo","task"}


def own_panes() -> set[str]:
    now = time.time()
    return {p for p, t in _json("place-own.json", {}).items() if now - t < OWN_TTL}


def _note_own(pane: str) -> None:
    now = time.time()
    rec = {p: t for p, t in _json("place-own.json", {}).items() if now - t < OWN_TTL}
    rec[pane] = now
    _put("place-own.json", rec)


# ------------------------------------------------------------------- drift

def _labels(snap: dict) -> tuple[dict, dict]:
    ws_label = {w["workspace_id"]: w.get("label") or "" for w in snap.get("workspaces", [])}
    tab_of = {t["tab_id"]: t for t in snap["tabs"]}
    return ws_label, tab_of


def drifted(snap: dict, cfg: PlacementConfig, roles: list[dict], panes: set[str] | None = None) -> dict[str, Dest]:
    ws_label, tab_of = _labels(snap)
    skip = sleep.sleeping_panes(snap)
    ws_names = set(ws_label.values())
    out = {}
    tab_off: dict[str, bool] = {}
    for sp in snap["panes"]:
        pid = sp["pane_id"]
        if (panes is not None and pid not in panes) or pid in skip:
            continue
        tid = sp["tab_id"]
        if tid not in tab_off:       # auto-tile's per-tab off switch also turns placement off
            tab_off[tid] = core.tab_record(tid).get("auto") is False
        if tab_off[tid]:
            continue
        k = pane_key(sp, cfg, roles)
        d = dest_for(k, cfg)
        if d is None:
            continue
        here = Dest(ws_label.get(sp["workspace_id"], ""), core.base_label(tab_of[sp["tab_id"]]))
        # execute puts a pane into its current workspace when the wanted one is missing and uncreatable
        ws_ok = here.workspace == d.workspace or (not cfg.create_workspaces and d.workspace not in ws_names)
        if not ws_ok or here.tab not in (d.tab, f"{d.tab} · {k.repo}"):
            out[pid] = d
    return out


# ----------------------------------------------------------------- execute

SLEEPING = "<sleeping>"      # _find_tab: the only tab with the wanted label is asleep


def _find_tab(snap: dict, ws_id: str, label: str, k: Key | None) -> tuple[str | None, str]:
    """(tab id to use or None, label to create with). Sleeping tabs are never a destination."""
    mine = _heelr_tabs()
    same = [t for t in snap["tabs"] if t["workspace_id"] == ws_id and core.base_label(t) == label
            and not (t.get("label") or "").startswith(core.SLEEP_MARK)]
    for t in same:
        rec = mine.get(t["tab_id"])
        if rec is None or k is None or rec == {"repo": k.repo, "task": k.task}:
            return t["tab_id"], label
    if not same:
        asleep = any(t["workspace_id"] == ws_id and core.base_label(t) == label
                     and (t.get("label") or "").startswith(core.SLEEP_MARK) for t in snap["tabs"])
        return (SLEEPING if asleep else None), label
    qualified = f"{label} · {k.repo}"
    q = next((x for x in snap["tabs"] if x["workspace_id"] == ws_id and core.base_label(x) == qualified
              and not (x.get("label") or "").startswith(core.SLEEP_MARK)), None)
    return (q["tab_id"] if q else None), qualified


def execute(snap: dict, moves: list[Move], cfg: PlacementConfig, log=print,
            key_of: dict[str, Key] | None = None, exact: bool = False) -> list[dict]:
    """Run the moves in order. A failed Herdr call stops the plan and keeps what was
    done: the returned entries are what `save_undo` / `back` work from."""
    undo: list[dict] = []
    seen: set[str] = set()
    with core.apply_lock():
        for mv in moves:
            if mv.pane in seen:
                log(f"skipped {mv.pane}: already moved in this plan")
                continue
            seen.add(mv.pane)
            try:
                entry = _move_one(mv, cfg, log, key_of, exact)
            except herdr.HerdrError as e:
                log(f"stopped after {len(undo)}/{len(moves)} moves: {e}")
                break
            if entry:
                undo.append(entry)
                _close_initial_shell(entry, log)
    return undo


def _move_one(mv: Move, cfg: PlacementConfig, log, key_of, exact) -> dict | None:
    snap = herdr.snapshot()                       # each move can renumber panes and close tabs
    sp = next((p for p in snap["panes"] if p["pane_id"] == mv.pane), None)
    if sp is None:
        log(f"skipped {mv.pane}: closed")
        return None
    ws_label, _ = _labels(snap)
    ws_id = mv.dest.workspace_id if mv.dest.workspace_id in ws_label else \
        next((w for w, l in ws_label.items() if l == mv.dest.workspace), None)
    created_ws = False
    if ws_id is None:
        if cfg.create_workspaces:
            r = herdr.call("workspace", "create", "--label", mv.dest.workspace,
                           "--cwd", sp.get("cwd") or "/", "--no-focus")
            ws_id, created_ws = r["workspace"]["workspace_id"], True
            _note_own(r["root_pane"]["pane_id"])
            snap = herdr.snapshot()
        else:
            ws_id = sp["workspace_id"]
    k = (key_of or {}).get(mv.pane)
    tab_id, label = _find_tab(snap, ws_id, mv.dest.tab, None if exact else k)
    if tab_id == SLEEPING:
        log(f"skipped {mv.pane}: the '{mv.dest.tab}' tab is asleep; it stays until wake")
        return None
    created_tab = tab_id is None
    if created_tab:
        res = herdr.move(mv.pane, new_tab=True, workspace=ws_id, label=label, focus=mv.focus)
        tab_id = res["pane"]["tab_id"]
        tabs = _heelr_tabs()
        tabs[tab_id] = {"repo": k.repo, "task": k.task} if k else {}
        _put("place-tabs.json", tabs)
    else:
        if tab_id == sp["tab_id"]:
            return None                           # already there
        target = next(p["pane_id"] for p in snap["panes"] if p["tab_id"] == tab_id)
        res = herdr.move(mv.pane, tab=tab_id, target=target, split="right", focus=mv.focus)
    new_pane = res["pane"]["pane_id"]
    if new_pane != mv.pane:
        facts.rekey(mv.pane, new_pane)
    _note_own(new_pane)
    if created_tab:
        # the tab's shape is the layout auto-tile keeps it in as panes arrive: the same record apply writes
        shape = cfg.shape_by_tab.get(mv.dest.tab, cfg.shape_default)
        core.update_tab_record(tab_id, layout=shape, panes=[new_pane])
    from_tab = next((t for t in snap["tabs"] if t["tab_id"] == sp["tab_id"]), {})
    return {"pane": new_pane, "from_ws": sp["workspace_id"], "from_tab": sp["tab_id"],
            "from_label": core.base_label(from_tab), "to_tab": tab_id,
            "created_tab": created_tab, "created_ws": created_ws, "_ws": ws_id}


def _close_initial_shell(entry: dict, log=print) -> None:
    """A workspace heelr created starts with one shell; close it if still idle.
    The move already happened, so a failure here only leaves the shell open."""
    ws_id = entry.pop("_ws")
    if not entry["created_ws"]:
        return
    try:
        for p in herdr.snapshot()["panes"]:
            if p["workspace_id"] == ws_id and p["pane_id"] != entry["pane"] and p["pane_id"] in own_panes():
                if core._closable(p["pane_id"], None, log=lambda *a: None):
                    herdr.call("pane", "close", p["pane_id"])
    except herdr.HerdrError as e:
        log(f"left the new workspace's first shell open: {e}")


# --------------------------------------------------------------------- undo

def save_undo(entries: list[dict], ws: str | None = None, prev_last: str | None = None) -> None:
    """ws: the workspace whose `back` owns this record; prev_last: the marker it replaced."""
    if entries:
        _put("place-back.json", {"moves": entries, "ws": ws, "prev_last": prev_last})


def undo_record() -> dict:
    rec = _json("place-back.json", {})
    return rec if isinstance(rec, dict) and rec.get("moves") else {}


def back(log=print, dry_run: bool = False) -> int:
    """Undo the saved record. On success the workspace's marker goes back to what it was
    before the sort/pull, so the next `back` reaches the layout undo underneath."""
    rec = undo_record()
    moves = rec.get("moves", [])
    if not moves:
        log("nothing to undo")
        return 1
    if dry_run:
        for e in reversed(moves):
            log(f"would move {e['pane']} back to {e.get('from_label') or e['from_tab']} "
                f"(workspace {e['from_ws']}) (dry run, nothing changed)")
        return 0
    rebuilt: dict[str, str] = {}              # a closed source tab -> the tab rebuilt for it
    with core.apply_lock():
        for e in reversed(moves):
            snap = herdr.snapshot()
            sp = next((p for p in snap["panes"] if p["pane_id"] == e["pane"]), None)
            if sp is None:
                log(f"skipped {e['pane']}: closed")
                continue
            if sp["tab_id"] != e["to_tab"]:
                log(f"skipped {e['pane']}: moved by hand since")
                continue
            try:
                home = rebuilt.get(e["from_tab"], e["from_tab"])
                if not any(t["tab_id"] == home for t in snap["tabs"]):
                    res = herdr.move(e["pane"], new_tab=True, workspace=e["from_ws"],
                                     label=e.get("from_label") or None, focus=False)
                    rebuilt[e["from_tab"]] = res["pane"]["tab_id"]
                else:
                    target = next(p["pane_id"] for p in snap["panes"] if p["tab_id"] == home)
                    res = herdr.move(e["pane"], tab=home, target=target, split="right", focus=False)
            except herdr.HerdrError as err:
                log(f"skipped {e['pane']}: {err}")
                continue
            new = res["pane"]["pane_id"]
            if new != e["pane"]:
                facts.rekey(e["pane"], new)
            _note_own(new)
    (core.state_dir() / "place-back.json").unlink(missing_ok=True)
    if rec.get("ws"):
        prev = rec.get("prev_last")
        core.set_last(rec["ws"], prev if prev in ("tab", "multi") else None)
    return 0
