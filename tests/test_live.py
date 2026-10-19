"""Live test against a Herdr server. Prefer `heelr selftest`, which runs this
file against a throwaway server; HEELR_LIVE=1 pytest tests/test_live.py runs it
against whichever server $HERDR_SOCKET_PATH points at.

Creates a throwaway, unfocused workspace with shell panes, applies every
rule preset plus `back`, and asserts pane ids and process ids never change (R2).
Closes the workspace afterwards. Never touches other workspaces.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from heelr import core, herdr
from heelr.assign import DEFAULT_ROLES
from heelr.layouts import load
from heelr.tree import same_structure

# real_processes lifts the conftest guard: these tests drive a throwaway server (skipped unless HEELR_LIVE=1)
pytestmark = [pytest.mark.skipif(os.environ.get("HEELR_LIVE") != "1", reason="set HEELR_LIVE=1"),
              pytest.mark.real_processes]


REPO = str(Path(__file__).resolve().parents[1])

# these write heelr config/state or link plugins under HOME: only safe on selftest's throwaway server
selftest_only = pytest.mark.skipif(not os.environ.get(herdr.SELFTEST_ALLOW_ENV),
                                   reason="writes config/state or links a plugin: only on selftest's throwaway server")


def heelr_cli(tab, *args, env=None):
    """Run this checkout's heelr (not whatever `heelr` is on PATH) in the same env, so the same server.
    tab=None: no --tab (sort, pull and auto take none)."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [REPO, os.environ.get("PYTHONPATH")])),
           **(env or {})}
    return subprocess.run([sys.executable, "-m", "heelr", *args, *(["--tab", tab] if tab else [])],
                          capture_output=True, text=True, env=env, timeout=60)


def pids(ws):
    out = {}
    for p in herdr.call("pane", "list", "--workspace", ws)["panes"]:
        info = herdr.call("pane", "process-info", "--pane", p["pane_id"])["process_info"]
        out[p["pane_id"]] = info["shell_pid"]
    return out


def _throwaway_workspace(n_panes):
    r = herdr.call("workspace", "create", "--label", "heelr-live-test", "--no-focus", "--cwd", "/tmp")
    ws, first = r["workspace"]["workspace_id"], r["root_pane"]["pane_id"]
    tab = r["tab"]["tab_id"]
    for _ in range(n_panes - 1):
        herdr.split(first, "right", 0.5)
    yield ws, tab
    herdr.call("workspace", "close", ws)
    for f in core._back_path(tab).parent.glob("*.json"):  # every undo record of this workspace
        if f.stem.startswith((f"{ws}_", f"workspace_{ws}", f"last_{ws}")):
            f.unlink()


@pytest.fixture
def workspace():
    yield from _throwaway_workspace(5)


@pytest.fixture
def one_pane_workspace():
    yield from _throwaway_workspace(1)


def test_presets_and_back_keep_processes(workspace):
    ws, tab = workspace
    before = pids(ws)
    assert len(before) == 5
    shapes = []
    for rule in ("main-left", "grid", "main-top", "main-right", "main-bottom", "main-left-grid",
                 "two-mains", "agents-logs", "even-rows", "even-columns", "main-center"):
        snap = herdr.snapshot()
        herdr.check_version(snap)
        ts = core.read_tab(snap, tab, DEFAULT_ROLES, with_cmdline=lambda p: "")
        lay = load(rule)
        lay.add_missing = False  # this test is about rearranging only; see test_add_missing_*
        target, _ = core.target_for(lay, ts)
        res = core.run(target, [], ts, dry_run=False)
        assert res.drift == [], (rule, res.drift)
        shapes.append(target)
    # back restores the layout from before the last apply
    snap = herdr.snapshot()
    ts = core.read_tab(snap, tab, DEFAULT_ROLES, with_cmdline=lambda p: "")
    res = core.run(core.load_back(ts), [], ts, dry_run=False)
    assert res.drift == []
    snap = herdr.snapshot()
    ts = core.read_tab(snap, tab, DEFAULT_ROLES, with_cmdline=lambda p: "")
    assert same_structure(shapes[-2], ts.tree, tol=0.05) == []
    # R2: same panes, same processes, nothing left behind in park tabs
    assert pids(ws) == before
    tabs = herdr.call("tab", "list", "--workspace", ws)["tabs"]
    assert [t["tab_id"] for t in tabs] == [tab]


