import pytest
from fakeherdr import FakeHerdr
from heelr import herdr


def test_snapshot_lists_workspaces_and_cwd():
    f = FakeHerdr()
    w = f.add_workspace("heelr")
    tab, (p,) = f.add_tab(w, "main", 1, cwd="/r/heelr")
    snap = f.snapshot()
    assert {"workspace_id": w, "label": "heelr"} in [
        {k: x[k] for k in ("workspace_id", "label")} for x in snap["workspaces"]]
    pane = next(x for x in snap["panes"] if x["pane_id"] == p)
    assert pane["cwd"] == pane["foreground_cwd"] == "/r/heelr"


def test_cross_workspace_move_renumbers_the_pane():
    f = FakeHerdr()
    a, b = f.add_workspace("a"), f.add_workspace("b")
    ta, (p1, p2) = f.add_tab(a, "x", 2)
    tb, (q,) = f.add_tab(b, "y", 1)
    r = f.call("pane", "move", p2, "--tab", tb, "--split", "right", "--target-pane", q, "--no-focus")
    new = r["move_result"]["pane"]["pane_id"]
    assert new != p2 and new.startswith(b + ":") and new in f.panes(tb)


def test_same_workspace_move_keeps_the_id():
    f = FakeHerdr()
    a = f.add_workspace("a")
    t1, (p1, p2) = f.add_tab(a, "x", 2)
    t2, (q,) = f.add_tab(a, "y", 1)
    r = f.call("pane", "move", p2, "--tab", t2, "--split", "right", "--target-pane", q, "--no-focus")
    assert r["move_result"]["pane"]["pane_id"] == p2


def test_tab_and_workspace_create():
    f = FakeHerdr()
    a = f.add_workspace("a")
    f.add_tab(a, "x", 1)
    r = f.call("tab", "create", "--workspace", a, "--label", "GH-1", "--cwd", "/r", "--no-focus")
    assert r["tab"]["label"] == "GH-1" and r["root_pane"]["pane_id"] in f.panes(r["tab"]["tab_id"])
    assert f.cwd[r["root_pane"]["pane_id"]] == "/r"
    w = f.call("workspace", "create", "--label", "repo2", "--cwd", "/r2", "--no-focus")
    assert f.workspaces[w["workspace"]["workspace_id"]] == "repo2"
    assert f.cwd[w["root_pane"]["pane_id"]] == "/r2"


def test_panes_in_one_tab_have_distinct_pids():
    f = FakeHerdr()
    _, (p1, p2, p3) = f.add_tab(f.add_workspace("a"), "x", 3)
    pids = {f.procs[p]["shell_pid"] for p in (p1, p2, p3)}
    gids = {f.procs[p]["foreground_process_group_id"] for p in (p1, p2, p3)}
    assert len(pids) == len(gids) == 3


def test_cross_workspace_move_carries_state_to_the_new_id():
    f = FakeHerdr()
    a, b = f.add_workspace("a"), f.add_workspace("b")
    _, (p1, p2) = f.add_tab(a, "x", 2, agents=(1,), cwd="/r/a")
    tb, (q,) = f.add_tab(b, "y", 1)
    f.busy(p2, "vim")
    f.focused_pane = p2
    proc = dict(f.procs[p2])
    r = f.call("pane", "move", p2, "--tab", tb, "--split", "right", "--target-pane", q, "--no-focus")
    new = r["move_result"]["pane"]["pane_id"]
    for d in (f.procs, f.cwd, f.agents):
        assert p2 not in d and new in d
    assert f.procs[new] == proc and f.cwd[new] == "/r/a"
    assert f.focused_pane == new


def test_new_tab_move_to_other_workspace_renumbers():
    f = FakeHerdr()
    a, b = f.add_workspace("a"), f.add_workspace("b")
    _, (p1, p2) = f.add_tab(a, "x", 2, cwd="/r/a")
    f.add_tab(b, "y", 1)
    r = f.call("pane", "move", p2, "--new-tab", "--workspace", b, "--label", "L", "--no-focus")
    new = r["move_result"]["pane"]["pane_id"]
    assert new.startswith(b + ":") and new != p2 and f.cwd[new] == "/r/a" and p2 not in f.cwd
    assert f.tab_of(new) == r["move_result"]["pane"]["tab_id"]


def test_move_rejects_workspace_without_new_tab():
    f = FakeHerdr()
    a, b = f.add_workspace("a"), f.add_workspace("b")
    _, (p1, p2) = f.add_tab(a, "x", 2)
    f.add_tab(b, "y", 1)
    with pytest.raises(herdr.HerdrError):
        f.call("pane", "move", p2, "--workspace", b, "--no-focus")


def test_move_focus_follows_unless_no_focus():
    f = FakeHerdr()
    a = f.add_workspace("a")
    _, (p1, p2, p3) = f.add_tab(a, "x", 3)
    t2, (q,) = f.add_tab(a, "y", 1)
    f.call("pane", "move", p2, "--tab", t2, "--split", "right", "--target-pane", q, "--no-focus")
    assert f.focused_pane == p1
    f.call("pane", "move", p3, "--tab", t2, "--split", "right", "--target-pane", q)
    assert (f.focused_tab, f.focused_pane) == (t2, p3)
    f.call("pane", "move", p3, "--new-tab", "--label", "n")
    assert f.focused_pane == p3 and f.tab_of(p3) == f.focused_tab
    f.focused_tab, f.focused_pane = t2, p2
    f.call("pane", "move", p1, "--new-tab", "--label", "m", "--no-focus")
    assert f.focused_pane == p2


def test_unknown_workspace_is_rejected():
    f = FakeHerdr()
    a = f.add_workspace("a")
    _, (p1, p2) = f.add_tab(a, "x", 2)
    with pytest.raises(herdr.HerdrError):
        f.call("tab", "create", "--workspace", "nope", "--label", "L", "--no-focus")
    with pytest.raises(herdr.HerdrError):
        f.call("pane", "move", p2, "--new-tab", "--workspace", "nope", "--no-focus")
