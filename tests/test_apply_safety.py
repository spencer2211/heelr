"""Offline tests of apply / back / pick end to end, against an in-memory Herdr.

Nothing here reaches a real server: `herdr.call` and `herdr.run_in` are the
fake, any real herdr/tmux subprocess fails the test, and state and config
live in a temp dir.
"""
import subprocess

import pytest

from fakeherdr import FakeHerdr
from heelr import cli, core, herdr
from heelr.tree import same_structure

REAL_CALL = herdr.call   # the JSON-parsing wrapper; only ever fed canned output here


@pytest.fixture(autouse=True)
def fake(tmp_path, monkeypatch):
    real_run, real_popen = subprocess.run, subprocess.Popen

    def guard(real):
        def wrapped(args, *a, **kw):
            argv = [args] if isinstance(args, str) else list(args)
            if any(("herdr" in str(x) or "tmux" in str(x)) for x in argv[:1]):
                raise AssertionError(f"real herdr/tmux call attempted: {argv}")
            return real(args, *a, **kw)
        return wrapped

    monkeypatch.setattr(subprocess, "run", guard(real_run))
    monkeypatch.setattr(subprocess, "Popen", guard(real_popen))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HERDR_BIN_PATH", "/nonexistent/herdr-must-not-run")
    monkeypatch.delenv("HERDR_ACTIVE_TAB_ID", raising=False)
    f = FakeHerdr()
    monkeypatch.setattr(herdr, "call", f.call)
    monkeypatch.setattr(herdr, "run_in", f.run_in)
    return f


def heelr(*args) -> int:
    return cli.main(["--allow-untested", *args])


FLEET = ('name = "fleet"\nkind = "multi"\n[[tabs]]\nlabel = "agents"\ngather = ["agent"]\n'
         '[tabs.layout]\nkind = "rule"\nrule = "even-columns"\n')


def write_layout(name: str, text: str) -> None:
    d = core.config_dir() / "layouts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(text)


# ------------------------------------------------------------------ harness

def test_harness_apply_then_back_round_trips(fake):
    tab, ids = fake.add_tab("w1", "main", 3, d="down")
    before = fake.tree(tab)
    assert heelr("apply", "main-left", "--tab", tab) == 0
    assert fake.tree(tab).dir == "right" and "heelr-park" not in fake.labels()
    assert heelr("back", "--tab", tab) == 0
    assert same_structure(before, fake.tree(tab), tol=0.02) == []


def test_harness_add_missing_then_back_closes_the_new_pane(fake):
    tab, ids = fake.add_tab("w1", "main", 1)
    assert heelr("apply", "main-left", "--tab", tab) == 0
    assert len(fake.panes(tab)) == 2
    assert heelr("back", "--tab", tab) == 0
    assert fake.panes(tab) == ids


def test_harness_closing_the_focused_tab_moves_focus_to_a_neighbour():
    f = FakeHerdr()
    t1, (a,) = f.add_tab("w1", "one", 1)
    t2, (b,) = f.add_tab("w1", "two", 1)
    f.focused_tab, f.focused_pane = t2, b
    f.call("pane", "close", b)
    snap = f.snapshot()
    assert snap["focused_tab_id"] == t1 and snap["focused_pane_id"] == a


def test_harness_can_fail_close_layout_and_run_in():
    f = FakeHerdr()
    t1, (a, b) = f.add_tab("w1", "one", 2)
    f.fail_requests = {("pane", "close"), ("pane", "layout")}
    f.fail_run_in = True
    for req in (("pane", "close", b), ("pane", "layout", "--pane", a)):
        with pytest.raises(herdr.HerdrError):
            f.call(*req)
    with pytest.raises(herdr.HerdrError):
        f.run_in(a, "htop")
    assert f.panes(t1) == [a, b]


def test_harness_same_tab_move_is_a_noop():
    f = FakeHerdr()
    tab, (a, b) = f.add_tab("w1", "x", 2)
    assert f.call("pane", "move", b, "--tab", tab, "--split", "down", "--target-pane", a)["move_result"] == \
        {"changed": False, "reason": "same_tab"}


# ---------------------------------------------------------- B1: the lock