def test_multi_gather_back_keeps_processes(workspace):
    from heelr import multi
    from heelr.layouts import parse
    ws_id, tab = workspace
    herdr.call("tab", "create", "--workspace", ws_id, "--label", "second", "--no-focus", "--cwd", "/tmp")
    before = pids(ws_id)
    assert len(before) == 6
    spec = parse({"name": "t", "kind": "multi", "tabs": [
        {"label": "left", "gather": ["*"], "max": 4, "layout": {"kind": "rule", "rule": "grid"}},
        {"label": "right", "gather": ["*"], "layout": {"kind": "rule", "rule": "even-rows"}}]})
    read = lambda: multi.read_workspace(herdr.snapshot(), ws_id, DEFAULT_ROLES, with_cmdline=lambda p: "")
    ws = read()
    multi.save_ws_back(ws)
    dests = multi.assign_tabs(spec, ws)
    sym = multi.execute_gather(multi.plan_gather(ws, dests), ws_id)
    problems, _ = multi.layout_tabs(dests, sym, DEFAULT_ROLES, log=lambda *_: None)
    assert problems == 0
    ws = read()
    assert sorted((t.label, len(t.panes)) for t in ws.tabs) == [("left", 4), ("right", 2)]
    # back: original tabs (recreated if they closed) with their panes
    dests = multi.back_dests(ws)
    sym = multi.execute_gather(multi.plan_gather(ws, dests), ws_id)
    problems, _ = multi.layout_tabs(dests, sym, DEFAULT_ROLES, log=lambda *_: None)
    assert problems == 0
    ws = read()
    assert sorted(len(t.panes) for t in ws.tabs) == [1, 5]
    assert pids(ws_id) == before


def six_columns_file(tmp_path) -> str:
    """A fixed six-slot layout with add_missing: on the five-pane tab it
    creates exactly one pane. (agents-logs no longer does on a tab of shells:
    with no agent it's a plain grid, and these panes run no agent.)"""
    from heelr.layouts import expand_rule
    text = core.to_toml("six-columns", expand_rule("even-columns", 6)).replace(
        'on_mismatch = "refuse"   # refuse | fill', 'on_mismatch = "fill"\nadd_missing = true')
    p = tmp_path / "six-columns.toml"
    p.write_text(text)
    return str(p)


def test_add_missing_creates_then_back_closes(workspace, tmp_path):
    """A fill layout with one slot more than the tab has panes opens a new
    pane; back closes it and restores the original panes and processes."""
    ws, tab = workspace
    before = pids(ws)
    hl = lambda *a: heelr_cli(tab, *a)
    out = hl("apply", six_columns_file(tmp_path))
    assert out.returncode == 0, out.stderr
    assert "created 1 new pane" in out.stdout
    assert len(pids(ws)) == len(before) + 1
    out = hl("back")
    assert out.returncode == 0, out.stderr
    assert "closed pane(s) the layout created" in out.stdout
    assert pids(ws) == before


@pytest.mark.parametrize("name,total", [("main-left", 2), ("two-mains", 3), ("even-rows", 2)])
def test_preset_fills_a_one_pane_tab_then_back_closes(one_pane_workspace, name, total):
    """A preset with add_missing on a one-pane tab creates the missing panes in
    the preset's shape; back closes them and leaves the original pane alone."""
    from heelr.layouts import expand_rule
    ws, tab = one_pane_workspace
    before = pids(ws)
    assert len(before) == 1
    hl = lambda *a: heelr_cli(tab, *a)
    out = hl("apply", name)
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"created {total - 1} new pane" in out.stdout
    after = pids(ws)
    assert len(after) == total and set(before.items()) <= set(after.items())
    ts = core.read_tab(herdr.snapshot(), tab, DEFAULT_ROLES, with_cmdline=lambda p: "")
    assert same_structure(expand_rule(name, total, load(name).main_ratio), ts.tree, tol=0.05, check_panes=False) == []
    out = hl("apply", name)                      # re-apply: already in shape, nothing new
    assert out.returncode == 0 and "nothing to do" in out.stdout, out.stdout + out.stderr
    out = hl("back")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "closed pane(s) the layout created" in out.stdout
    assert pids(ws) == before


