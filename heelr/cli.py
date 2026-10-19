"""heelr — switchable, adjustable Herdr layouts."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from . import burst, core, facts, herdr, marks, multi, place
from . import sleep as sleep_mod
from . import key as keymod
from .assign import Mismatch, load_roles
from .layouts import ROLE_RULES, config_dir, discover, expand_rule, load, user_layout_dir
from .tree import Node, Split, first_leaf, from_herdr_layout, leaves, same_structure, shape


def _roles():
    return load_roles(config_dir() / "roles.toml")


def _read(args):
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    return core.read_tab(snap, args.tab, _roles())


def _report(res: core.Result, dry_run: bool) -> int:
    if res.noop:
        print("already in this layout; nothing to do")
        return 0
    if dry_run:
        print(f"{len(res.ops)} step(s) planned (dry run, nothing changed):")
        for op in res.ops:
            print("  " + op.describe())
        return 0
    print(f"applied in {res.done} step(s)")
    if res.new_panes:
        print(f"created {len(res.new_panes)} new pane(s): {', '.join(res.new_panes.values())}")
    if res.drift:
        print("layout differs from target after apply:", *res.drift, sep="\n  ")
        return 3
    return 0


def cmd_apply(args) -> int:
    with core.lock_unless(args.dry_run):  # held from the first read to the last state write
        return _cmd_apply(args)


def _cmd_apply(args) -> int:
    lay = load(args.name)
    if lay.kind == "multi":
        return _apply_multi(args, lambda ws: multi.assign_tabs(lay, ws))
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    ts = core.read_tab(snap, args.tab, _roles())
    core.refuse_if_parked(snap, ts.workspace_id)
    if lay.kind == "rule" and (r := core.tab_ratio(ts.tab_id, lay.name)) is not None:
        lay.main_ratio = r  # nudged on this tab
    try:
        target, overflow = core.target_for(lay, ts)
    except Mismatch as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if lay.kind == "rule" and lay.rule in ROLE_RULES:
        role = lay.main_role or ROLE_RULES[lay.rule]
        k = sum(p.role == role for p in ts.panes)
        if k == 0 or (k == len(ts.panes) and None not in [l.pane for l in leaves(target)]):
            what = f"all {k} panes are" if k else f"none of the {len(ts.panes)} panes is"
            print(f"note: {what} '{role}', so {lay.name} is a plain grid here")
    if overflow:
        print(f"{len(overflow)} pane(s) don't fit and go to the '{core.OVERFLOW_LABEL}' tab: {', '.join(overflow)}")
    res = core.run(target, overflow, ts, args.dry_run, command=lay.new_pane_command)
    if not args.dry_run:
        core.update_tab_record(ts.tab_id, layout=lay.name, panes=core.slot_order(core.placed(target, res)))
    return _report(res, args.dry_run)


def _read_ws(args, refuse_parked: bool = False):
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    ws = snap["focused_workspace_id"] if not args.tab else _ws_of(snap, args.tab)
    if refuse_parked:
        core.refuse_if_parked(snap, ws)
    return multi.read_workspace(snap, ws, _roles())


def _ws_of(snap: dict, tab: str) -> str:
    ws = next((t["workspace_id"] for t in snap["tabs"] if t["tab_id"] == tab), None)
    if ws is None:
        raise herdr.HerdrError(f"no tab {tab!r}")
    return ws


def _apply_multi(args, make_dests, back: bool = False) -> int:
    ws = _read_ws(args, refuse_parked=not back)  # back is what clears a park tab
    dests = make_dests(ws)
    ops = multi.plan_gather(ws, dests)
    if args.dry_run:
        print(f"{len(ops)} gather move(s) planned (dry run, nothing changed):")
        for op in ops:
            print("  " + multi.describe(op))
        for d in dests:
            what = (d.layout.name if hasattr(d.layout, "name") else "recorded layout") if d.layout else "left as is"
            print(f"  then tab '{d.label}' ({len(d.panes)} pane(s)): {what}")
        return 0
    prev = multi.save_ws_back(ws)
    prev_last = core.set_last(ws.ws_id, "multi")  # before anything can fail
    for t in ws.tabs:  # their single-tab layouts won't survive the gather
        core.forget_tab_layout(t.tab_id)
    with core.apply_lock():
        sym = multi.execute_gather(ops, ws.ws_id)
    print(f"gathered with {len(ops)} move(s)")
    created: list[str] = []
    problems, changed = multi.layout_tabs(dests, sym, _roles(), created=created)
    if created:
        multi.add_ws_created(ws.ws_id, created)
    if not ops and not changed:
        multi.restore_ws_back(ws.ws_id, prev)  # nothing moved: keep the older undo point
        core.set_last(ws.ws_id, prev_last)
        print("already in this layout; nothing to do")
        return 3 if problems else 0
    if ws.focused_tab:  # keep the user on the tab holding the pane they were in
        focused = next((p for p, i in ws.info.items() if i.focused), None)
        if focused:
            try:
                herdr.call("tab", "focus", herdr.call("pane", "get", focused)["pane"]["tab_id"])
            except herdr.HerdrError:
                pass
    return 3 if problems else 0


def cmd_back(args) -> int:
    with core.lock_unless(args.dry_run):
        try:
            return _cmd_back(args)
        except herdr.HerdrError as e:
            if _park_open_for(args) and core.PARK_HELP not in str(e):
                raise herdr.HerdrError(f"{e}\n{core.PARK_HELP}") from None
            raise


def _park_open_for(args) -> bool:
    """Is a heelr-park tab open in the workspace `back` acted on?"""
    try:
        snap = herdr.snapshot()
        return core.park_tab_open(snap, _ws_of(snap, args.tab or snap["focused_tab_id"]))
    except herdr.HerdrError:
        return False


def _ws_panes(snap: dict, ws_id: str) -> set[str]:
    """Panes `back` may close or reorder: only the undone tab's own workspace. A pane moved
    out by hand (its id recorded by the pane store) is no longer this layout's to undo."""
    return {p["pane_id"] for p in snap["panes"] if p["workspace_id"] == ws_id}