def lock_held() -> bool:
    import fcntl
    with open(core.state_dir() / "apply.lock", "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False


def test_refused_apply_keeps_the_undo_record(fake):
    import fcntl
    tab, _ = fake.add_tab("w1", "main", 3, d="down")
    assert heelr("apply", "main-left", "--tab", tab) == 0
    record = core._back_path(tab).read_text()
    with open(core.state_dir() / "apply.lock", "w") as other:   # another apply is running
        fcntl.flock(other, fcntl.LOCK_EX)
        assert heelr("apply", "grid", "--tab", tab) == 1
    assert core._back_path(tab).read_text() == record


def test_tab_apply_holds_the_lock_through_save_verify_and_created(fake, monkeypatch):
    tab, _ = fake.add_tab("w1", "main", 1)
    seen = []
    fake.on_call = lambda args: seen.append((args[:2], lock_held())) if args[:2] in (
        ("pane", "move"), ("pane", "split"), ("pane", "layout")) else None
    for name in ("save_back", "add_created"):
        real = getattr(core, name)
        monkeypatch.setattr(core, name, lambda *a, _r=real, _n=name: (seen.append((_n, lock_held())), _r(*a))[1])
    assert heelr("apply", "main-left", "--tab", tab) == 0
    kinds = [k for k, _ in seen]
    assert "save_back" in kinds and ("pane", "layout") in kinds and "add_created" in kinds
    assert all(held for _, held in seen), seen


def test_tab_back_holds_the_lock_through_close_and_restore(fake):
    tab, _ = fake.add_tab("w1", "main", 2, d="down")
    assert heelr("apply", "main-left", "--tab", tab) == 0
    new = fake.call("pane", "split", fake.panes(tab)[0], "--direction", "down", "--ratio", "0.5")
    seen = []
    fake.on_call = lambda args: seen.append((args, lock_held()))
    core.add_created(tab, [{"pane": new["pane"]["pane_id"], "command": None}])  # so back closes one
    assert heelr("back", "--tab", tab) == 0
    assert any(a[:2] == ("pane", "close") for a, _ in seen)
    assert any(a[:2] == ("pane", "move") for a, _ in seen)
    assert all(held for _, held in seen), [a for a, h in seen if not h]


def test_multi_apply_holds_one_lock_across_gather_and_layout(fake, monkeypatch):
    t1, _ = fake.add_tab("w1", "one", 2, agents=(0,))
    t2, _ = fake.add_tab("w1", "two", 2, agents=(1,))
    write_layout("fleet", FLEET)
    seen = []
    first = []

    def hook(args):
        if args[:2] == ("api", "snapshot") and not first:
            first.append(1)      # the read before planning may run unlocked
            return
        seen.append((args, lock_held()))
    fake.on_call = hook
    assert heelr("apply", "fleet", "--tab", t1) == 0
    assert any(a[:2] == ("pane", "move") for a, _ in seen)
    assert all(held for _, held in seen), [a for a, h in seen if not h]


# ------------------------------------------- B5: undo scope after a failure

def test_failed_tab_apply_still_makes_back_tab_scoped(fake):
    t1, _ = fake.add_tab("w1", "one", 2, agents=(0,))
    t2, _ = fake.add_tab("w1", "two", 3, d="down", agents=(1,))
    write_layout("fleet", FLEET)
    assert heelr("apply", "fleet", "--tab", t1) == 0
    assert core.get_last("w1") == "multi"
    tab = next(t for t, v in fake.tabs.items() if v["label"] == "agents")
    fake.fail_at = fake.mutations + 2          # cut the tab apply after its first move
    assert heelr("apply", "main-left", "--tab", tab) == 1
    assert core.get_last("w1") == "tab"        # prefix+u now undoes this tab's apply


def test_failed_multi_apply_still_makes_back_workspace_scoped(fake):
    t1, _ = fake.add_tab("w1", "one", 2, agents=(0,))
    fake.add_tab("w1", "two", 2, agents=(1,))
    write_layout("fleet", FLEET)
    assert heelr("apply", "main-top", "--tab", t1) == 0
    assert core.get_last("w1") == "tab"
    fake.fail_at = fake.mutations + 1
    assert heelr("apply", "fleet", "--tab", t1) == 1
    assert core.get_last("w1") == "multi"


# ------------------------------------------ back checks before it closes

def test_back_that_must_refuse_closes_nothing(fake):
    tab, (a,) = fake.add_tab("w1", "main", 1)
    assert heelr("apply", "main-left", "--tab", tab) == 0
    (new,) = set(fake.panes(tab)) - {a}
    fake.call("pane", "split", a, "--direction", "down", "--ratio", "0.5")  # a pane back doesn't know
    assert heelr("back", "--tab", tab) == 1
    assert new in fake.panes(tab)                  # the created pane is still there


# ------------------------------------------- B2: partial failure, then back

def _fresh(monkeypatch, tmp_path, i) -> FakeHerdr:
    f = FakeHerdr()
    monkeypatch.setattr(herdr, "call", f.call)
    monkeypatch.setattr(herdr, "run_in", f.run_in)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / f"state{i}"))
    return f