@pytest.mark.parametrize("name", ["main-right", "main-center", "two-mains", "main-left-grid"])
def test_nudge_resizes_in_place_and_back(workspace, name):
    """nudge sets the layout's ratios with `pane resize` alone: same panes and
    processes, and the tab matches the layout drawn at the new ratio."""
    from heelr.layouts import expand_rule
    ws, tab = workspace
    before = pids(ws)
    hl = lambda *a: heelr_cli(tab, *a)
    out = hl("apply", name)
    assert out.returncode == 0, out.stdout + out.stderr
    base = load(name).main_ratio
    for step, want in (("+", round(min(0.8, base + 0.1), 2)), ("-", round(base, 2))):
        out = hl("nudge", step)
        assert out.returncode == 0, out.stdout + out.stderr
        ts = core.read_tab(herdr.snapshot(), tab, DEFAULT_ROLES, with_cmdline=lambda p: "")
        assert same_structure(expand_rule(name, 5, want), ts.tree, tol=0.03, check_panes=False) == [], \
            (name, step, want)
    assert pids(ws) == before


def test_promote_swaps_with_the_largest_and_back_undoes_it(workspace):
    ws, tab = workspace
    before = pids(ws)
    hl = lambda *a: heelr_cli(tab, *a)
    assert hl("apply", "main-right").returncode == 0
    lay = herdr.call("pane", "layout", "--pane", next(iter(before)))["layout"]
    main = core.largest_pane(lay)
    herdr.call("pane", "focus", "--pane", main, "--direction", "left")   # into the stack
    small = herdr.call("pane", "layout", "--pane", main)["layout"]["focused_pane_id"]
    assert small != main
    out = hl("promote")
    assert out.returncode == 0, out.stdout + out.stderr
    lay = herdr.call("pane", "layout", "--pane", main)["layout"]
    assert core.largest_pane(lay) == small and lay["focused_pane_id"] == small
    out = hl("back")
    assert out.returncode == 0, out.stdout + out.stderr
    assert core.largest_pane(herdr.call("pane", "layout", "--pane", main)["layout"]) == main
    assert pids(ws) == before


def _wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


@selftest_only
def test_the_plugin_hook_auto_tiles_new_and_closed_panes(workspace):
    """The real hook path: Herdr runs the plugin's `../../bin/heelr auto` on pane events."""
    from heelr.layouts import expand_rule
    ws, tab = workspace
    binary = os.environ["HERDR_BIN_PATH"]
    plugin = Path(REPO) / "plugins" / "tab-colours"
    cfg = core.config_dir() / "config.toml"
    assert Path(os.environ["HOME"]) in cfg.parents          # the throwaway HOME, never the user's
    subprocess.run([binary, "plugin", "link", str(plugin)], check=True, capture_output=True)
    try:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("auto_tile = true\n")
        before = pids(ws)
        assert heelr_cli(tab, "apply", "main-right").returncode == 0
        first = next(iter(before))
        new = herdr.split(first, "down", 0.5)["pane_id"]
        assert _wait_for(lambda: new in core.tab_record(tab).get("panes", [])), core.tab_record(tab)
        order = core.tab_record(tab)["panes"]
        assert order[-1] == new and len(order) == 6
        ts = core.read_tab(herdr.snapshot(), tab, DEFAULT_ROLES, with_cmdline=lambda p: "")
        want = expand_rule("main-right", 6, load("main-right").main_ratio)
        assert same_structure(want, ts.tree, tol=0.05, check_panes=False) == []
        herdr.call("pane", "close", new)
        assert _wait_for(lambda: new not in core.tab_record(tab).get("panes", [new]))
        ts = core.read_tab(herdr.snapshot(), tab, DEFAULT_ROLES, with_cmdline=lambda p: "")
        want = expand_rule("main-right", 5, load("main-right").main_ratio)
        assert same_structure(want, ts.tree, tol=0.05, check_panes=False) == []
        assert pids(ws) == before
        assert not (core.state_dir() / "auto.log").exists()
    finally:
        cfg.unlink(missing_ok=True)
        subprocess.run([binary, "plugin", "unlink", "heelr.tab-colours"], capture_output=True)


