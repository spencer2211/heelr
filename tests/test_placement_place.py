# tests/test_placement_place.py
import pytest

from heelr import core, facts, herdr, key, place, sleep
from fakeherdr import FakeHerdr
from test_placement_facts import make_repo, make_worktree  # noqa: F401

ROLES = [{"name": "agent", "agent": True}, {"name": "shell", "fallback": True}]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    f = FakeHerdr()
    f.version = "0.9.3"
    monkeypatch.setattr(herdr, "call", f.call)
    monkeypatch.setattr(herdr, "run_in", f.run_in)
    return f


def cfg(**kw):
    return key.PlacementConfig(enabled=True, ticket=r"GH-\d+", **kw)


def test_unsorted_has_no_destination():
    assert place.dest_for(key.Key("shell", "x", key.UNSORTED), cfg()) is None


def test_templates():
    k = key.Key("agent", "heelr", "GH-1")
    assert place.dest_for(k, cfg()) == place.Dest("heelr", "GH-1")
    assert place.dest_for(k, cfg(tab="{repo}", workspace="all")) == place.Dest("all", "heelr")


def test_drift_lists_only_misplaced(fake, tmp_path):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = fake.add_workspace("heelr")
    t1, (a,) = fake.add_tab(w, "GH-1", 1, cwd=str(wt))
    t2, (b,) = fake.add_tab(w, "main", 1, cwd=str(wt))           # wrong tab
    d = place.drifted(fake.snapshot(), cfg(), ROLES)
    assert d == {b: place.Dest("heelr", "GH-1")}


def test_sleeping_panes_never_drift(fake, tmp_path):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = fake.add_workspace("heelr")
    fake.add_tab(w, "GH-1", 1, cwd=str(wt))
    t2, (b,) = fake.add_tab(w, core.SLEEP_MARK + "main", 1, cwd=str(wt))
    assert place.drifted(fake.snapshot(), cfg(), ROLES) == {}


def test_move_into_existing_user_tab_keeps_its_label(fake, tmp_path):     # Review Focus 2
    w = fake.add_workspace("heelr")
    t1, (a,) = fake.add_tab(w, "GH-1", 1)
    t2, (b, b2) = fake.add_tab(w, "other", 2)
    place.execute(fake.snapshot(), [place.Move(b2, place.Dest("heelr", "GH-1"), False)], cfg())
    assert fake.tabs[t1]["label"] == "GH-1" and len(fake.panes(t1)) == 2
    assert fake.labels().count("GH-1") == 1


def test_creates_missing_tab_with_final_label_without_focus(fake):
    w = fake.add_workspace("heelr")
    t1, (a, b) = fake.add_tab(w, "main", 2)
    before = (fake.focused_tab, fake.focused_pane)
    place.execute(fake.snapshot(), [place.Move(b, place.Dest("heelr", "GH-2"), False)], cfg())
    assert "GH-2" in fake.labels()
    assert (fake.focused_tab, fake.focused_pane) == before
    assert any("--no-focus" in c for c in map(" ".join, fake.calls) if "pane move" in c)


def test_cross_workspace_move_rekeys_everything(fake):                     # Review Focus 3
    a = fake.add_workspace("repo-a")
    b = fake.add_workspace("repo-b")
    ta, (p1, p2) = fake.add_tab(a, "main", 2)
    tb, (q,) = fake.add_tab(b, "GH-3", 1)
    facts.set_tag(p2, "GH-3")
    undo = place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-b", "GH-3"), False)], cfg())
    new = undo[0]["pane"]
    assert new != p2 and new in fake.panes(tb)
    assert facts.load_pane(new)["tag"] == "GH-3" and not facts._pane_path(p2).exists()
    assert new in place.own_panes()


def test_creates_missing_workspace_and_closes_its_idle_shell(fake):
    a = fake.add_workspace("repo-a")
    ta, (p1, p2) = fake.add_tab(a, "main", 2)
    undo = place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-new", "GH-4"), False)], cfg())
    ws = next(w for w, l in fake.workspaces.items() if l == "repo-new")
    tabs = [t for t, v in fake.tabs.items() if v["ws"] == ws]
    assert [fake.tabs[t]["label"] for t in tabs] == ["GH-4"]
    assert undo[0]["created_ws"] is True