@pytest.mark.parametrize("name,n", [("main-right", 1), ("main-left", 1), ("two-mains", 1),
                                    ("main-bottom", 1), ("main-left", 3),
                                    # the anchor changes: back must not swap leaves (BI3)
                                    ("main-right", 2), ("main-right", 3),
                                    ("main-bottom", 2), ("main-bottom", 3),
                                    ("main-center", 1), ("main-center", 4)])
def test_back_recovers_from_an_apply_cut_at_any_step(monkeypatch, tmp_path, name, n):
    f = _fresh(monkeypatch, tmp_path, "count")
    tab, _ = f.add_tab("w1", "main", n, d="down")
    assert heelr("apply", name, "--tab", tab) == 0
    steps = f.mutations
    for k in range(1, steps + 1):
        f = _fresh(monkeypatch, tmp_path, k)
        tab, ids = f.add_tab("w1", "main", n, d="down")
        before = f.tree(tab)
        f.fail_at = k
        assert heelr("apply", name, "--tab", tab) == 1, k
        f.fail_at = None
        assert heelr("back", "--tab", tab) == 0, (k, f.tabs)
        assert tab in f.tabs and sorted(f.panes(tab)) == sorted(ids), (k, f.tabs)
        assert same_structure(before, f.tree(tab), tol=0.02) == [], k
        assert f.all_panes() == set(ids), (k, "created panes left behind")
        assert "heelr-park" not in f.labels(), k


def test_back_dry_run_after_a_cut_with_only_created_panes_left(fake, capsys):
    tab, (a,) = fake.add_tab("w1", "main", 1)
    fake.fail_at = 3                          # split $new0, park a, [move a back fails]
    assert heelr("apply", "main-right", "--tab", tab) == 1
    assert a not in fake.panes(tab) and "heelr-park" in fake.labels()
    fake.fail_at = None
    snapshot_before = fake.snapshot()
    assert heelr("back", "-n", "--tab", tab) == 0
    assert f"would move {a} back into the tab" in capsys.readouterr().out
    assert fake.snapshot() == snapshot_before


def test_multi_apply_cut_while_creating_records_the_new_panes(fake):
    import json
    t1, _ = fake.add_tab("w1", "one", 1, agents=(0,))
    write_layout("solo", 'name = "solo"\nkind = "multi"\n[[tabs]]\nlabel = "agents"\ngather = ["agent"]\n'
                         'layout = "main-right"\n')
    fake.fail_at = 3        # gather into a new tab, split $new0, [park fails]
    assert heelr("apply", "solo", "--tab", t1) == 3   # the tab is reported as not laid out
    created = [c["pane"] for c in json.loads(multi_back_path("w1").read_text())["created"]]
    assert len(created) == 1 and created[0] in fake.all_panes()


def multi_back_path(ws):
    from heelr import multi
    return multi._ws_back_path(ws)


# -------------------------------- B6: multi-tab back, a tab gained a pane

