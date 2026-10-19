import io
import json
import sys

import pytest

from heelr import cli, core, facts, key, marks, place
from test_placement_place import fake  # noqa: F401
from test_placement_facts import make_repo, make_worktree
from test_apply_safety import lock_held


@pytest.fixture
def on(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "cfg/heelr").mkdir(parents=True)
    (tmp_path / "cfg/heelr/placement.toml").write_text('[placement]\nenabled = true\nticket = \'GH-\\d+\'\n')
    return fake


def h(*a):
    return cli.main(["--allow-untested", *a])


def state_files():
    return {p: p.read_bytes() for p in core.state_dir().rglob("*") if p.is_file()}


def test_report_stores_and_never_moves(on, monkeypatch):
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main", 1)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": "/a", "worktree": "GH-2"})))
    assert h("report", "--pane", p) == 0
    assert facts.load_pane(p)["report"]["worktree"] == "GH-2"
    monkeypatch.setattr(sys, "stdin", io.StringIO("garbage"))
    assert h("report", "--pane", p) == 0


def test_report_reads_the_status_line_shape(on, monkeypatch):
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main", 1)
    monkeypatch.setenv("HERDR_PANE_ID", p)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"workspace": {"current_dir": "/a"}, "worktree": {"name": "GH-3", "branch": "b3"}})))
    assert h("report") == 0
    rep = facts.load_pane(p)["report"]
    assert (rep["cwd"], rep["worktree"], rep["branch"]) == ("/a", "GH-3", "b3")


def test_tag_and_clear(on):
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main", 1)
    h("tag", "GH-7", "--pane", p)
    assert facts.load_pane(p)["tag"] == "GH-7"
    h("tag", "--clear", "--pane", p)
    assert "tag" not in facts.load_pane(p)


def test_sort_tab_then_back(on, tmp_path):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(wt))
    on.focused_tab, on.focused_pane = t1, a
    assert h("sort") == 0
    assert "GH-1" in on.labels() and b not in on.panes(t1)
    assert a in on.panes(t1)                     # the focused pane never moves
    assert h("back") == 0
    assert b in on.panes(t1)


def test_partial_sort_still_saves_undo_and_back_undoes_it(on, tmp_path):     # carry 1
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b, c) = on.add_tab(w, "main", 3, cwd=str(wt))
    on.focused_tab, on.focused_pane = t1, a
    on.add_tab(w, "GH-1", 1, cwd=str(wt))
    on.fail_at = 2                                # the second mutating call fails: one pane moved
    assert h("sort") == 0
    assert core.get_last(w) == "place"
    on.fail_at = None
    moved = [p for p in (b, c) if p not in on.panes(t1)]
    assert len(moved) == 1
    assert h("back") == 0
    assert len(on.panes(t1)) == 3               # the one moved pane is back


def test_nothing_moved_saves_no_undo(on, tmp_path, capsys):
    w = on.add_workspace("heelr")
    t1, (a,) = on.add_tab(w, "main", 1)
    on.focused_tab, on.focused_pane = t1, a
    assert h("sort") == 0
    assert core.get_last(w) != "place"
    assert not (core.state_dir() / "place-back.json").exists()


def test_pull_with_no_existing_tab_makes_one_tab(on, tmp_path):      # reviewer item 4
    ra, rb = make_repo(tmp_path / "ra"), make_repo(tmp_path / "rb")
    wa, wb = on.add_workspace("ra"), on.add_workspace("rb")
    ta, (a, x) = on.add_tab(wa, "main", 2, cwd=str(tmp_path))
    tb, (b,) = on.add_tab(wb, "x", 1, cwd=str(make_worktree(rb, "GH-6", "GH-6")))
    tc, (c,) = on.add_tab(wa, "y", 1, cwd=str(make_worktree(ra, "GH-6", "GH-6")))
    on.focused_tab, on.focused_pane = ta, a
    assert h("pull", "GH-6") == 0
    assert on.labels().count("GH-6") == 1
    assert not any(l.startswith("GH-6 · ") for l in on.labels())
    assert core.get_last(wa) == "place"