@selftest_only
def test_tab_colours_round_trips_tokens_through_the_real_snapshot(workspace, tmp_path):
    """The colour plugin reads `tokens` back from `api snapshot`: pin that shape on a real server."""
    ws, tab = workspace
    script = Path(REPO) / "plugins" / "tab-colours" / "tab_colours.py"
    env = dict(os.environ, HERDR_PLUGIN_STATE_DIR=str(tmp_path / "st"),
               HERDR_PLUGIN_CONFIG_DIR=str(tmp_path / "cfg"))
    subprocess.run([sys.executable, str(script)], env=env, check=True, capture_output=True, timeout=60)
    s = herdr.snapshot()
    label = next(t.get("label") or str(t.get("number")) for t in s["tabs"] if t["tab_id"] == tab)
    toks = [{k: v for k, v in (p.get("tokens") or {}).items() if k.startswith("tabc")}
            for p in s["panes"] if p.get("tab_id") == tab]
    assert toks and all(len(t) == 1 for t in toks), toks
    assert len({next(iter(t)) for t in toks}) == 1                # one slot for the whole tab
    assert all(next(iter(t.values())) == label for t in toks)    # valued with the tab's label
    out = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, timeout=60)
    assert out.returncode == 0
    assert {p["pane_id"]: p.get("tokens") for p in herdr.snapshot()["panes"]} == \
        {p["pane_id"]: p.get("tokens") for p in s["panes"]}                 # converged: nothing to redo


STUB_AGENT = r"""#!/bin/bash
# Stand-in for `claude --resume <id>`: draws an idle prompt and reports its session to Herdr,
# starts an MCP-like child in its own process group and, with $STUB_TASK set, a "task" in
# a group of its own (as a Bash-tool shell is), then becomes a long-lived process whose
# argv[0] is "claude".
sid=$2
h=${HERDR_BIN_PATH:-herdr}
# Herdr reads Claude's state from the screen: an empty prompt box reads as idle
printf '\n%s\n❯ \n%s\n' "$(printf '─%.0s' $(seq 40))" "$(printf '─%.0s' $(seq 40))"
# what Claude's SessionStart hook reports
"$h" pane report-agent-session "$HERDR_PANE_ID" --source herdr:claude --agent claude \
  --agent-session-id "$sid" >>"$HOME/stub-agent.log" 2>&1
sleep 99999 &
if [ -n "$STUB_TASK" ]; then setsid sleep 99998 & fi
exec -a claude python3 -c 'import signal, time; signal.signal(signal.SIGCHLD, signal.SIG_IGN); time.sleep(1e9)' --resume "$sid"
"""


def _fg(pane):
    info = herdr.call("pane", "process-info", "--pane", pane)["process_info"]
    lead = [p for p in info["foreground_processes"] if p["pid"] == info["foreground_process_group_id"]]
    return info, (lead[0] if lead else None)


