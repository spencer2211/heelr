import json

import pytest

from heelr import burst, cli, core, key, place
from test_placement_place import fake, cfg  # noqa: F401
from test_placement_facts import make_repo, make_worktree


def test_single_and_burst_decisions(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    burst.note("a", 100.0)
    assert burst.decide("a", 0.75, 100.8) == "single"
    burst.note("b", 200.0)
    burst.note("c", 200.2)
    burst.note("d", 200.4)
    assert burst.decide("b", 0.75, 201.2) == "burst-wait"
    assert burst.decide("d", 0.75, 201.2) == "burst-last"
    assert burst.burst_panes("d") == ["b", "c", "d"]


def test_restore_one_second_apart_is_a_burst(tmp_path, monkeypatch):   # reviewer item 3
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    burst.note("a", 300.0)
    burst.note("b", 301.0)
    burst.note("c", 302.0)
    assert burst.decide("b", 0.75, 302.5) == "burst-wait"
    assert burst.decide("c", 0.75, 302.8) == "burst-last"   # every pane after the first moves without focus


def event(pane):
    return {"HERDR_PLUGIN_EVENT": "pane.created",
            "HERDR_PLUGIN_EVENT_JSON": json.dumps({"event": "pane_created", "data": {"pane": {"pane_id": pane}}})}


@pytest.fixture
def on(fake, tmp_path, monkeypatch):
    (tmp_path / "cfg").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "cfg/heelr").mkdir()
    (tmp_path / "cfg/heelr/placement.toml").write_text('[placement]\nenabled = true\nticket = \'GH-\\d+\'\n')
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return fake