def test_pull_gathers_task_across_workspaces_and_skips_sleeping(on, tmp_path):
    ra, rb = make_repo(tmp_path / "ra"), make_repo(tmp_path / "rb")
    wa, wb = on.add_workspace("ra"), on.add_workspace("rb")
    ta, (a,) = on.add_tab(wa, "GH-5", 1, cwd=str(make_worktree(ra, "GH-5", "GH-5")))
    tb, (b,) = on.add_tab(wb, "GH-5", 1, cwd=str(make_worktree(rb, "GH-5", "GH-5")))
    tz, (z,) = on.add_tab(wb, core.SLEEP_MARK + "GH-5", 1, cwd=str(tmp_path / "rb/.worktrees/GH-5"))
    on.focused_tab, on.focused_pane = ta, a
    assert h("pull", "GH-5") == 0
    assert len(on.panes(ta)) == 2                # b joined (renumbered), a stayed
    assert z in on.panes(tz)                     # sleeping pane untouched


def test_pull_skips_a_tab_with_auto_tile_off_and_the_focused_pane(on, tmp_path):     # carry 2
    ra, rb = make_repo(tmp_path / "ra"), make_repo(tmp_path / "rb")
    wa, wb = on.add_workspace("ra"), on.add_workspace("rb")
    ta, (a,) = on.add_tab(wa, "main", 1, cwd=str(make_worktree(ra, "GH-5", "GH-5")))   # focused, has the task
    tb, (b,) = on.add_tab(wb, "x", 1, cwd=str(make_worktree(rb, "GH-5", "GH-5")))
    on.focused_tab, on.focused_pane = ta, a
    core.update_tab_record(tb, auto=False)
    assert h("pull", "GH-5") == 0
    assert b in on.panes(tb)                     # auto-tile off: not pulled
    assert on.panes(ta) == [a] and "GH-5" not in on.labels()   # focused pane not moved


def test_partial_pull_saves_undo(on, tmp_path):                                      # carry 1
    ra, rb = make_repo(tmp_path / "ra"), make_repo(tmp_path / "rb")
    wa, wb = on.add_workspace("ra"), on.add_workspace("rb")
    ta, (a,) = on.add_tab(wa, "main", 1, cwd=str(tmp_path))
    tb, (b, c) = on.add_tab(wb, "x", 2, cwd=str(make_worktree(rb, "GH-5", "GH-5")))
    on.focused_tab, on.focused_pane = ta, a
    on.fail_at = 2
    assert h("pull", "GH-5") == 0
    assert core.get_last(wa) == "place"
    assert (core.state_dir() / "place-back.json").exists()


def test_tag_without_task_or_clear_is_an_error(on, capsys):
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main", 1)
    h("tag", "GH-7", "--pane", p)
    assert h("tag", "--pane", p) == 1
    assert facts.load_pane(p)["tag"] == "GH-7"


def two_workspaces_sorted_apart(on, tmp_path):
    """A: pane b misplaced; B: pane d misplaced. Returns ids."""
    ra, rb = make_repo(tmp_path / "ra"), make_repo(tmp_path / "rb")
    wta, wtb = make_worktree(ra, "GH-1", "GH-1"), make_worktree(rb, "GH-2", "GH-2")
    wa, wb = on.add_workspace("ra"), on.add_workspace("rb")
    ta, (a, b) = on.add_tab(wa, "main", 2, cwd=str(wta))
    tb, (c, d) = on.add_tab(wb, "main", 2, cwd=str(wtb))
    return wa, wb, ta, tb, a, b, c, d


def focus(on, tab, pane):
    on.focused_tab, on.focused_pane = tab, pane


def test_back_in_a_after_sort_in_b_never_undoes_b(on, tmp_path):                  # C1
    wa, wb, ta, tb, a, b, c, d = two_workspaces_sorted_apart(on, tmp_path)
    focus(on, ta, a)
    assert h("sort") == 0
    focus(on, tb, c)
    assert h("sort") == 0
    moved_d = d not in on.panes(tb)
    assert moved_d
    focus(on, ta, a)
    h("back")                                           # A's own record was replaced by B's: nothing is applied
    assert d not in on.panes(tb)
    assert core.get_last(wa) != "place"                 # stale marker cleared