def _is_stub(lead):
    """The stub after its exec: argv[0] is "claude" (its comm is python3)."""
    return bool(lead) and (lead.get("argv") or [""])[0] == "claude" and "--resume" in lead["argv"]


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@selftest_only
def test_sleep_refuses_a_task_then_sleeps_and_wakes_a_stub_agent(one_pane_workspace, tmp_path):
    ws, tab = one_pane_workspace
    home = Path(os.environ["HOME"])
    pane = herdr.call("pane", "list", "--workspace", ws)["panes"][0]["pane_id"]
    sid = "00000000-0000-4000-8000-00000000abcd"
    proj = home / ".claude" / "projects" / "-tmp"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{sid}.jsonl").write_text('{"type":"custom-title","customTitle":"stub-agent","sessionId":"%s"}\n' % sid)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(STUB_AGENT)
    (bin_dir / "claude").chmod(0o755)
    herdr.run_in(pane, f"export PATH={bin_dir}:$PATH; STUB_TASK=1 claude --resume {sid}")
    assert _wait_for(lambda: _is_stub(_fg(pane)[1])), _fg(pane)
    status = lambda: herdr.call("pane", "get", pane)["pane"].get("agent_status")  # noqa: E731
    if not _wait_for(lambda: status() == "idle"):
        ex = subprocess.run([os.environ["HERDR_BIN_PATH"], "agent", "explain", pane], capture_output=True, text=True)
        rd = subprocess.run([os.environ["HERDR_BIN_PATH"], "pane", "read", pane], capture_output=True, text=True)
        pytest.fail(f"stub never idle: {status()}\nexplain: {ex.stdout}{ex.stderr}\nscreen:\n{rd.stdout[-600:]}")
    _, lead = _fg(pane)
    leader = lead["pid"]
    def tab_label():
        return next(t["label"] for t in herdr.snapshot()["tabs"] if t["tab_id"] == tab)
    label = tab_label()
    try:
        # a running task (a child in its own group) blocks the whole tab
        r = heelr_cli(tab, "sleep")
        assert r.returncode == 1 and "has a running task: sleep" in r.stdout, r.stdout + r.stderr
        assert _alive(leader)
        task = [int(p) for p in os.listdir("/proc") if p.isdigit() and _ppid(int(p)) == leader
                and os.getpgid(int(p)) != leader]
        assert len(task) == 1
        os.kill(task[0], 9)
        assert _wait_for(lambda: not _alive(task[0]))
        group = [int(p) for p in os.listdir("/proc") if p.isdigit() and _pgid(int(p)) == leader]
        assert len(group) == 2                                   # the leader and its MCP-like child
        r = heelr_cli(tab, "sleep")
        log = (home / "stub-agent.log").read_text() if (home / "stub-agent.log").exists() else ""
        assert r.returncode == 0 and "slept 1 agent" in r.stdout, (r.stdout + r.stderr, log,
                                                                   herdr.call("pane", "get", pane))
        assert not any(_alive(p) for p in group)                 # the whole group is gone
        assert _fg(pane)[1]["pid"] == _fg(pane)[0]["shell_pid"]  # the shell is back in front
        assert tab_label() == f"💤 {label}"
        r = heelr_cli(tab, "wake")
        assert r.returncode == 0 and "woke 1 agent" in r.stdout, r.stdout + r.stderr
        assert _wait_for(lambda: _is_stub(_fg(pane)[1])), (
            _fg(pane), subprocess.run([os.environ["HERDR_BIN_PATH"], "pane", "read", pane],
                                      capture_output=True, text=True).stdout[-800:])
        leader = _fg(pane)[1]["pid"]
        assert tab_label() == label
        r = heelr_cli(tab, "wake")                                # nothing left to wake: types nothing
        assert r.returncode == 0 and "nothing asleep" in r.stdout
        assert _fg(pane)[1]["pid"] == leader
    finally:
        for p in os.listdir("/proc"):
            if p.isdigit() and _pgid(int(p)) == leader:
                os.kill(int(p), 9)