def test_multi_back_overflows_a_pane_opened_since_in_a_restored_tab(fake):
    t1, (a1, a2) = fake.add_tab("w1", "agents", 2, agents=(0, 1))
    t2, (s3, a4) = fake.add_tab("w1", "two", 2, agents=(1,))
    before = {t1: fake.tree(t1), t2: fake.tree(t2)}
    write_layout("fleet", FLEET)
    assert heelr("apply", "fleet", "--tab", t1) == 0
    assert set(fake.panes(t1)) == {a1, a2, a4}
    new = fake.call("pane", "split", a1, "--direction", "down", "--ratio", "0.5")["pane"]["pane_id"]
    assert heelr("back", "--tab", t1) == 0
    assert same_structure(before[t1], fake.tree(t1), tol=0.02) == []
    two = next(t for t, v in fake.tabs.items() if v["label"] == "two")
    assert same_structure(before[t2], fake.tree(two), tol=0.02) == []
    assert fake.tabs[fake.tab_of(new)]["label"] == "overflow"


# ------------------------------------------------ B7: errors in the menu

def test_pick_error_keeps_the_menu_open_with_the_reason_and_logs_it(fake, monkeypatch, capsys):
    tab, _ = fake.add_tab("w1", "main", 2)
    keys = iter(["1", "x"])                  # 1 = main-left, then "press any key"
    pressed = []
    monkeypatch.setattr(cli, "_getkey", lambda: pressed.append(1) or next(keys))
    fake.fail_at = 1
    assert heelr("pick", "--tab", tab) == 1
    out = capsys.readouterr().out
    assert len(pressed) == 2, "menu closed without waiting for a key"
    assert "injected failure" in out and "press any key" in out
    log = (core.state_dir() / "pick.log").read_text()
    assert "-> main-left rc=1" in log and "injected failure" in log
    assert fake.notifications == []          # shown in the menu, not as a notification


# ------------------------------------------------------- save: names

BAD_NAMES = ["", ".", "..", ".." + "/evil", "a" + "/b", ".hidden", "a\\b"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_save_rejects_names_that_are_not_a_plain_file_name(fake, tmp_path, name):
    tab, _ = fake.add_tab("w1", "main", 2)
    assert heelr("save", name, "--tab", tab) == 2
    assert not list(tmp_path.rglob("*.toml"))


def test_save_escapes_names_and_labels_for_toml(fake):
    import tomllib
    tab, _ = fake.add_tab("w1", 'my "tab" \\ x', 2)
    name = 'say "hi" there'
    assert heelr("save", name, "--tab", tab) == 0
    assert heelr("save", name + " all", "--tabs", "--tab", tab) == 0
    d = core.config_dir() / "layouts"
    assert tomllib.loads((d / f"{name}.toml").read_text())["name"] == name
    multi = tomllib.loads((d / f"{name} all.toml").read_text())
    assert multi["name"] == name + " all" and multi["tabs"][0]["label"] == 'my "tab" \\ x'


def test_toml_str_round_trips_awkward_text():
    import tomllib
    for text in ['plain', 'q"uote', 'back\\slash', 'tab\tnew\nline', 'del\x7f', 'ünï ✓']:
        assert tomllib.loads(f"x = {core.toml_str(text)}")["x"] == text


# ------------------------------------------- a field missing from Herdr

def test_a_missing_snapshot_field_is_a_clear_error(fake, monkeypatch, capsys):
    import json
    fake.add_tab("w1", "main", 2)
    snap = fake.snapshot()
    del snap["focused_tab_id"]

    def canned(argv, *a, **kw):          # stands in for the herdr binary; nothing is run
        if list(argv[1:3]) == ["notification", "show"]:
            return subprocess.CompletedProcess(argv, 0, '{"result": {}}', "")
        assert list(argv[1:3]) == ["api", "snapshot"], argv
        return subprocess.CompletedProcess(argv, 0, json.dumps({"result": {"snapshot": snap}}), "")
    monkeypatch.setattr(subprocess, "run", canned)
    monkeypatch.setattr(herdr, "call", REAL_CALL)
    assert heelr("apply", "grid") == 1
    err = capsys.readouterr().err
    assert "'focused_tab_id'" in err and "Herdr" in err, err


# ---------------- BI2: apply refuses while an earlier apply's park tab is open

def test_apply_after_a_cut_apply_refuses_and_back_still_recovers(fake, capsys):
    tab, ids = fake.add_tab("w1", "main", 1)
    fake.fail_at = 3                          # split $new0, park the pane, [fail]
    assert heelr("apply", "main-right", "--tab", tab) == 1
    fake.fail_at = None
    state = fake.snapshot()
    capsys.readouterr()
    assert heelr("apply", "main-left", "--tab", tab) == 1
    assert "heelr back" in capsys.readouterr().err
    assert fake.snapshot() == state           # nothing moved, undo record untouched
    assert heelr("back", "--tab", tab) == 0
    assert fake.panes(tab) == ids and "heelr-park" not in fake.labels()


def test_multi_apply_refuses_while_a_park_tab_is_open(fake, capsys):
    t1, _ = fake.add_tab("w1", "one", 2, agents=(0,))
    fake.add_tab("w1", "heelr-park", 1)
    write_layout("fleet", FLEET)
    state = fake.snapshot()
    assert heelr("apply", "fleet", "--tab", t1) == 1
    assert "heelr back" in capsys.readouterr().err
    assert fake.snapshot() == state


def test_park_tab_without_a_usable_back_record_says_how_to_get_out(fake, capsys):
    tab, _ = fake.add_tab("w1", "main", 2)
    fake.add_tab("w1", "heelr-park", 1)      # no undo record for it at all
    assert heelr("apply", "grid", "--tab", tab) == 1
    err = capsys.readouterr().err
    assert "heelr back" in err and "by hand" in err and "close the heelr-park tab" in err
    assert heelr("back", "--tab", tab) == 1  # back can't restore it either...
    err = capsys.readouterr().err
    assert "by hand" in err and "close the heelr-park tab" in err   # ...and says what to do


# ---------------------------------------------------------- write_atomic

def test_write_atomic_syncs_before_the_rename(monkeypatch, tmp_path):
    import os
    events = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda a, b: (events.append("replace"), real_replace(a, b))[1])
    core.write_atomic(tmp_path / "rec.json", "new")
    assert events[:2] == ["fsync", "replace"] and (tmp_path / "rec.json").read_text() == "new"