def _cmd_back(args) -> int:
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    tab = args.tab or snap["focused_tab_id"]
    ws_id = _ws_of(snap, tab)
    if core.get_last(ws_id) == "place":
        if place.undo_record().get("ws") == ws_id:
            return place.back(dry_run=args.dry_run)
        core.set_last(ws_id, None)       # a stale marker: never apply another workspace's record
    if get_last_kind(ws_id) == "multi":
        created = multi.ws_created(ws_id)
        multi.back_dests(_read_ws(args))  # refuses here, before anything is closed
        if created and not args.dry_run:
            live = _ws_panes(snap, ws_id)
            gone = core.close_created(created, live, False)
            if gone:
                print(f"closed pane(s) the layout created: {', '.join(gone)}")
        return _apply_multi(args, multi.back_dests, back=True)
    ts = core.read_tab(snap, tab, _roles())
    live = _ws_panes(snap, ws_id)
    close, still_used = core.sort_created(core.created_panes(tab), live)
    # created panes still in use weren't in the old layout: they go to overflow
    kept = [p for p in still_used if p in {x.pane_id for x in ts.panes}]
    out = set(close) | set(kept)
    target = core.load_back(core.without(ts, out))  # refuses here, before anything is closed
    anchor = first_leaf(target).pane
    if anchor not in {p.pane_id for p in ts.panes}:
        # the recorded first pane is elsewhere (parked by an apply that stopped,
        # or moved away): bring it back first; the planner keeps it in place
        if args.dry_run:
            print(f"would move {anchor} back into the tab"
                  + (f", close {', '.join(close)}" if close else "")
                  + (f", move {', '.join(kept)} to the '{core.OVERFLOW_LABEL}' tab" if kept else "")
                  + ", then restore the recorded layout (dry run, nothing changed)")
            return 0
        else:
            herdr.move(anchor, tab=ts.tab_id, target=ts.panes[0].pane_id, split="right", ratio=0.5)
            ts = core.read_tab(herdr.snapshot(), tab, _roles())
    if close:
        print(("would close" if args.dry_run else "closed") + f" pane(s) the layout created: {', '.join(close)}")
        if not args.dry_run:
            for p in close:
                herdr.call("pane", "close", p)
            ts = core.read_tab(herdr.snapshot(), tab, _roles())
    ts = core.without(ts, out)  # plan as if closed/kept panes were already out of the tab
    res = core.run(core.load_back(ts), kept, ts, args.dry_run)
    if not args.dry_run:
        core.forget_tab_layout(ts.tab_id)
    return _report(res, args.dry_run)


def cmd_report(args) -> int:
    pane = args.pane or os.environ.get("HERDR_PANE_ID")
    try:
        return _report_save(pane)
    except Exception:           # adapters call this on every prompt: never fail
        return 0


def _report_save(pane) -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if pane and isinstance(data, dict):
        flat = dict(data)
        ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
        wt = data.get("worktree") if isinstance(data.get("worktree"), dict) else {}
        flat.setdefault("cwd", ws.get("current_dir"))          # Claude status line shape
        if wt:
            flat["worktree"] = wt.get("name")
            flat.setdefault("branch", wt.get("branch"))
        if facts.save_report(pane, flat):      # adapters report on every prompt: refresh on news only
            marks.refresh()
    return 0


SHELL_HOOKS = {
    # JSON is built by hand, so " and \ in the folder name are escaped (other control characters
    # in a folder name are not handled: heelr report drops invalid JSON silently).
    "bash": r"""__heelr_hook() {
  local rc=$?   # keep the last command's status for a PS1 that shows it
  [ -n "${HERDR_PANE_ID:-}" ] || return $rc
  [ "$PWD" = "${__heelr_last:-}" ] && return $rc
  __heelr_last=$PWD
  local p=${PWD//\\/\\\\}
  p=${p//\"/\\\"}
  (printf '{"cwd":"%s"}' "$p" | heelr report --pane "$HERDR_PANE_ID" >/dev/null 2>&1 &)
  return $rc
}
if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
  # bash >= 5.1 array: add one element, leave the others alone
  case " ${PROMPT_COMMAND[*]} " in *" __heelr_hook "*) ;; *) PROMPT_COMMAND+=(__heelr_hook);; esac
else
  # joined with a newline, so a value ending in ";", "&", a newline or a comment still works
  case $'\n'"${PROMPT_COMMAND:-}"$'\n' in *$'\n__heelr_hook\n'*) ;; *) PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND$'\n'}__heelr_hook";; esac
fi
""",
    "zsh": r"""__heelr_hook() {
  [[ -n "${HERDR_PANE_ID:-}" ]] || return 0
  [[ "$PWD" == "${__heelr_last:-}" ]] && return 0
  __heelr_last=$PWD
  local p=${PWD//\\/\\\\}
  p=${p//\"/\\\"}
  (printf '{"cwd":"%s"}' "$p" | heelr report --pane "$HERDR_PANE_ID" >/dev/null 2>&1 &)
}
autoload -Uz add-zsh-hook && add-zsh-hook chpwd __heelr_hook
__heelr_hook   # chpwd does not fire at startup: report the starting folder
""",
    "fish": r"""function __heelr_report
  set -q HERDR_PANE_ID; or return 0
  set -l p (string replace -a '\\' '\\\\' -- $PWD | string replace -a '"' '\\"')
  printf '{"cwd":"%s"}' $p | heelr report --pane "$HERDR_PANE_ID" >/dev/null 2>&1 &
end
function __heelr_hook --on-variable PWD
  __heelr_report
end
__heelr_report   # the event does not fire at startup: report the starting folder
""",
}


def cmd_shell_hook(args) -> int:
    print("# heelr: tell heelr when this pane's folder changes.")
    if args.shell == "bash":
        print("# Add AFTER any line that sets PROMPT_COMMAND.")
    elif args.shell == "fish":
        print("# usage: heelr shell-hook fish | source")
    print(SHELL_HOOKS[args.shell], end="")
    return 0


def cmd_tag(args) -> int:
    pane = args.pane or os.environ.get("HERDR_PANE_ID")
    if not pane:
        raise herdr.HerdrError("no pane: pass --pane or run inside Herdr")
    if not args.clear and not args.task:
        raise herdr.HerdrError("no task: heelr tag TASK, or heelr tag --clear")
    facts.set_tag(pane, None if args.clear else args.task)
    return 0