def test_off_by_default_moves_nothing(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nocfg"))
    r = make_repo(tmp_path / "heelr")
    w = fake.add_workspace("heelr")
    t1, (a, b) = fake.add_tab(w, "other", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    before = fake.snapshot()["panes"]
    cli.main(["--allow-untested", "auto"])
    assert fake.snapshot()["panes"] == before


def test_single_new_pane_moves_with_focus(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(wt))
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    on.focused_pane = b                               # herdr focuses the pane you just split
    cli.main(["--allow-untested", "auto"])
    tab = next(t for t in on.tabs if on.tabs[t]["label"] == "GH-1")
    assert on.focused_tab == tab


def test_background_unfocused_new_pane_is_placed_without_stealing_focus(on, tmp_path, monkeypatch):   # G2
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(wt))
    on.focused_pane = a                               # b was made by a script, focus stayed on a
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    tab = next(t for t in on.tabs if on.tabs[t]["label"] == "GH-1")
    assert on.tab_of(b) == tab
    assert on.focused_tab == t1 and on.focused_pane == a


def test_untested_herdr_version_blocks_background_placement(on, tmp_path, monkeypatch):    # G1
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    on.version = "9.9.9"
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    before = on.snapshot()["panes"]
    for _ in range(2):
        assert cli.main(["auto"]) == 0
    assert on.snapshot()["panes"] == before
    assert "untested" in _log(tmp_path)
    assert sum("placement" in n for n in on.notifications) == 1
    cli.main(["--allow-untested", "auto"])            # the flag lets it through
    assert on.tab_of(b) != t1


def test_lock_timeout_in_the_hook_is_logged_quietly(on, tmp_path, monkeypatch):            # G9
    import fcntl
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "AUTO_WAIT", 0.1)
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    before = on.snapshot()["panes"]
    with open(core.state_dir() / "apply.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        assert cli.main(["--allow-untested", "auto"]) == 0
    assert on.snapshot()["panes"] == before
    assert "another layout change is running" in _log(tmp_path)
    assert "error" not in _log(tmp_path)
    assert not any("placement" in n for n in on.notifications)


def test_burst_moves_without_focus(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b, c) = on.add_tab(w, "main", 3, cwd=str(wt))
    burst.note(b, 1000.0)
    clock = [1000.2]                                  # the hook's burst_window sleep advances the clock
    monkeypatch.setattr(cli.time, "time", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    for k, v in event(c).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert on.focused_tab == t1
    tab = next(t for t in on.tabs if on.tabs[t]["label"] == "GH-1")
    assert {b, c} <= set(on.panes(tab))


def test_burst_never_moves_the_focused_pane(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b, c) = on.add_tab(w, "main", 3, cwd=str(wt))
    on.focused_pane = c                               # herdr focuses the newest split
    burst.note(b, 1000.0)
    clock = [1000.2]
    monkeypatch.setattr(cli.time, "time", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    for k, v in event(c).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert on.tab_of(c) == t1 and on.focused_pane == c
    assert on.tab_of(b) != t1


def test_long_chain_places_every_pane(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    for i in range(30):
        burst.note(f"p{i}", 100.0 + 0.4 * i)
    last = 100.0 + 0.4 * 29
    assert burst.decide("p29", 0.75, last + 0.8) == "burst-last"
    assert burst.burst_panes("p29") == [f"p{i}" for i in range(30)]


def test_old_groups_are_dropped_but_the_live_chain_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    burst.note("old", 100.0)
    burst.note("x", 200.0)
    burst.note("y", 200.5)
    assert burst.burst_panes("old") == []
    assert burst.burst_panes("y") == ["x", "y"]


def _fail_drift(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(place, "drifted", boom)


def test_a_lasting_placement_error_notifies_once(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b, c) = on.add_tab(w, "main", 3, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    _fail_drift(monkeypatch)
    for pane in (b, c):
        for k, v in event(pane).items():
            monkeypatch.setenv(k, v)
        cli.main(["--allow-untested", "auto"])
    assert sum("placement" in n for n in on.notifications) == 1


def test_placement_error_notifies_again_after_a_clean_run(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b, c) = on.add_tab(w, "main", 3, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    real = place.drifted
    _fail_drift(monkeypatch)
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    monkeypatch.setattr(place, "drifted", real)
    cli.main(["--allow-untested", "auto"])            # clean: recovers
    _fail_drift(monkeypatch)
    monkeypatch.setattr(cli.time, "time", lambda: 5000.0)   # c is well apart from b's burst
    for k, v in event(c).items():                     # b is heelr's own by now; c is a new pane
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert sum("placement" in n for n in on.notifications) == 2


def test_a_tab_with_auto_tile_off_is_not_placed(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(wt))
    core.update_tab_record(t1, auto=False)
    assert place.drifted(on.snapshot(), key.load_config(), []) == {}
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert on.labels() == ["main"]


def test_auto_placement_leaves_the_undo_record_alone(on, tmp_path, monkeypatch):   # reviewer item 7
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert not (core.state_dir() / "place-back.json").exists()


def test_pane_created_by_heelr_under_the_lock_is_not_placed(on, tmp_path, monkeypatch):
    """core.run creates a pane while holding apply_lock; herdr's pane.created hook fires
    before run has noted the pane as its own. The hook (a thread: lock depth is per
    thread, flock per open file) passes the unlocked own-pane check, is provably blocked
    on apply_lock while apply holds it, and only then does apply note the pane. The
    re-check under the lock must skip it."""
    import threading
    from test_apply_safety import heelr
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a,) = on.add_tab(w, "main", 1, cwd=str(wt))
    blocked = threading.Event()
    hook_thread = []
    real_flock, real_note = core._flock_until, place._note_own

    def flock_spy(f, deadline):
        if hook_thread and threading.current_thread() is hook_thread[0]:
            blocked.set()                      # the hook is waiting for the lock apply holds
        return real_flock(f, deadline)
    monkeypatch.setattr(core, "_flock_until", flock_spy)
    seen = {}

    def run():
        for k, v in event(seen["new"]).items():
            monkeypatch.setenv(k, v)
        cli.main(["--allow-untested", "auto"])

    def note_after_hook_blocks(pane):
        if not hook_thread:                    # the first pane run created: the hook races it
            seen["new"] = pane
            on.cwd[pane] = str(wt)             # a pane the hook would place if it got the chance
            t = threading.Thread(target=run)
            hook_thread.append(t)
            t.start()
            assert blocked.wait(5), "hook never reached apply_lock"
            t.join(0.3)
            seen["alive_while_held"] = t.is_alive()
        real_note(pane)
    monkeypatch.setattr(place, "_note_own", note_after_hook_blocks)
    heelr("apply", "main-left", "--tab", t1)
    assert seen["alive_while_held"] is True        # genuinely concurrent: blocked while apply held the lock
    hook_thread[0].join(10)
    assert not hook_thread[0].is_alive()
    assert "GH-1" not in on.labels()
    assert seen["new"] in on.all_panes()


def test_user_pane_right_after_an_auto_tile_run_is_placed(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    with core.apply_lock():
        pass                                     # an unrelated auto-tile run just finished
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert "GH-1" in on.labels()


def test_heelr_own_panes_are_ignored(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    place._note_own(b)
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert "GH-1" not in on.labels()


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"data": {}}', '{"data": {"pane": {"pane_id": 5}}}'])
def test_malformed_event_json_is_ignored(on, monkeypatch, raw):
    monkeypatch.setenv("HERDR_PLUGIN_EVENT", "pane.created")
    monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", raw)
    assert cli._event_pane() is None
    assert cli.main(["--allow-untested", "auto"]) == 0


def _log(tmp_path):
    p = tmp_path / "state" / "heelr" / "placement.log"
    return p.read_text() if p.exists() else ""


def test_a_failed_move_in_the_hook_is_logged_and_notified_once(on, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b, c) = on.add_tab(w, "main", 3, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    on.fail_requests.add(("pane", "move"))
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    for now in (1000.0, 5000.0):                  # the same pane fails the same way twice
        monkeypatch.setattr(cli.time, "time", lambda now=now: now)
        assert cli.main(["--allow-untested", "auto"]) == 0
    log = _log(tmp_path)
    assert "stopped after 0/1 moves" in log and "injected failure" in log
    assert sum("placement" in n for n in on.notifications) == 1       # same lasting error: once


def test_a_failed_workspace_create_in_the_hook_is_logged_and_notified(on, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    r = make_repo(tmp_path / "newrepo")
    w = on.add_workspace("scratch")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-2", "GH-2")))
    on.fail_requests.add(("workspace", "create"))
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert "stopped after 0/1 moves" in _log(tmp_path)
    assert sum("placement" in n for n in on.notifications) == 1


def test_a_skipped_move_in_the_hook_is_logged_without_a_notification(on, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    on.add_tab(w, core.SLEEP_MARK + "GH-1", 1, cwd=str(tmp_path / "heelr/.worktrees/GH-1"))   # only match is asleep
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert "is asleep" in _log(tmp_path)
    assert not any("placement" in n for n in on.notifications)


def _ev(name, data, monkeypatch):
    monkeypatch.setenv("HERDR_PLUGIN_EVENT", name)
    monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", json.dumps({"event": name.replace(".", "_"), "data": data}))


def test_pane_moved_event_rekeys_the_pane_store(on, monkeypatch):                    # G4b
    from heelr import facts
    w1, w2 = on.add_workspace("a"), on.add_workspace("b")
    on.add_tab(w1, "main", 1)
    facts.set_tag("w1:p7", "GH-5")
    _ev("pane.moved", {"previous_pane_id": "w1:p7", "previous_workspace_id": w1,
                       "previous_tab_id": "t1", "pane": {"pane_id": "w2:p5"}, "created_tab": False}, monkeypatch)
    assert cli.main(["--allow-untested", "auto"]) == 0
    assert facts.load_pane("w2:p5")["tag"] == "GH-5" and not facts._pane_path("w1:p7").exists()
    assert facts.resolve("w1:p7") == "w2:p5"


@pytest.mark.parametrize("data", [{}, {"pane": {"pane_id": "w2:p5"}}, {"previous_pane_id": 7, "pane": {"pane_id": "x"}},
                                  {"previous_pane_id": "w1:p7", "pane": "w2:p5"}, "junk", None,
                                  {"previous_pane_id": "w1:p7", "pane": {"pane_id": "w1:p7"}}])
def test_malformed_or_same_id_moved_payloads_change_nothing(on, monkeypatch, data):  # G4b
    from heelr import facts
    facts.set_tag("w1:p7", "GH-5")
    _ev("pane.moved", data, monkeypatch)
    assert cli.main(["--allow-untested", "auto"]) == 0
    assert facts.load_pane("w1:p7")["tag"] == "GH-5" and not facts._aliases()


def test_pane_closed_event_forgets_the_pane(on, monkeypatch):                         # G4b
    from heelr import facts
    facts.set_tag("w2:p5", "GH-5")
    facts.rekey("w1:p7", "w2:p5")
    for data in ({"pane": {"pane_id": "w2:p5"}}, {"pane_id": "w2:p5"}):
        facts.set_tag("w2:p5", "GH-5")
        _ev("pane.closed", data, monkeypatch)
        assert cli.main(["--allow-untested", "auto"]) == 0
        assert not facts._pane_path("w2:p5").exists() and facts._aliases() == {}
        facts.rekey("w1:p7", "w2:p5")
    _ev("pane.closed", {"pane": {}}, monkeypatch)
    assert cli.main(["--allow-untested", "auto"]) == 0


def test_pane_created_drops_a_stale_alias_for_a_reused_id(on, monkeypatch):           # G4b
    from heelr import facts
    facts.rekey("w1:p7", "w2:p5")
    for k, v in event("w1:p7").items():           # herdr hands w1:p7 to a brand new pane
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert facts.resolve("w1:p7") == "w1:p7"


@pytest.mark.parametrize("name,data", [
    ("pane.moved", {"previous_pane_id": "w1:p7", "pane": {"pane_id": "w2:p5"}}),
    ("pane.closed", {"pane": {"pane_id": "w2:p5"}}),
    ("pane.created", {"pane": {"pane_id": "w1:p7"}})])
def test_pane_events_with_placement_off_take_no_lock_and_write_nothing(on, tmp_path, monkeypatch, name, data):   # P2
    from heelr import facts
    (tmp_path / "cfg/heelr/placement.toml").write_text("[placement]\nenabled = false\n")
    facts.set_tag("w1:p7", "GH-5")
    before = {p: p.read_bytes() for p in core.state_dir().rglob("*") if p.is_file()}

    def no_lock(*a, **k):
        raise AssertionError("placement is off: the hook must not take the lock")
    monkeypatch.setattr(core, "apply_lock", no_lock)
    _ev(name, data, monkeypatch)
    assert cli.main(["--allow-untested", "auto"]) == 0
    assert {p: p.read_bytes() for p in core.state_dir().rglob("*") if p.is_file()} == before