def test_no_workspace_creation_when_disallowed(fake):
    a = fake.add_workspace("repo-a")
    ta, (p1, p2) = fake.add_tab(a, "main", 2)
    place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-new", "GH-4"), False)],
                  cfg(create_workspaces=False))
    assert set(fake.workspaces.values()) == {"repo-a"}
    assert any(fake.tabs[t]["label"] == "GH-4" and fake.tabs[t]["ws"] == a for t in fake.tabs)


def test_clash_with_heelr_tab_of_another_key_is_qualified(fake):
    w = fake.add_workspace("all")
    t1, (a, b) = fake.add_tab(w, "main", 2)
    place.execute(fake.snapshot(), [place.Move(b, place.Dest("all", "GH-1"), False)], cfg(), key_of={b: key.Key("shell", "r1", "GH-1")})
    t3, (c,) = fake.add_tab(w, "x", 1)
    place.execute(fake.snapshot(), [place.Move(c, place.Dest("all", "GH-1"), False)], cfg(), key_of={c: key.Key("shell", "r2", "GH-1")})
    assert "GH-1 · r2" in fake.labels()


def test_back_restores_and_skips_closed(fake):
    w = fake.add_workspace("heelr")
    t1, (a, b, c) = fake.add_tab(w, "main", 3)
    undo = place.execute(fake.snapshot(), [place.Move(b, place.Dest("heelr", "GH-1"), False),
                                           place.Move(c, place.Dest("heelr", "GH-2"), False)], cfg())
    place.save_undo(undo)
    fake.call("pane", "close", c)
    lines = []
    assert place.back(log=lines.append) == 0
    assert b in fake.panes(t1)
    assert any("skipped" in l and "closed" in l for l in lines)


def test_pull_style_exact_puts_two_repos_in_one_new_tab(fake):        # reviewer item 4
    w = fake.add_workspace("ra")
    ta, (a, b) = fake.add_tab(w, "main", 2)
    keys = {a: key.Key("shell", "ra", "GH-5"), b: key.Key("shell", "rb", "GH-5")}
    place.execute(fake.snapshot(), [place.Move(a, place.Dest("ra", "GH-5"), False),
                                    place.Move(b, place.Dest("ra", "GH-5"), False)],
                  cfg(), key_of=keys, exact=True)
    assert fake.labels().count("GH-5") == 1 and "GH-5 · rb" not in fake.labels()


def test_apply_created_panes_are_own(fake):
    from test_apply_safety import heelr
    w = fake.add_workspace("x")
    t, (a,) = fake.add_tab(w, "main", 1, agents=(0,))
    heelr("apply", "agents-logs", "--tab", t)                    # adds a shell for the bottom row
    created = [p for p in fake.panes(t) if p != a]
    assert created and set(created) <= place.own_panes()


def test_drift_qualifier_must_match_the_repo(fake, tmp_path):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = fake.add_workspace("heelr")
    t1, (a,) = fake.add_tab(w, "GH-1 · other", 1, cwd=str(wt))
    assert a in place.drifted(fake.snapshot(), cfg(), ROLES)


def test_own_panes_are_remembered(fake):
    w = fake.add_workspace("heelr")
    t1, (a, b) = fake.add_tab(w, "main", 2)
    undo = place.execute(fake.snapshot(), [place.Move(b, place.Dest("heelr", "GH-1"), False)], cfg())
    assert undo[0]["pane"] in place.own_panes()


def test_sleeping_panes_from_label_and_record(fake):
    w = fake.add_workspace("heelr")
    t1, (a,) = fake.add_tab(w, core.SLEEP_MARK + "GH-1", 1)
    t2, (b, c) = fake.add_tab(w, "main", 2)
    sleep.save_record({"tab": t2, "panes": [{"pane": c, "session": "s", "status": "slept"},
                                            {"pane": b, "session": "s", "status": "woken"}]})
    assert sleep.sleeping_panes(fake.snapshot()) == {a, c}