def test_write_atomic_leaves_no_tmp_file_when_the_write_fails(monkeypatch, tmp_path):
    import os
    (tmp_path / "rec.json").write_text("old")

    def boom(fd):
        raise OSError("disk full")
    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        core.write_atomic(tmp_path / "rec.json", "new")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rec.json"]
    assert (tmp_path / "rec.json").read_text() == "old"


def test_multi_back_that_must_refuse_closes_nothing(fake):
    t1, (a,) = fake.add_tab("w1", "one", 1, agents=(0,))
    write_layout("solo", 'name = "solo"\nkind = "multi"\n[[tabs]]\nlabel = "agents"\ngather = ["agent"]\n'
                         'layout = "main-left"\n')
    assert heelr("apply", "solo", "--tab", t1) == 0
    (new,) = fake.all_panes() - {a}
    fake.call("pane", "close", a)            # every recorded pane is gone: back can't restore
    assert heelr("back", "--tab", fake.tab_of(new)) == 1
    assert new in fake.all_panes()           # and so it closed nothing


def test_back_keeps_a_created_pane_whose_process_info_is_empty(fake, capsys):
    tab, (a,) = fake.add_tab("w1", "main", 1)
    assert heelr("apply", "main-left", "--tab", tab) == 0
    (new,) = set(fake.panes(tab)) - {a}
    fake.procs[new] = {"shell_pid": None, "foreground_process_group_id": None, "foreground_processes": []}
    assert heelr("back", "--tab", tab) == 0
    assert new in fake.all_panes() and fake.tabs[fake.tab_of(new)]["label"] == "overflow"
    assert f"keeping {new}" in capsys.readouterr().out


def test_pick_os_error_keeps_the_menu_open_and_is_logged(fake, monkeypatch, capsys):
    tab, _ = fake.add_tab("w1", "main", 2)
    keys = iter(["1", "x"])
    pressed = []
    monkeypatch.setattr(cli, "_getkey", lambda: pressed.append(1) or next(keys))

    def fail(args):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cli, "cmd_apply", fail)
    assert heelr("pick", "--tab", tab) == 1
    assert len(pressed) == 2 and "No space left" in capsys.readouterr().out
    assert "No space left" in (core.state_dir() / "pick.log").read_text()