def test_back_twice_falls_through_to_the_layout_undo(on, tmp_path):                # C1
    wa, wb, ta, tb, a, b, c, d = two_workspaces_sorted_apart(on, tmp_path)
    prev = core.set_last(wa, "multi")
    assert prev is None
    focus(on, ta, a)
    assert h("sort") == 0
    assert core.get_last(wa) == "place"
    assert h("back") == 0
    assert b in on.panes(ta)
    assert core.get_last(wa) == "multi"                 # restored: the next back is the layout undo
    assert not (core.state_dir() / "place-back.json").exists()


def test_sort_twice_then_back_twice_never_loops_on_place(on, tmp_path):            # C1
    wa, wb, ta, tb, a, b, c, d = two_workspaces_sorted_apart(on, tmp_path)
    focus(on, ta, a)
    h("sort")
    h("sort")
    h("back")
    assert core.get_last(wa) != "place"


def test_back_dry_run_lists_what_it_would_restore(on, tmp_path, capsys):          # C4
    wa, wb, ta, tb, a, b, c, d = two_workspaces_sorted_apart(on, tmp_path)
    focus(on, ta, a)
    h("sort")
    capsys.readouterr()
    assert h("back", "-n") == 0
    out = capsys.readouterr().out
    assert f"would move {b}" in out and "main" in out and "workspace" in out
    assert d in on.all_panes() and core.get_last(wa) == "place"


def test_sort_workspace_and_all_scopes(on, tmp_path):                              # C4
    wa, wb, ta, tb, a, b, c, d = two_workspaces_sorted_apart(on, tmp_path)
    focus(on, ta, a)
    assert h("sort", "--workspace") == 0
    assert b not in on.panes(ta) and d in on.panes(tb)      # only the focused workspace
    focus(on, tb, c)
    assert h("sort", "--all") == 0
    assert d not in on.panes(tb)