def test_never_placed_into_a_sleeping_tab(fake):
    w = fake.add_workspace("heelr")
    t1, (a,) = fake.add_tab(w, core.SLEEP_MARK + "GH-1", 1)
    t2, (b, c) = fake.add_tab(w, "main", 2)
    lines = []
    undo = place.execute(fake.snapshot(), [place.Move(c, place.Dest("heelr", "GH-1"), False)], cfg(),
                         log=lines.append)
    assert fake.panes(t1) == [a] and undo == [] and c in fake.panes(t2)
    assert fake.labels().count("GH-1") == 0 and any("skipped" in l and "asleep" in l for l in lines)


def test_failure_mid_plan_keeps_done_moves_and_back_undoes_them(fake):          # F1
    w = fake.add_workspace("heelr")
    t1, (a, b, c) = fake.add_tab(w, "main", 3)
    fake.fail_at = 2
    lines = []
    undo = place.execute(fake.snapshot(), [place.Move(p, place.Dest("heelr", f"GH-{i}"), False)
                                           for i, p in enumerate((a, b, c), 1)], cfg(), log=lines.append)
    assert len(undo) == 1 and any("stopped after 1/3" in l for l in lines)
    place.save_undo(undo)
    assert place.back(log=lines.append) == 0
    assert a in fake.panes(t1)


def test_failing_initial_shell_close_keeps_the_entry(fake):                     # F1
    a = fake.add_workspace("repo-a")
    ta, (p1, p2) = fake.add_tab(a, "main", 2)
    fake.fail_requests = {("pane", "close")}
    undo = place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-new", "GH-4"), False)], cfg())
    assert len(undo) == 1 and undo[0]["created_ws"] is True


def test_back_rebuilds_a_source_tab_herdr_closed(fake):                          # F2
    w = fake.add_workspace("heelr")
    t0, _ = fake.add_tab(w, "other", 1)
    t1, (a, b) = fake.add_tab(w, "main", 2)
    undo = place.execute(fake.snapshot(), [place.Move(a, place.Dest("heelr", "GH-1"), False),
                                           place.Move(b, place.Dest("heelr", "GH-2"), False)], cfg())
    assert t1 not in fake.tabs
    place.save_undo(undo)
    assert place.back() == 0
    mains = [t for t, v in fake.tabs.items() if v["label"] == "main"]
    assert len(mains) == 1 and set(fake.panes(mains[0])) == {a, b}


def test_missing_workspace_not_creatable_is_not_drift(fake, tmp_path):          # F3
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = fake.add_workspace("heelr")
    t1, (a,) = fake.add_tab(w, "GH-1", 1, cwd=str(wt))
    t2, (b,) = fake.add_tab(w, "main", 1, cwd=str(wt))
    c = cfg(workspace="elsewhere", create_workspaces=False)
    assert place.drifted(fake.snapshot(), c, ROLES) == {b: place.Dest("elsewhere", "GH-1")}


def test_bad_templates_fall_back_to_defaults(tmp_path):                          # F4
    for bad in ('"{role}"', '"{repo"', '"{0}"', '"{repo!z}"'):
        f = tmp_path / "p.toml"
        f.write_text(f"[placement]\nenabled = true\nworkspace = {bad}\ntab = {bad}\n")
        c = key.load_config(f)
        assert c.enabled and (c.workspace, c.tab) == ("{repo}", "{task}")


def test_dest_for_never_raises_and_ignores_role():                               # F4
    bad = key.PlacementConfig(enabled=True, workspace="{nope}", tab="{role}")
    assert place.dest_for(key.Key("agent", "r", "GH-1"), bad) == place.Dest("r", "GH-1")
    c = cfg(tab="{task}")
    assert place.dest_for(key.Key("agent", "r", "GH-1"), c) == place.dest_for(key.Key("shell", "r", "GH-1"), c)