def _scope_panes(snap: dict, scope: str) -> set[str]:
    if scope == "all":
        return {p["pane_id"] for p in snap["panes"]}
    if scope == "workspace":
        return {p["pane_id"] for p in snap["panes"] if p["workspace_id"] == snap["focused_workspace_id"]}
    return {p["pane_id"] for p in snap["panes"] if p["tab_id"] == snap["focused_tab_id"]}


def _run_moves(snap, moves, cfg, keys, dry_run, exact=False, verb="sort") -> int:
    """Run a user-requested placement. The undo record is saved (and `back` pointed at
    it) even when execute stopped early: the partial list is what happened."""
    if dry_run:
        for m in moves:
            print(f"would move {m.pane} -> {m.dest.workspace} / {m.dest.tab}")
        return 0
    if not moves:
        print(f"nothing to {verb}")
        return 0
    undo = place.execute(snap, moves, cfg, key_of=keys, exact=exact)
    if undo:
        ws = snap["focused_workspace_id"]
        prev = core.set_last(ws, "place")
        place.save_undo(undo, ws=ws, prev_last=prev)
    print(f"moved {len(undo)} pane(s)")
    return 0


def cmd_sort(args) -> int:
    cfg = keymod.load_config()
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    scope = "all" if args.all else "workspace" if args.workspace else "tab"
    roles = _roles()
    panes = _scope_panes(snap, scope) - {snap.get("focused_pane_id")}
    todo = place.drifted(snap, cfg, roles, panes)
    keys = {sp["pane_id"]: keymod.pane_key(sp, cfg, roles) for sp in snap["panes"] if sp["pane_id"] in todo}
    _report_classify_failures()
    return _run_moves(snap, [place.Move(p, d, False) for p, d in todo.items()], cfg, keys, args.dry_run)


def cmd_pull(args) -> int:
    cfg = keymod.load_config()
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    roles = _roles()
    ws_label = {w["workspace_id"]: w.get("label") or "" for w in snap.get("workspaces", [])}
    here = ws_label.get(snap["focused_workspace_id"], "")
    dest = place.Dest(here, args.task, snap["focused_workspace_id"])
    tab_of = {t["tab_id"]: t for t in snap["tabs"]}
    skip = sleep_mod.sleeping_panes(snap) | {snap.get("focused_pane_id")}
    keys, moves = {}, []
    for sp in snap["panes"]:
        if sp["pane_id"] in skip or core.tab_record(sp["tab_id"]).get("auto") is False:
            continue
        k = keymod.pane_key(sp, cfg, roles)
        tab = tab_of[sp["tab_id"]]
        if k.task == args.task and not (sp["workspace_id"] == snap["focused_workspace_id"]
                                        and core.base_label(tab) == args.task):
            keys[sp["pane_id"]] = k
            moves.append(place.Move(sp["pane_id"], dest, False))
    _report_classify_failures()
    return _run_moves(snap, moves, cfg, keys, args.dry_run, exact=True, verb="pull")


def cmd_drift_mark(args) -> int:
    """Refresh the tab marks now (pane events and placement commands do it on their own). Never fails."""
    return 0                 # main() refreshes after every command marked refresh_marks


def _tell(msg: str) -> None:
    """A result worth seeing even from a background keybinding (where stderr isn't a tty)."""
    print(msg)
    if not sys.stderr.isatty():
        try:
            herdr.call("notification", "show", "heelr", "--body", msg[:300])
        except herdr.HerdrError:
            pass


def _read_for_change(args):
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    ts = core.read_tab(snap, args.tab, _roles())
    core.refuse_if_parked(snap, ts.workspace_id)
    return snap, ts


def cmd_promote(args) -> int:
    """Swap the focused pane with the tab's largest one."""
    with core.apply_lock():
        snap, ts = _read_for_change(args)
        lay = next(l for l in snap["layouts"] if l["tab_id"] == ts.tab_id)
        main, focused = core.largest_pane(lay), ts.focused_pane
        if focused == main:
            _tell(f"{focused} is already main")
            return 0
        core.save_back(ts)
        core.set_last(ts.workspace_id, "tab")  # before anything can fail: back must undo this
        herdr.call("pane", "swap", "--source-pane", focused, "--target-pane", main)
        order = core.tab_record(ts.tab_id).get("panes")
        if order:  # keep auto-tile's order in step; a tab with no record stays without one
            swap = {focused: main, main: focused}
            core.update_tab_record(ts.tab_id, panes=[swap.get(p, p) for p in order])
        print(f"promoted {focused} (swapped with {main})")
        return 0


NUDGE_STEP, NUDGE_MIN, NUDGE_MAX = 0.1, 0.2, 0.8
NO_MAIN_AREA = ("grid", "even-columns", "even-rows")


def cmd_nudge(args) -> int:
    """Grow or shrink the main area of the tab's layout, and remember it for the tab."""
    with core.apply_lock():
        snap, ts = _read_for_change(args)
        rec = core.tab_record(ts.tab_id)
        name = rec.get("layout")
        if not name:
            raise herdr.HerdrError("no heelr layout recorded for this tab; apply a layout first (prefix+m)")
        lay = load(name)
        if lay.kind != "rule" or lay.rule in NO_MAIN_AREA:
            raise herdr.HerdrError(f"{name} has no main area to resize")
        cur = core.tab_ratio(ts.tab_id, name) or lay.main_ratio
        step = NUDGE_STEP if args.step == "+" else -NUDGE_STEP
        new = round(min(NUDGE_MAX, max(NUDGE_MIN, cur + step)), 2)
        if abs(new - cur) < 1e-9:
            _tell(f"{name}: main area already at the limit ({cur:.0%})")
            return 0
        lay.main_ratio = new
        target, overflow = core.target_for(lay, ts)
        ratios = {**rec.get("ratios", {}), name: new}
        if overflow or same_structure(target, ts.tree, tol=1.0, check_panes=False):
            # the tab no longer has the layout's shape: apply it again, with the new size
            res = core.run(target, overflow, ts, False, command=lay.new_pane_command)
            core.update_tab_record(ts.tab_id, layout=name, ratios=ratios,
                                   panes=core.slot_order(core.placed(target, res)))
            print(f"{name}: main area {cur:.0%} -> {new:.0%} (applied again: the tab had changed shape)")
            return _report(res, False)
        for pane, d, amount in core.resize_steps(ts.tree, target):  # no pane moves
            herdr.call("pane", "resize", "--pane", pane, "--direction", d, "--amount", f"{amount:.4f}")
        core.update_tab_record(ts.tab_id, ratios=ratios)
        print(f"{name}: main area {cur:.0%} -> {new:.0%}")
        actual = from_herdr_layout(herdr.layout(ts.panes[0].pane_id))
        drift = same_structure(target, actual, tol=0.05, check_panes=False)
        if drift:
            print("layout differs from target after resize:", *drift, sep="\n  ")
            return 3
        return 0