def test_report_never_fails(on, monkeypatch):                                      # C3, C4
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main", 1)
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(facts, "save_report", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": "/a"})))
    assert h("report", "--pane", p) == 0
    monkeypatch.setattr(sys.stdin, "read", boom, raising=False)
    assert h("report", "--pane", p) == 0


@pytest.mark.parametrize("payload", ["", "[]", "null", "42", '"s"', "{}"])
def test_report_empty_or_non_dict_payload(on, monkeypatch, payload):                # C4
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main", 1)
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert h("report", "--pane", p) == 0


def test_a_mark_refresh_stays_in_its_budget(on, tmp_path, monkeypatch, capsys,
                                           real_subprocess, real_signals):        # C2
    import time
    s = tmp_path / "hang.sh"
    s.write_text("#!/bin/sh\nsleep 30\n")
    s.chmod(0o755)
    (tmp_path / "cfg/heelr/placement.toml").write_text(
        f"[placement]\nenabled = true\nticket = 'GH-\\\\d+'\nclassify = '{s}'\n")
    w = on.add_workspace("x")
    t, ps = on.add_tab(w, "main", 6, cwd=str(tmp_path))
    focus(on, t, ps[0])
    monkeypatch.setattr(marks, "BUDGET", 2.0)
    t0 = time.monotonic()
    assert h("drift-mark") == 0
    assert time.monotonic() - t0 < 3.0
    assert on.tabs[t]["label"] == "main"                 # out of budget: nothing renamed


def test_report_and_tag_with_the_old_id_follow_a_cross_workspace_move(on, monkeypatch):    # G3
    a = on.add_workspace("repo-a")
    b = on.add_workspace("repo-b")
    ta, (p1, p2) = on.add_tab(a, "main", 2)
    tb, (q,) = on.add_tab(b, "GH-3", 1)
    undo = place.execute(on.snapshot(), [place.Move(p2, place.Dest("repo-b", "GH-3"), False)],
                         key.PlacementConfig(enabled=True))
    new = undo[0]["pane"]
    assert new != p2
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": "/z"})))
    assert h("report", "--pane", p2) == 0
    h("tag", "GH-3", "--pane", p2)
    assert facts.load_pane(new)["report"]["cwd"] == "/z" and facts.load_pane(new)["tag"] == "GH-3"
    assert not facts._pane_path(p2).exists()


@pytest.fixture
def broken_classify(on, tmp_path, monkeypatch):
    (tmp_path / "cfg/heelr/placement.toml").write_text(
        "[placement]\nenabled = true\nticket = 'GH-\\d+'\nclassify = '/nonexistent/classify.sh'\n")
    monkeypatch.setattr(cli.time, "time", lambda: clock[0])
    clock[0] = 1000.0
    from heelr import key as keymod
    keymod.take_failures()

    def no_spawn(*a, **k):
        raise OSError("no such script")
    monkeypatch.setattr(keymod.subprocess, "Popen", no_spawn)
    return on


clock = [1000.0]


def _plog(tmp_path):
    p = tmp_path / "state" / "heelr" / "placement.log"
    return p.read_text() if p.exists() else ""


def _classify_notes(on):
    return [n for n in on.notifications if "classify" in n]


def test_classify_failure_in_sort_is_logged_and_notified_at_most_once_a_minute(broken_classify, tmp_path):   # G4c
    on = broken_classify
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    on.focused_tab, on.focused_pane = t1, a
    assert h("sort") == 0
    assert "could not start" in _plog(tmp_path) and "classify" in _plog(tmp_path)
    assert len(_classify_notes(on)) == 1
    clock[0] += 30
    h("sort", "--all")
    assert len(_classify_notes(on)) == 1                  # inside the minute
    clock[0] += 31
    h("sort", "--all")
    assert len(_classify_notes(on)) == 2


def test_classify_failure_in_pull_is_reported(broken_classify, tmp_path):                      # G4c
    on = broken_classify
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    on.focused_tab, on.focused_pane = t1, a
    h("pull", "GH-1")
    assert "classify" in _plog(tmp_path) and len(_classify_notes(on)) == 1


def test_classify_failure_in_the_hook_is_reported(broken_classify, tmp_path, monkeypatch):     # G4c
    import json as _j
    on = broken_classify
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    monkeypatch.setenv("HERDR_PLUGIN_EVENT", "pane.created")
    monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", _j.dumps({"data": {"pane": {"pane_id": b}}}))
    assert h("auto") == 0
    assert "classify" in _plog(tmp_path) and len(_classify_notes(on)) == 1


def test_pull_targets_the_focused_workspace_not_its_label_twin(on, tmp_path):                 # G8
    r = make_repo(tmp_path / "heelr")
    w1, w2, wo = on.add_workspace("heelr"), on.add_workspace("heelr"), on.add_workspace("other")
    t1, (a1,) = on.add_tab(w1, "GH-7", 1, cwd=str(make_worktree(r, "GH-7", "GH-7")))
    t2, (a2,) = on.add_tab(w2, "main", 1, cwd=str(tmp_path))
    to, (b,) = on.add_tab(wo, "x", 1, cwd=str(r / ".worktrees/GH-7"))
    on.focused_tab, on.focused_pane = t2, a2                 # the second "heelr" is focused
    assert h("pull", "GH-7") == 0
    gh7 = [t for t in on.tabs if on.tabs[t]["label"] == "GH-7"]
    assert [on.tabs[t]["ws"] for t in gh7] == [w2] and len(on.panes(gh7[0])) == 2      # both landed in the focused one


def test_nothing_to_pull_says_so(on, tmp_path, capsys):                                      # G10
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main", 1)
    assert h("pull", "GH-404") == 0
    out = capsys.readouterr().out
    assert "nothing to pull" in out and "nothing to sort" not in out
    h("sort")
    assert "nothing to sort" in capsys.readouterr().out