def test_second_move_of_a_pane_in_one_plan_is_ignored(fake):                     # F6
    w = fake.add_workspace("heelr")
    t1, (a, b) = fake.add_tab(w, "main", 2)
    undo = place.execute(fake.snapshot(), [place.Move(b, place.Dest("heelr", "GH-1"), False),
                                           place.Move(b, place.Dest("heelr", "GH-2"), False)], cfg())
    assert len(undo) == 1 and "GH-2" not in fake.labels()


def test_created_tab_records_no_invented_key(fake):                              # F6
    w = fake.add_workspace("heelr")
    t1, (a, b) = fake.add_tab(w, "main", 2)
    undo = place.execute(fake.snapshot(), [place.Move(b, place.Dest("heelr", "GH-1"), False)], cfg())
    assert place._heelr_tabs()[undo[0]["to_tab"]] == {}


def test_busy_initial_shell_is_kept(fake, monkeypatch):                                       # F7
    a = fake.add_workspace("repo-a")
    ta, (p1, p2) = fake.add_tab(a, "main", 2)
    real = fake.call

    def call(*args, **kw):
        r = real(*args, **kw)
        if args[:2] == ("workspace", "create"):
            fake.busy(r["root_pane"]["pane_id"])
        return r
    monkeypatch.setattr(herdr, "call", call)
    place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-new", "GH-4"), False)], cfg())
    ws = next(w for w, l in fake.workspaces.items() if l == "repo-new")
    assert len([t for t, v in fake.tabs.items() if v["ws"] == ws]) == 2


def test_user_tab_wins_over_an_earlier_heelr_clash_tab(fake):                    # F7
    w = fake.add_workspace("all")
    t1, (a, b, c) = fake.add_tab(w, "main", 3)
    place.execute(fake.snapshot(), [place.Move(b, place.Dest("all", "GH-1"), False)], cfg(),
                  key_of={b: key.Key("shell", "r1", "GH-1")})
    tu, (u,) = fake.add_tab(w, "GH-1", 1)                  # the user's own, listed after heelr's
    place.execute(fake.snapshot(), [place.Move(c, place.Dest("all", "GH-1"), False)], cfg(),
                  key_of={c: key.Key("shell", "r2", "GH-1")})
    assert c in fake.panes(tu) and "GH-1 · r2" not in fake.labels()


def test_created_tab_gets_its_shape_and_auto_tile_uses_it(fake, tmp_path, monkeypatch):         # G4a
    from heelr import cli
    (tmp_path / "cfg/heelr").mkdir(parents=True)
    (tmp_path / "cfg/heelr/config.toml").write_text("auto_tile = true\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    a = fake.add_workspace("repo-a")
    ta, (p1, p2, p3) = fake.add_tab(a, "main", 3)
    c = cfg(shape_default="grid", shape_by_tab={"GH-3": "main-right"})
    u = place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-a", "GH-3"), False),
                                        place.Move(p3, place.Dest("repo-a", "other"), False)], c)
    t3, tother = u[0]["to_tab"], u[1]["to_tab"]
    assert core.tab_record(t3) == {"layout": "main-right", "panes": [p2]}      # shape_by_tab
    assert core.tab_record(tother) == {"layout": "grid", "panes": [p3]}        # shape_default
    ta2 = u[0]["from_tab"]
    place.execute(fake.snapshot(), [place.Move(p1, place.Dest("repo-a", "GH-3"), False)], c)
    assert core.tab_record(t3)["panes"] == [p2]                                # not laid out yet
    assert cli.main(["--allow-untested", "auto"]) == 0
    assert set(core.tab_record(t3)["panes"]) == {p1, p2} and core.tab_record(t3)["layout"] == "main-right"
    assert len(core.tab_record(t3)["panes"]) == 2


def test_an_existing_tab_keeps_its_record(fake):                                                # G4a
    a = fake.add_workspace("repo-a")
    ta, (p1, p2) = fake.add_tab(a, "main", 2)
    tb, (q,) = fake.add_tab(a, "GH-3", 1)
    core.update_tab_record(tb, layout="grid", panes=[q])
    place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-a", "GH-3"), False)], cfg(shape_by_tab={"GH-3": "main-right"}))
    assert core.tab_record(tb) == {"layout": "grid", "panes": [q]}