AUTO_WAIT = 5.0          # seconds `auto` waits for another heelr change to finish
AUTO_PASSES = 3          # re-checks, so a burst of new panes settles
AUTO_LOG_MAX = 64 * 1024


def auto_tile_enabled() -> bool:
    import tomllib
    p = config_dir() / "config.toml"
    try:
        with open(p, "rb") as f:
            return tomllib.load(f).get("auto_tile") is True
    except Exception:
        return False


def _auto_log(line: str, once: bool = False, name: str = "auto.log") -> bool:
    """One line in state/auto.log, kept under AUTO_LOG_MAX (the newer half
    survives). `once`: skip it if it's already the last line. True if written."""
    try:
        p = core.state_dir() / name
        old = p.read_text() if p.exists() else ""
        if once and old.rstrip("\n").endswith(line):
            return False
        if len(old) > AUTO_LOG_MAX // 2:
            old = old[-(AUTO_LOG_MAX // 2):].split("\n", 1)[-1]
        core.write_atomic(p, old + f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")
        return True
    except OSError:
        return False


_auto_failed = False     # an error was logged during this `heelr auto` run


def _auto_error(e: BaseException) -> None:
    """Log an error; notify only when it isn't the same as the last one logged,
    so a lasting problem doesn't notify on every pane event."""
    global _auto_failed
    _auto_failed = True
    text = error_text(e) if isinstance(e, (*ERRORS, OSError)) else f"{type(e).__name__}: {e}"
    if _auto_log(f"error {text}", once=True):
        try:
            herdr.call("notification", "show", "heelr", "--body", f"auto-tile: {text}"[:300])
        except Exception:
            pass


def _placement_error(e: BaseException) -> None:
    """Placement's own error record (placement.log), so a clean auto-tile pass can't
    reset its dedupe: the same lasting error notifies once, until a clean placement."""
    text = error_text(e) if isinstance(e, (*ERRORS, OSError)) else f"{type(e).__name__}: {e}"
    if _auto_log(f"error {text}", once=True, name="placement.log"):
        try:
            herdr.call("notification", "show", "heelr", "--body", f"placement: {text}"[:300])
        except Exception:
            pass


CLASSIFY_NOTIFY_EVERY = 60.0     # seconds between "classify is failing" notifications


def _report_classify_failures() -> None:
    """Log what went wrong running the classify script, and notify at most once a minute.
    Called by the hook, sort and pull; never by a drift-mark refresh, which stays silent."""
    try:
        failures = keymod.take_failures()
        if not failures:
            return
        for f in failures:
            _auto_log(f, once=True, name="placement.log")
        stamp, now = core.state_dir() / "classify-notified", time.time()
        try:
            last = float(stamp.read_text())
        except (OSError, ValueError):
            last = 0.0
        if now - last < CLASSIFY_NOTIFY_EVERY:
            return
        core.write_atomic(stamp, str(now))
        herdr.call("notification", "show", "heelr", "--body", f"placement: {failures[0]}"[:300])
    except Exception:
        pass                         # reporting must never break the command


def _placement_ok() -> None:
    try:
        last = (core.state_dir() / "placement.log").read_text().splitlines()[-1]
    except (OSError, IndexError):
        return
    if " error " in last:
        _auto_log("ok: placement working again", name="placement.log")


def _auto_last_line() -> str:
    """auto.log's last line without its timestamp ("" if none)."""
    try:
        lines = (core.state_dir() / "auto.log").read_text().splitlines()
    except OSError:
        return ""
    return lines[-1].split(" ", 1)[-1] if lines else ""


def bind_order(tree: Node, order: list[str]) -> Node:
    """Bind panes to a rule layout in slot order: main slot(s) first, then the rest."""
    import copy
    t = copy.deepcopy(tree)
    ls = list(leaves(t))
    for leaf, pane in zip([l for l in ls if l.main] + [l for l in ls if not l.main], order):
        leaf.pane = pane
    return t


def _event_pane() -> str | None:
    """The pane a pane.created hook is about; None for any other event or a bad payload."""
    if os.environ.get("HERDR_PLUGIN_EVENT") != "pane.created":
        return None
    try:
        pane = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")["data"]["pane"]["pane_id"]
    except (ValueError, KeyError, TypeError):
        return None
    return pane if isinstance(pane, str) and pane else None


def _event_data() -> dict:
    try:
        d = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")["data"]
    except (ValueError, KeyError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _sid(v) -> str | None:
    return v if isinstance(v, str) and v else None


def _track_pane_events() -> None:
    """Keep the pane store in step with manual changes: a pane moved by hand, or closed.
    Under the lock, since rekey rewrites every state file. A malformed payload is ignored."""
    ev, d = os.environ.get("HERDR_PLUGIN_EVENT"), _event_data()
    pane = d.get("pane")
    new = _sid(pane.get("pane_id")) if isinstance(pane, dict) else None
    if ev == "pane.moved":
        old = _sid(d.get("previous_pane_id"))
        todo = (lambda: facts.rekey(old, new)) if old and new and old != new else None
    elif ev == "pane.closed":
        gone = new or _sid(d.get("pane_id"))
        todo = (lambda: facts.forget(gone)) if gone else None
    elif ev == "pane.created":
        todo = (lambda: facts.drop_alias(new)) if new else None     # a new pane took an old id
    else:
        todo = None
    if todo is None:
        return
    try:
        with core.apply_lock(wait=AUTO_WAIT, layout=False):
            todo()
    except (herdr.HerdrError, OSError):
        pass                     # busy or unwritable: the store catches up on the next event


def _place_new_pane(pane: str, cfg, allow_untested: bool = False) -> None:
    """Move a just-created pane (or its whole burst) to its repo/task tab. Background
    placement: never writes the undo record, never calls set_last."""
    if pane in place.own_panes():
        return
    burst.note(pane, time.time())
    time.sleep(cfg.burst_window)                     # outside any lock
    verdict = burst.decide(pane, cfg.burst_window, time.time())
    if verdict == "burst-wait":
        return
    panes = [pane] if verdict == "single" else burst.burst_panes(pane)
    roles = _roles()
    pre = herdr.snapshot()
    herdr.check_version(pre, allow_untested)          # same gate as every other command
    lines: list[str] = []
    keys = {sp["pane_id"]: keymod.pane_key(sp, cfg, roles) for sp in pre["panes"] if sp["pane_id"] in panes}
    # The lock is reached within burst_window (max 5 s) + AUTO_WAIT (5 s) + key work of the event, under place.OWN_TTL (30 s);
    # creators note their panes while holding this lock, so this re-check cannot miss one.
    try:
        with core.apply_lock(wait=AUTO_WAIT):
            panes = [p for p in panes if p not in place.own_panes()]
            if not panes:
                return
            snap = herdr.snapshot()
            todo = place.drifted(snap, cfg, roles, set(panes))
            if verdict != "single":      # the focused pane never moves on its own
                todo.pop(snap.get("focused_pane_id"), None)
            if todo:
                # focus follows only a pane you just made and are on, not a background split
                moves = [place.Move(p, d, focus=(verdict == "single" and snap.get("focused_pane_id") == p))
                         for p, d in todo.items()]
                place.execute(snap, moves, cfg, log=lines.append, key_of=keys)
    except core.LockBusy as e:
        _auto_log(f"skipped {pane}: {e}; it is placed on the next event or `heelr sort`", name="placement.log")
        return
    stopped = [l for l in lines if l.startswith("stopped after")]
    for l in lines:
        if l not in stopped:
            _auto_log(l, name="placement.log")          # skipped: logged, no notification
    if stopped:
        raise herdr.HerdrError(stopped[0])              # cmd_auto: logged and notified once


def cmd_auto(args) -> int:
    """Keep laid-out tabs in their layout as panes open and close (a plugin hook).
    Never fails the hook: problems go to auto.log."""
    global _auto_failed
    try:
        cfg = keymod.load_config()
    except Exception:                # the hook never fails: an unreadable config is placement off
        cfg = keymod.PlacementConfig()
    if cfg.enabled:                  # placement off: the hook is exactly auto-tile
        _track_pane_events()
    pane = _event_pane()
    if pane:
        if cfg.enabled:
            try:
                _place_new_pane(pane, cfg, args.allow_untested)
                _placement_ok()
            except Exception as e:  # never fail the hook
                _placement_error(e)
            finally:
                _report_classify_failures()
    if not auto_tile_enabled():
        return 0
    _auto_failed = locked = False
    try:
        with core.apply_lock(wait=AUTO_WAIT):
            locked = True
            for _ in range(AUTO_PASSES):
                if not _auto_pass(args):
                    break
        if not _auto_failed and _auto_last_line().startswith("error "):
            # a clean run after an error: the same error later is news again
            _auto_log("ok: auto-tile working again")
    except Exception as e:  # never fail the hook
        if not locked and isinstance(e, herdr.HerdrError):
            _auto_log(f"busy: another heelr change ran for over {AUTO_WAIT:g}s; left as is")
        else:
            _auto_error(e)
    return 0


def cmd_auto_toggle(args) -> int:
    """Switch auto-tile off (or back on) for one tab. Kept until toggled again."""
    with core.apply_lock(wait=AUTO_WAIT):  # auto read-modify-writes the same record
        tab = args.tab or herdr.snapshot()["focused_tab_id"]
        on = core.tab_record(tab).get("auto") is False
        core.update_tab_record(tab, auto=on)
    msg = f"auto-tile {'on' if on else 'off'} for this tab"
    if not auto_tile_enabled():
        msg += " (but auto_tile is off in the heelr config)"
    _tell(msg)
    return 0


def _auto_pass(args) -> bool:
    """Lay out every tab whose panes differ from its recorded order. True if any did.
    One tab's failure is logged and doesn't stop the others."""
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    did = False
    for tab in snap["tabs"]:
        try:
            did = _auto_tab(snap, tab) or did
        except Exception as e:
            _auto_error(e)
    return did


def _sleep_tab(args) -> str:
    return args.tab or herdr.snapshot()["focused_tab_id"]


def cmd_sleep(args) -> int:
    """End the tab's idle, named Claude agents to free their memory (all or none)."""
    from . import sleep
    ok, msg = sleep.do_sleep(_sleep_tab(args))
    _tell(msg)
    return 0 if ok else 1


def cmd_wake(args) -> int:
    """Resume the agents `heelr sleep` ended, in the same panes."""
    from . import sleep
    ok, msg = sleep.do_wake(_sleep_tab(args))
    _tell(msg)
    return 0 if ok else 1


def cmd_sleep_toggle(args) -> int:
    """Wake the tab if it has slept agents, else sleep it."""
    from . import sleep
    tab = _sleep_tab(args)
    ok, msg = (sleep.do_wake if sleep.asleep(tab) else sleep.do_sleep)(tab)
    _tell(msg)
    return 0 if ok else 1


def _auto_tab(snap: dict, tab: dict) -> bool:
    """Lay out one tab if its panes changed. True if it did."""
    tid = tab["tab_id"]
    rec = core.tab_record(tid)
    name, order = rec.get("layout"), rec.get("panes")
    if not name or not order or rec.get("auto") is False:
        return False
    here = [p["pane_id"] for p in snap["panes"] if p["tab_id"] == tid]
    if set(here) == set(order):
        return False  # nothing opened or closed (moves within the tab don't count)
    if not set(here) & set(order):
        return False  # none of its panes left: a reused tab id, not the tab heelr laid out
    try:
        lay = load(name)
    except (*ERRORS, OSError):
        return False
    if lay.kind != "rule":
        return False
    if core.park_tab_open(snap, tab["workspace_id"]):
        _auto_log(f"paused: a '{core.PARK_LABEL}' tab is open in {tab['workspace_id']}; "
                  f"run `heelr back` there", once=True)
        return False
    ts = core.read_tab(snap, tid, _roles())
    cur = [p.pane_id for p in ts.panes]
    new_order = [p for p in order if p in cur] + [p for p in cur if p not in order]
    lay.main_ratio = core.tab_ratio(tid, name) or lay.main_ratio
    if lay.rule in ROLE_RULES:  # role bands decide where panes go, as apply does
        lay.add_missing = False
        target, _ = core.target_for(lay, ts)
    else:  # exactly the panes there are: never creates one
        target = bind_order(expand_rule(lay.rule, len(new_order), lay.main_ratio), new_order)
    res = core.run(target, [], ts, False, log=lambda *a: None, last=None, undo=False)
    core.update_tab_record(tid, panes=core.slot_order(core.placed(target, res)))
    if res.drift:
        _auto_log(f"{tid}: layout differs from target: {'; '.join(res.drift)}")
    return True


def get_last_kind(ws_id: str) -> str:
    return core.get_last(ws_id)


def cmd_save(args) -> int:
    try:
        core.check_layout_name(args.name)
    except ValueError as e:
        print(f"heelr: {e}", file=sys.stderr)
        return 2
    out = user_layout_dir() / f"{args.name}.toml"
    if out.exists() and not args.force:
        print(f"{out} exists; pass --force to overwrite", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.tabs:
        ws = _read_ws(args)
        out.write_text(multi.capture(ws, args.name))
        print(f"saved {len(ws.tabs)}-tab layout to {out}")
        return 0
    ts = _read(args)
    out.write_text(core.to_toml(args.name, core.capture(ts)))
    print(f"saved {len(ts.panes)}-pane layout to {out}")
    return 0


def cmd_list(args) -> int:
    for name, path in sorted(discover().items()):
        if args.names:
            print(name)
            continue
        try:
            lay = load(name)
            desc = (f"rule {lay.rule}" if lay.kind == "rule"
                    else f"multi, {len(lay.tabs)} tabs" if lay.kind == "multi"
                    else f"fixed, {sum(1 for _ in leaves(lay.root))} panes")
        except Exception as e:  # show broken files rather than hiding them
            desc = f"INVALID: {e}"
        print(f"{name:<20} {desc:<24} {path}")
    return 0


def _draw(n: Node, w: int = 48, h: int = 14) -> list[str]:
    grid = [[" "] * w for _ in range(h)]
    labels = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    def box(x, y, bw, bh, node):
        if isinstance(node, Split):
            if node.dir == "right":
                aw = max(1, round(bw * node.ratio))
                box(x, y, aw, bh, node.a)
                box(x + aw, y, bw - aw, bh, node.b)
            else:
                ah = max(1, round(bh * node.ratio))
                box(x, y, bw, ah, node.a)
                box(x, y + ah, bw, bh - ah, node.b)
            return
        for i in range(x, x + bw):
            grid[y][i] = grid[y + bh - 1][i] = "-"
        for j in range(y, y + bh):
            grid[j][x] = grid[j][x + bw - 1] = "|"
        tag = next(labels) + ("*" if node.main else "") + (f" {node.role}" if node.role else "")
        if node.pane:
            tag += f" {node.pane}"
        for k, c in enumerate(tag[: max(0, bw - 2)]):
            grid[y + max(1, bh // 2) if bh > 2 else y][x + 1 + k] = c

    box(0, 0, w, h, n)
    return ["".join(r) for r in grid]


def cmd_show(args) -> int:
    lay = load(args.name)
    if lay.kind == "multi":
        print(f"{lay.name} (multi) — unclaimed panes go to tab '{lay.park_tab}'")
        for t in lay.tabs:
            sub = t.layout if isinstance(t.layout, str) else (t.layout.rule or "inline fixed") if t.layout else "as is"
            print(f"  tab '{t.label}': gather {t.gather}" + (f" max {t.max}" if t.max else "") + f", layout {sub}")
        return 0
    tree = lay.root if lay.kind == "fixed" else expand_rule(lay.rule, args.n, lay.main_ratio)
    print(f"{lay.name} ({lay.kind}{', ' + lay.rule if lay.kind == 'rule' else ''}) — {shape(tree)}")
    print("\n".join(_draw(tree)))
    return 0


DEFAULT_FAVOURITES = ["main-left", "grid", "main-top", "even-columns", "even-rows"]  # listed first


def favourites() -> list[str]:
    import tomllib
    p = config_dir() / "config.toml"
    if p.exists():
        with open(p, "rb") as f:
            return list(tomllib.load(f).get("favourites", DEFAULT_FAVOURITES))
    return DEFAULT_FAVOURITES


def _getkey() -> str:
    import termios
    import tty
    if not sys.stdin.isatty():
        return (sys.stdin.readline().strip() or "q")[0]
    import select
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        raw = os.read(fd, 1)
        while select.select([fd], [], [], 0.05)[0]:  # rest of an escape sequence
            raw += os.read(fd, 64)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return _decode_key(raw.decode(errors="replace"))


def _decode_key(seq: str) -> str:
    """One key from raw input. Understands the kitty keyboard protocol
    (CSI <code>[;mods]u), which some hosts send even for plain digits, and the
    arrow keys (returned as "LEFT" / "RIGHT")."""
    m = re.fullmatch(r"\x1b\[(\d+)(?:[:;][\d:;]*)?u", seq)
    if m:
        return chr(int(m.group(1)))
    m = re.fullmatch(r"\x1b(?:\[(?:1;[\d:]+)?|O)([CD])", seq)
    if m:
        return "RIGHT" if m.group(1) == "C" else "LEFT"
    if seq.startswith("\x1b") and len(seq) > 1:
        return ""          # some other special key (up, home, ...): ignored, only a bare Esc cancels
    return seq[:1] if seq else "q"


# Layout keys in the menu: never b (back), q (cancel) or h / l (move).
MENU_KEYS = "123456789acdefgijkmnoprstuvwxyz"
_CARDS = ((17, 9), (13, 7), (9, 5))   # thumbnail sizes, largest that fits first
LABEL_MAX = 20                          # longer card labels wrap onto a second line


def _wrap(text: str, width: int) -> list[str]:
    """`text` in at most two lines of `width`, broken after a hyphen or at a
    space where possible; anything left over ends in an ellipsis."""
    lines, rest = [], text
    while rest and len(lines) < 2:
        if len(rest) <= width:
            lines.append(rest)
            rest = ""
            break
        cut = max(rest.rfind("-", 0, width) + 1, rest.rfind(" ", 2, width + 1))  # never right after the key
        if cut <= 0:
            cut = width
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        lines[-1] = lines[-1][: width - 1].rstrip("- ") + "…"
    return lines


def _carousel(order: list[str], keys: str, favs: set[str], sel: int, cols: int, rows: int,
              tty: bool = False) -> list[str]:
    """The menu as lines: one row of layout cards scrolled to keep `sel` in view, the
    selected one underlined (and reversed on a tty), its full name top right. Falls
    back to a plain list with a > marker when not even 2 cards fit."""
    from .thumb import for_layout
    rev = (lambda t: f"\033[7m{t}\033[0m") if tty else (lambda t: t)
    title = "heelr — pick a layout for this tab"
    head = title + order[sel].rjust(max(1, cols - len(title) - 1))
    help_ = "  ←/→ move  Enter apply  key: apply at once  b back  q cancel"
    star = lambda n: " ★" if n in favs else ""
    labels = [_wrap(f"{keys[i] if i < len(keys) else ' '} {order[i]}{star(order[i])}", LABEL_MAX)
              for i in range(len(order))]   # past the last key: reached with the arrows
    widest = max(len(l) for ls in labels for l in ls)
    depth = max(len(ls) for ls in labels)   # label lines under every card, so the height never changes
    for tw, th in _CARDS:
        card = max(tw, widest) + 3   # every label whole, and well apart from the next
        per_view = min(len(order), (cols - 4) // card)
        if per_view >= 2 and th + 5 + depth <= rows:
            break
    else:
        per_view = 0
    thumbs = None
    if per_view:
        start = min(max(0, sel - per_view // 2), len(order) - per_view)
        shown = range(start, start + per_view)
        try:
            thumbs = {i: for_layout(load(order[i]), tw, th) for i in shown}
        except Exception:  # a broken user file shouldn't hide the menu
            thumbs = None
    if thumbs is None:
        lines = [head, ""] + [f"{'>' if i == sel else ' '} {k}  {n}{star(n)}"
                              for i, (k, n) in enumerate(zip(keys.ljust(len(order)), order))]
        return lines + ["", help_]
    left = "◀ " if start > 0 else "  "
    right = " ▶" if start + per_view < len(order) else ""
    out = [head, ""]
    for r in range(th):
        cells = [(rev if i == sel else str)(thumbs[i][r]) + " " * (card - tw) for i in shown]
        out.append((left if r == th // 2 else "  ") + "".join(cells).rstrip()
                   + (right if r == th // 2 else ""))
    for r in range(depth):
        out.append("  " + "".join((labels[i][r] if r < len(labels[i]) else "").ljust(card)
                                  for i in shown).rstrip())
    out.append("  " + "".join(("━" * tw if i == sel else " " * tw) + " " * (card - tw)
                              for i in shown).rstrip())
    return out + [help_]


def cmd_pick(args) -> int:
    """Layout menu for a Herdr popup keybinding: arrows and Enter, or a layout's key."""
    import shutil
    names = sorted(discover())
    favs = [n for n in favourites() if n in names]
    order = favs + [n for n in names if n not in favs]
    keys = MENU_KEYS[: len(order)]
    last = core.tab_record(args.tab).get("layout") if args.tab else None
    sel = order.index(last) if last in order else 0
    tty = sys.stdout.isatty()
    while True:
        cols, rows = shutil.get_terminal_size((96, 16))
        if tty:
            print("\033[H\033[2J", end="")
        print("\n".join(_carousel(order, keys, set(favs), sel, cols, rows, tty)), flush=True)
        k = _getkey()
        if k in ("q", "\x1b", "\x03"):
            _pick_log(args.tab, f"key {k!r} cancel")
            return 0
        if k in ("RIGHT", "l", "LEFT", "h"):
            sel = (sel + (1 if k in ("RIGHT", "l") else -1)) % len(order)
            continue
        if k == "b":
            args.dry_run = False
            what, fn = "back", cmd_back
        elif k in ("\r", "\n") or (k and k in keys):
            args.name, args.dry_run = order[sel if k in ("\r", "\n") else keys.index(k)], False
            what, fn = args.name, cmd_apply
        else:
            continue  # not a menu key: keep waiting
        break
    print()
    import contextlib
    import io
    buf = io.StringIO()
    err = None
    with contextlib.redirect_stdout(buf):
        try:
            rc = fn(args)
        except (*ERRORS, OSError) as e:  # shown in the menu, which then waits for a key
            err = error_text(e)
            print(f"heelr: {err}")
            rc = 1
    print(buf.getvalue(), end="")
    detail = f"error {err[:300]!r}" if err else repr(buf.getvalue().strip()[-120:])
    _pick_log(args.tab, f"key {k!r} -> {what} rc={rc} {detail}")
    out = buf.getvalue()
    if rc != 0 or ("nothing to do" in out and "closed pane" not in out):  # nothing visible happened
        print("\npress any key")
        _getkey()
    return rc


def _pick_log(tab: str | None, line: str) -> None:
    try:
        d = core.state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "pick.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} tab={tab} {line}\n")
    except OSError:
        pass


def cmd_current(args) -> int:
    ts = _read(args)
    print(f"tab {ts.tab_id} — {shape(ts.tree)}")
    roles = {p.pane_id: p.role for p in ts.panes}
    t = core.copy_tree(ts.tree)
    for l in leaves(t):
        l.role = roles.get(l.pane)
    print("\n".join(_draw(t)))
    return 0


def cmd_selftest(args) -> int:
    """Live tests against a throwaway server; never the live one."""
    from . import selftest
    try:
        return selftest.run(selftest.resolve_bin(args.bin), record=args.record)
    except selftest.SelftestError as e:  # reported here: main() would notify the live server
        print(f"heelr selftest: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("heelr selftest: interrupted; throwaway server stopped and removed", file=sys.stderr)
        return 130


def cmd_check_update(args) -> int:
    """Read-only: herdr.dev/latest.json vs the installed binary vs the running server."""
    from . import selftest, update
    try:
        latest = update.fetch_latest()
        binary = selftest.resolve_bin(args.bin)
        installed = selftest.binary_version(binary)
        srv = update.server_info(binary) or {}  # read-only
        rc, lines = update.status(latest, installed, srv.get("version"), srv.get("protocol"),
                                  srv.get("restart_needed", False))
    except (update.UpdateError, selftest.SelftestError) as e:
        print(f"heelr check-update: {e}", file=sys.stderr)
        return update.ERROR
    print("\n".join(lines))
    return rc


ERRORS = (herdr.HerdrError, KeyError, ValueError)


def error_text(e: BaseException) -> str:
    return str(e.args[0] if isinstance(e, KeyError) and e.args else e)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="heelr", description=__doc__)
    from . import __version__
    ap.add_argument("--version", action="version", version=f"heelr {__version__}")
    ap.add_argument("--allow-untested", action="store_true", help="run on an untested Herdr version")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def tab_opts(p):
        p.add_argument("--tab", default=os.environ.get("HERDR_ACTIVE_TAB_ID"),
                       help="tab id (default: $HERDR_ACTIVE_TAB_ID, else the focused tab)")
        return p

    p = tab_opts(sub.add_parser("apply", help="rearrange the tab into a layout"))
    p.add_argument("name")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.set_defaults(fn=cmd_apply)
    p = tab_opts(sub.add_parser("back", help="restore the layout from before the last apply"))
    p.add_argument("-n", "--dry-run", action="store_true")
    p.set_defaults(fn=cmd_back, refresh_marks=True)
    p = tab_opts(sub.add_parser("save", help="capture the tab as an editable fixed layout"))
    p.add_argument("name")
    p.add_argument("--force", action="store_true")
    p.add_argument("--tabs", action="store_true", help="capture the whole workspace as a multi-tab layout")
    p.set_defaults(fn=cmd_save)
    p = sub.add_parser("list", help="list layouts")
    p.add_argument("--names", action="store_true", help="names only (for pickers)")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show", help="draw a layout")
    p.add_argument("name")
    p.add_argument("-n", type=int, default=4, help="pane count for rule layouts")
    p.set_defaults(fn=cmd_show)
    p = tab_opts(sub.add_parser("promote", help="swap the focused pane with the largest one"))
    p.set_defaults(fn=cmd_promote)
    p = tab_opts(sub.add_parser("nudge", help="grow (+) or shrink (-) the main area by 10%%"))
    p.add_argument("step", choices=["+", "-"])
    p.set_defaults(fn=cmd_nudge)
    p = sub.add_parser("auto", help="keep laid-out tabs in their layout (plugin hook; needs auto_tile = true)")
    p.set_defaults(fn=cmd_auto, refresh_marks=True)
    p = tab_opts(sub.add_parser("auto-toggle", help="switch auto-tile off / on for the tab"))
    p.set_defaults(fn=cmd_auto_toggle)
    p = tab_opts(sub.add_parser("sleep", help="end the tab's idle, named Claude agents (frees memory)"))
    p.set_defaults(fn=cmd_sleep)
    p = tab_opts(sub.add_parser("wake", help="resume the agents `sleep` ended, in the same panes"))
    p.set_defaults(fn=cmd_wake)
    p = tab_opts(sub.add_parser("sleep-toggle", help="wake the tab if it has slept agents, else sleep it"))
    p.set_defaults(fn=cmd_sleep_toggle)
    p = tab_opts(sub.add_parser("pick", help="single-keypress menu (for a popup keybinding)"))
    p.set_defaults(fn=cmd_pick)
    p = tab_opts(sub.add_parser("current", help="draw the tab's current layout"))
    p.set_defaults(fn=cmd_current)
    p = sub.add_parser("selftest", help="run the live tests on a throwaway Herdr server")
    p.add_argument("--bin", help="Herdr binary to test (default: $HERDR_BIN_PATH, else herdr.bin "
                                 "beside the herdr on PATH)")
    p.add_argument("--record", action="store_true", help="on pass, add the version to the tested list")
    p.set_defaults(fn=cmd_selftest)
    p = sub.add_parser("check-update", help="is a newer Herdr out? (exit 0 up to date, 10 update "
                                            "available, 11 restart pending, 1 error)")
    p.add_argument("--bin", help="installed Herdr binary (same default as selftest)")
    p.set_defaults(fn=cmd_check_update)

    p = sub.add_parser("report", help="store facts about a pane (adapters; JSON on stdin)")
    p.add_argument("--pane")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("shell-hook", help="print a shell snippet that reports folder changes")
    p.add_argument("shell", choices=sorted(SHELL_HOOKS))
    p.set_defaults(fn=cmd_shell_hook)
    p = sub.add_parser("tag", help="set the pane's task by hand (overrides everything)")
    p.add_argument("task", nargs="?")
    p.add_argument("--pane")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(fn=cmd_tag, refresh_marks=True)
    p = sub.add_parser("sort", help="move drifted panes to their tabs (focused tab by default)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--workspace", action="store_true", help="the whole focused workspace")
    g.add_argument("--all", action="store_true", help="every workspace on the Herdr server")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.set_defaults(fn=cmd_sort, refresh_marks=True)
    p = sub.add_parser("pull", help="gather one task's panes from every workspace into this one")
    p.add_argument("task")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.set_defaults(fn=cmd_pull, refresh_marks=True)
    p = sub.add_parser("drift-mark", help="refresh the tab marks now: <label> ↗N for N drifted panes")
    p.set_defaults(fn=cmd_drift_mark, refresh_marks=True)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except ERRORS as e:
        msg = error_text(e)
        print(f"heelr: {msg}", file=sys.stderr)
        if not sys.stderr.isatty():  # run from a background keybinding: show it in Herdr
            try:
                herdr.call("notification", "show", "heelr", "--body", msg[:300])
            except herdr.HerdrError:
                pass
        return 1
    finally:
        # these change where panes are (or are the focus hook): bring the tab marks up to date
        if getattr(args, "refresh_marks", False) and not getattr(args, "dry_run", False):
            marks.refresh(args.allow_untested)


if __name__ == "__main__":
    sys.exit(main())