def _ppid(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _pgid(pid):
    try:
        return os.getpgid(pid)
    except OSError:
        return None


# ------------------------------------------------------------------ placement

def _wt_repo(base: Path, name: str, tasks) -> dict[str, str]:
    """A repo with a main checkout and one linked worktree per task, on disk only (no git process).
    Returns {"main": path, task: path, ...}."""
    root = base / name
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git" / "refs" / "heads" / "main").write_text("0" * 40 + "\n")
    out = {"main": str(root)}
    for task in tasks:
        gd = root / ".git" / "worktrees" / task
        gd.mkdir(parents=True)
        (gd / "HEAD").write_text(f"ref: refs/heads/{task}\n")
        (gd / "commondir").write_text("../..\n")
        wt = root / ".worktrees" / task
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {gd}\n")
        out[task] = str(wt)
    return out


@pytest.fixture
def placing(tmp_path):
    """Placement enabled in the throwaway HOME's config; closes every workspace made and clears state."""
    home = Path(os.environ["HOME"])
    cfg = core.config_dir() / "placement.toml"
    assert home in cfg.parents and core.state_dir().is_relative_to(home)   # never the user's
    before = {w["workspace_id"] for w in herdr.snapshot()["workspaces"]}
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("[placement]\nenabled = true\nticket = 'GH-\\d+'\nburst_window = 0.2\n")
    repo_base = tmp_path / "repos"
    repo_base.mkdir()
    yield repo_base
    cfg.unlink(missing_ok=True)
    for w in herdr.snapshot()["workspaces"]:
        if w["workspace_id"] not in before:
            herdr.call("workspace", "close", w["workspace_id"])
    for f in core.state_dir().iterdir():
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            shutil.rmtree(f, ignore_errors=True)


def _ws(label):
    return next((w["workspace_id"] for w in herdr.snapshot()["workspaces"] if w.get("label") == label), None)


def _tabs_of(ws):
    """{base label: sorted cwd basenames of its panes} for one workspace."""
    s = herdr.snapshot()
    out = {}
    for t in s["tabs"]:
        if t["workspace_id"] == ws:
            out[core.base_label(t)] = sorted(Path(p["cwd"]).name for p in s["panes"] if p["tab_id"] == t["tab_id"])
    return out


def _new_workspace(label, cwd, focus=False):
    r = herdr.call("workspace", "create", "--label", label, *([] if focus else ["--no-focus"]), "--cwd", cwd)
    return r["workspace"]["workspace_id"], r["root_pane"]["pane_id"], r["tab"]["tab_id"]


@selftest_only
def test_placement_create_replies_have_the_shapes_place_reads(placing):
    """place.py reads the pane id and tab id out of `workspace create`; pin the shape on a real server."""
    ws, pane, tab = _new_workspace("shape-ws", "/tmp")
    assert pane.startswith(ws) and tab.startswith(ws)
    r = herdr.call("tab", "create", "--workspace", ws, "--label", "shape-tab", "--no-focus", "--cwd", "/tmp")
    assert r["tab"]["tab_id"] and r["tab"]["workspace_id"] == ws and r["root_pane"]["pane_id"]
    assert r["root_pane"]["tab_id"] == r["tab"]["tab_id"]
    # the move reply place.py reads, for a move to a new tab and into a tab
    p2 = herdr.split(pane, "right", 0.5)["pane_id"]
    m = herdr.move(p2, new_tab=True, workspace=ws, label="moved", focus=False)
    assert m["pane"]["pane_id"] and m["pane"]["tab_id"] not in (tab, r["tab"]["tab_id"])
    m2 = herdr.move(m["pane"]["pane_id"], tab=tab, target=pane, split="right", focus=False)
    assert m2["pane"]["tab_id"] == tab


@selftest_only
def test_placement_auto_puts_a_new_worktree_pane_in_its_task_tab(placing):
    paths = _wt_repo(placing, "heelrrepo", ["GH-1"])
    ws, first, tab = _new_workspace("heelrrepo", paths["main"])     # the repo's workspace, one "main" pane
    new = herdr.call("pane", "split", first, "--direction", "right", "--ratio", "0.5", "--no-focus",
                     "--cwd", paths["GH-1"])["pane"]["pane_id"]
    out = _auto(new)
    assert out.returncode == 0, out.stdout + out.stderr
    assert _tabs_of(ws) == {"1": ["heelrrepo"], "GH-1": ["GH-1"]}, (_tabs_of(ws), _read_log("placement.log"))


@selftest_only
def test_placement_auto_creates_the_missing_workspace_and_task_tab(placing):
    paths = _wt_repo(placing, "newrepo", ["GH-2"])
    ws, first, tab = _new_workspace("scratch", paths["main"])
    new = herdr.call("pane", "split", first, "--direction", "right", "--ratio", "0.5", "--no-focus",
                     "--cwd", paths["GH-2"])["pane"]["pane_id"]
    out = _auto(new)
    assert out.returncode == 0, out.stdout + out.stderr
    target = _ws("newrepo")
    assert target, (herdr.snapshot()["workspaces"], _read_log("placement.log"))
    assert _tabs_of(target) == {"GH-2": ["GH-2"]}, (_tabs_of(target), _read_log("placement.log"))   # initial shell closed
    assert _tabs_of(ws) == {"1": ["newrepo"]}


def _auto(pane):
    """What Herdr's pane.created hook runs: `heelr auto` with the event in its environment."""
    event = json.dumps({"event": "pane.created", "data": {"pane": {"pane_id": pane}}})
    return heelr_cli(None, "auto", env={"HERDR_PLUGIN_EVENT": "pane.created", "HERDR_PLUGIN_EVENT_JSON": event})


def _read_log(name):
    p = core.state_dir() / name
    return p.read_text() if p.exists() else ""


@selftest_only
def test_placement_pull_then_back_restores_every_pane(placing):
    a = _wt_repo(placing, "repoa", ["GH-7"])
    b = _wt_repo(placing, "repob", ["GH-7"])
    wa, pa, _ = _new_workspace("repoa", a["main"])
    wb, pb, _ = _new_workspace("repob", b["main"])
    herdr.call("tab", "create", "--workspace", wa, "--label", "side", "--no-focus", "--cwd", a["GH-7"])  # emptied by pull
    herdr.call("pane", "split", pb, "--direction", "right", "--ratio", "0.5", "--no-focus", "--cwd", b["GH-7"])
    herdr.call("pane", "split", pb, "--direction", "down", "--ratio", "0.5", "--no-focus", "--cwd", b["GH-7"])
    wh, ph, _ = _new_workspace("home-ws", "/tmp", focus=True)        # the workspace pulled into
    herdr.call("workspace", "focus", wh)
    before = {w: _tabs_of(w) for w in (wa, wb)}
    out = heelr_cli(None, "pull", "GH-7")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "moved 3 pane(s)" in out.stdout, out.stdout
    assert _tabs_of(wh)["GH-7"] == ["GH-7"] * 3
    assert all("GH-7" not in _tabs_of(w) for w in (wa, wb)) and "side" not in _tabs_of(wa)   # side closed
    out = heelr_cli(None, "back")
    assert out.returncode == 0, out.stdout + out.stderr
    assert {w: _tabs_of(w) for w in (wa, wb)} == before
    assert "GH-7" not in _tabs_of(wh)


@selftest_only
def test_placement_sort_all_never_moves_the_focused_pane(placing):
    a = _wt_repo(placing, "repoa", ["GH-3", "GH-4"])
    ws, first, tab = _new_workspace("repoa", a["main"], focus=True)
    herdr.call("workspace", "focus", ws)
    other = herdr.call("pane", "split", first, "--direction", "down", "--ratio", "0.5", "--no-focus",
                       "--cwd", a["GH-4"])["pane"]["pane_id"]
    focused = herdr.call("pane", "split", first, "--direction", "right", "--ratio", "0.5", "--focus",
                         "--cwd", a["GH-3"])["pane"]["pane_id"]       # drifted too, but focused
    assert herdr.snapshot()["focused_pane_id"] == focused
    out = heelr_cli(None, "sort", "--all")
    assert out.returncode == 0, out.stdout + out.stderr
    s = herdr.snapshot()
    pane = {p["pane_id"]: p for p in s["panes"]}
    assert pane[focused]["tab_id"] == tab                 # stayed in the tab it was in
    assert s["focused_pane_id"] == focused
    assert pane[other]["tab_id"] != tab                    # the unfocused drifted pane did move
    assert _tabs_of(ws)["GH-4"] == ["GH-4"]


@selftest_only
def test_drift_marks_label_the_tab_and_clear_after_sort(placing):
    a = _wt_repo(placing, "repom", ["GH-5"])
    ws, first, tab = _new_workspace("repom", a["main"], focus=True)
    herdr.call("workspace", "focus", ws)
    herdr.call("pane", "split", first, "--direction", "down", "--ratio", "0.5", "--no-focus",
               "--cwd", a["GH-5"])                             # belongs in tab GH-5
    assert herdr.snapshot()["focused_pane_id"] == first
    out = heelr_cli(None, "drift-mark")
    assert out.returncode == 0, out.stdout + out.stderr
    label = lambda: next(t["label"] for t in herdr.snapshot()["tabs"] if t["tab_id"] == tab)
    assert label().endswith(" ↗2"), label()        # both: first belongs in a tab main, the other in GH-5
    assert _tabs_of(ws) == {core.base_label({"label": label()}): ["GH-5", "repom"]}
    out = heelr_cli(None, "sort", "--all")
    assert out.returncode == 0, out.stdout + out.stderr
    assert label().endswith(" ↗1"), label()        # sort leaves the focused pane where it is
    assert _tabs_of(ws)["GH-5"] == ["GH-5"]