# ------------------- failures outside the moves: close, verify, pane run

def test_back_whose_close_fails_can_be_run_again(fake):
    tab, ids = fake.add_tab("w1", "main", 1)
    assert heelr("apply", "main-left", "--tab", tab) == 0
    fake.fail_requests = {("pane", "close")}
    assert heelr("back", "--tab", tab) == 1
    fake.fail_requests = set()
    assert heelr("back", "--tab", tab) == 0
    assert fake.all_panes() == set(ids)


def test_apply_whose_verify_fails_can_be_undone(fake):
    tab, ids = fake.add_tab("w1", "main", 1)
    before = fake.tree(tab)
    fake.fail_requests = {("pane", "layout")}
    assert heelr("apply", "main-left", "--tab", tab) == 1
    fake.fail_requests = set()
    assert heelr("back", "--tab", tab) == 0
    assert fake.all_panes() == set(ids) and same_structure(before, fake.tree(tab)) == []


def test_apply_whose_new_pane_command_fails_can_be_undone(fake):
    tab, ids = fake.add_tab("w1", "main", 1)
    write_layout("watch", 'name = "watch"\nkind = "rule"\nrule = "main-left"\nadd_missing = true\n'
                          'new_pane_command = "htop"\n')
    fake.fail_run_in = True
    assert heelr("apply", "watch", "--tab", tab) == 1
    assert heelr("back", "--tab", tab) == 0
    assert fake.all_panes() == set(ids)


# ---------------------------------------------------------- a sleeping tab (`heelr sleep`: "💤 " + label)

def test_a_sleeping_park_tab_still_blocks_apply(fake, capsys):
    tab, _ = fake.add_tab("w1", "main", 2)
    fake.add_tab("w1", "💤 heelr-park", 1)
    state = fake.snapshot()
    assert core.park_tab_open(state, "w1")
    assert heelr("apply", "grid", "--tab", tab) == 1
    assert "heelr back" in capsys.readouterr().err and fake.snapshot() == state


def test_a_sleeping_overflow_tab_is_reused(fake):
    tab, _ = fake.add_tab("w1", "main", 2)
    ov, (o,) = fake.add_tab("w1", "💤 overflow", 1)
    assert core.read_tab(fake.snapshot(), tab, []).overflow_tab == (ov, o)


def test_multi_layouts_see_a_sleeping_tab_by_its_label(fake):
    from heelr import multi
    t1, _ = fake.add_tab("w1", "💤 agents", 1)
    ws = multi.read_workspace(fake.snapshot(), "w1", [])
    assert ws.by_label("agents").tab_id == t1
    assert 'label = "agents"' in multi.capture(ws, "snap")


def test_layout_lock_is_never_the_live_one(tmp_path):
    p = core.layout_lock_path()
    assert p is not None and str(p).startswith(str(tmp_path))


def test_sleep_takes_no_layout_lock(tmp_path):
    import fcntl
    with core.apply_lock(layout=False):
        with open(core.layout_lock_path(), "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)        # free


def test_apply_lock_also_takes_the_shared_layout_lock(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    with core.apply_lock():
        with open(tmp_path / "herdr.sock.layout.lock", "a") as f:
            with pytest.raises(BlockingIOError):
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(tmp_path / "herdr.sock.layout.lock", "a") as f:   # released afterwards
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_apply_lock_times_out_on_a_held_layout_lock_and_releases_apply_lock(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    with open(tmp_path / "herdr.sock.layout.lock", "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)                        # another plugin holds it
        with pytest.raises(herdr.HerdrError, match="another layout change"):
            with core.apply_lock(wait=0.2):
                pass
        assert not lock_held()                                   # apply.lock was released


def test_apply_lock_releases_both_on_error(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    with pytest.raises(ValueError):
        with core.apply_lock():
            raise ValueError("boom")
    assert not lock_held()
    with open(tmp_path / "herdr.sock.layout.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
