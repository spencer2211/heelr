"""Auto-tile: the per-tab pane order, `heelr auto`, `heelr auto-toggle`."""
import fcntl
import threading
import time

import pytest

from heelr import core, herdr
from heelr.tree import leaves
from test_apply_safety import fake, heelr, lock_held  # noqa: F401


def slots(fake, tab):
    """The tab's panes in slot order: main pane(s) first (largest), then the rest in tree order."""
    lay = fake.call("pane", "layout", "--pane", fake.panes(tab)[0])["layout"]
    main = core.largest_pane(lay)
    return [main] + [p for p in fake.panes(tab) if p != main]


# ------------------------------------------------- the per-tab pane order

def test_apply_records_the_panes_in_slot_order(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert heelr("apply", "main-right", "--tab", tab) == 0
    assert core.tab_record(tab)["panes"] == slots(fake, tab)


def test_slot_order_lists_every_main_first():
    from heelr.layouts import expand_rule
    t = expand_rule("two-mains", 4)
    for i, l in enumerate(leaves(t)):
        l.pane = f"p{i}"
    assert core.slot_order(t) == ["p0", "p1", "p2", "p3"]
    t = expand_rule("main-right", 3)
    for i, l in enumerate(leaves(t)):
        l.pane = f"p{i}"
    assert core.slot_order(t) == ["p2", "p0", "p1"]


def test_promote_swaps_the_two_in_the_recorded_order(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert heelr("apply", "main-right", "--tab", tab) == 0
    main, s1, s2 = core.tab_record(tab)["panes"]
    fake.focused_pane = s2
    assert heelr("promote", "--tab", tab) == 0
    assert core.tab_record(tab)["panes"] == [s2, s1, main]


def test_promote_on_a_tab_with_no_record_writes_none(fake):
    tab, (a, b) = fake.add_tab("w1", "main", 2)
    fake.focused_pane = a
    assert heelr("promote", "--tab", tab) == 0
    assert core.tab_record(tab) == {}


def test_nudge_that_applies_again_records_the_panes(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert heelr("apply", "main-right", "--tab", tab) == 0
    fake.call("pane", "split", fake.panes(tab)[0], "--direction", "down", "--ratio", "0.5")
    assert heelr("nudge", "+", "--tab", tab) == 0          # changed shape: applied again
    assert core.tab_record(tab)["panes"] == slots(fake, tab)


# ------------------------------------------------- the lock can wait

def _hold_lock_for(seconds):
    f = open(core.state_dir() / "apply.lock", "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    threading.Timer(seconds, lambda: (fcntl.flock(f, fcntl.LOCK_UN), f.close())).start()


def test_the_lock_can_wait_for_another_holder(fake):
    _hold_lock_for(0.3)
    t0 = time.monotonic()
    with core.apply_lock(wait=5):
        assert time.monotonic() - t0 >= 0.25
    time.sleep(0.05)


def test_a_waiting_lock_gives_up_after_its_timeout(fake):
    _hold_lock_for(1.0)
    with pytest.raises(herdr.HerdrError, match="another layout change"):
        with core.apply_lock(wait=0.2):
            pass
    time.sleep(1.0)


# ------------------------------------------------------------- heelr auto

from heelr import cli  # noqa: E402
from heelr.layouts import expand_rule  # noqa: E402
from heelr.tree import same_structure  # noqa: E402

MUTATING = (("pane", "move"), ("pane", "split"), ("pane", "swap"), ("pane", "resize"), ("pane", "close"))


def mutating(fake):
    return [c for c in fake.calls if c[:2] in MUTATING]


def auto_on(on=True):
    d = core.config_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.toml").write_text(f"auto_tile = {'true' if on else 'false'}\n")


def laid_out(fake, name="main-right", n=3, on=True):
    tab, _ = fake.add_tab("w1", "main", n)
    assert heelr("apply", name, "--tab", tab) == 0
    auto_on(on)
    fake.calls.clear()
    return tab, core.tab_record(tab)["panes"]


def open_pane(fake, beside):
    """What Herdr does when a pane opens: split the focused one."""
    return fake.call("pane", "split", beside, "--direction", "down", "--ratio", "0.5")["pane"]["pane_id"]


def assert_layout(fake, tab, name, order):
    """The tab is `name` for len(order) panes, with the panes in that slot order."""
    from heelr.layouts import load
    want = expand_rule(name, len(order), core.tab_ratio(tab, name) or load(name).main_ratio)
    assert same_structure(want, fake.tree(tab), tol=0.02, check_panes=False) == []
    assert slots(fake, tab)[0] == order[0]
    assert core.slot_order(cli.bind_order(want, order)) == order
    got = list(leaves(fake.tree(tab)))
    assert [l.pane for l in got] == [l.pane for l in leaves(cli.bind_order(want, order))]


def test_a_new_pane_joins_the_end_of_the_stack(fake):
    tab, order = laid_out(fake)
    new = open_pane(fake, order[1])
    assert heelr("auto") == 0
    assert_layout(fake, tab, "main-right", order + [new])
    assert core.tab_record(tab)["panes"] == order + [new]
    assert set(fake.panes(tab)) == set(order) | {new}       # the same panes, none created or lost


def test_a_closed_pane_leaves_no_gap(fake):
    tab, order = laid_out(fake, n=4)
    fake.call("pane", "close", order[2])
    assert heelr("auto") == 0
    assert_layout(fake, tab, "main-right", [order[0], order[1], order[3]])


def test_when_main_closes_the_top_of_the_stack_moves_up(fake):
    tab, order = laid_out(fake, n=4)
    fake.call("pane", "close", order[0])
    assert heelr("auto") == 0
    assert_layout(fake, tab, "main-right", order[1:])


def test_a_burst_of_new_panes_all_join_in_order(fake):
    tab, order = laid_out(fake)
    a = open_pane(fake, order[0])
    b = open_pane(fake, a)
    assert heelr("auto") == 0
    assert_layout(fake, tab, "main-right", order + [a, b])


def test_works_for_two_mains(fake):
    tab, order = laid_out(fake, "two-mains", n=3)
    new = open_pane(fake, order[2])
    assert heelr("auto") == 0
    assert_layout(fake, tab, "two-mains", order + [new])


def test_the_tabs_nudged_ratio_is_kept(fake):
    tab, order = laid_out(fake)
    assert heelr("nudge", "+", "--tab", tab) == 0
    new = open_pane(fake, order[1])
    assert heelr("auto") == 0
    assert_layout(fake, tab, "main-right", order + [new])
    assert abs(fake.tree(tab).ratio - 0.3) < 0.02


def test_focus_stays_on_the_new_pane(fake):
    tab, order = laid_out(fake)
    new = open_pane(fake, order[1])
    fake.focused_pane = new
    assert heelr("auto") == 0
    assert fake.focused_pane == new


def test_closing_down_to_one_pane_creates_nothing(fake):
    tab, order = laid_out(fake, "main-left", n=2)
    fake.call("pane", "close", order[1])
    assert heelr("auto") == 0
    assert not [c for c in fake.calls if c[:2] == ("pane", "split")]
    assert core.tab_record(tab)["panes"] == [order[0]]


def test_a_second_run_finds_nothing_to_do(fake):
    tab, order = laid_out(fake)
    open_pane(fake, order[1])
    assert heelr("auto") == 0
    fake.calls.clear()
    assert heelr("auto") == 0
    assert mutating(fake) == []


def test_moving_panes_within_the_tab_doesnt_trigger_it(fake):
    tab, order = laid_out(fake)
    fake.call("pane", "swap", "--source-pane", order[1], "--target-pane", order[2])
    fake.calls.clear()
    assert heelr("auto") == 0
    assert mutating(fake) == []


def test_promote_then_a_new_pane_keeps_the_promoted_pane_main(fake):
    tab, order = laid_out(fake)
    fake.focused_pane = order[2]
    assert heelr("promote", "--tab", tab) == 0
    new = open_pane(fake, order[1])
    assert heelr("auto") == 0
    assert slots(fake, tab)[0] == order[2]
    assert core.tab_record(tab)["panes"][-1] == new


def test_auto_saves_no_undo_record(fake):
    tab, order = laid_out(fake)
    before = core._back_path(tab).read_text()
    open_pane(fake, order[1])
    assert heelr("auto") == 0
    assert core._back_path(tab).read_text() == before


@pytest.mark.parametrize("why", ["off", "no config", "tab off", "no record", "no pane order", "after back"])
def test_it_leaves_the_tab_alone_when(fake, why):
    tab, order = laid_out(fake, on=why != "off")
    if why == "no config":
        (core.config_dir() / "config.toml").unlink()
    if why == "tab off":
        core.update_tab_record(tab, auto=False)
    if why == "no record":
        core._tab_record_path(tab).unlink()
    if why == "no pane order":
        core._tab_record_path(tab).write_text('{"layout": "main-right"}')
    if why == "after back":
        assert heelr("back", "--tab", tab) == 0
    fake.calls.clear()
    open_pane(fake, fake.panes(tab)[0])
    assert heelr("auto") == 0
    assert [c[:2] for c in mutating(fake)] == [("pane", "split")]     # only the pane we opened


def test_a_fixed_layout_is_left_alone(fake):
    tab, _ = fake.add_tab("w1", "main", 2)
    assert heelr("save", "mine", "--tab", tab) == 0
    assert heelr("apply", "mine", "--tab", tab) == 0
    core.update_tab_record(tab, panes=fake.panes(tab))
    auto_on()
    open_pane(fake, fake.panes(tab)[0])
    fake.calls.clear()
    assert heelr("auto") == 0
    assert mutating(fake) == []
    assert fake.notifications == []                     # skipped, not failed
    assert not (core.state_dir() / "auto.log").exists()


def test_a_pane_opened_during_a_run_is_tiled_by_the_next_pass(fake):
    tab, order = laid_out(fake)
    first = open_pane(fake, order[1])
    late = []

    def hook(args):
        if args[:2] == ("pane", "move") and not late:   # mid-run, after the pass's snapshot
            late.append(open_pane(fake, order[0]))
    fake.on_call = hook
    assert heelr("auto") == 0
    fake.on_call = None
    assert late and core.tab_record(tab)["panes"] == order + [first] + late
    assert_layout(fake, tab, "main-right", order + [first] + late)


def test_agents_logs_lays_the_tab_out_as_apply_would(fake):
    tab, _ = fake.add_tab("w1", "main", 3, agents=(0, 1))
    assert heelr("apply", "agents-logs", "--tab", tab) == 0
    auto_on()
    new = open_pane(fake, fake.panes(tab)[0])
    assert heelr("auto") == 0
    ts = core.read_tab(herdr.snapshot(), tab, cli._roles())
    from heelr.layouts import load
    lay = load("agents-logs")
    lay.add_missing = False
    want, _ = core.target_for(lay, ts)
    assert same_structure(want, fake.tree(tab), tol=0.02, check_panes=False) == []
    assert new in fake.panes(tab)


def test_a_park_tab_pauses_it_and_says_so_once(fake):
    tab, order = laid_out(fake)
    fake.add_tab("w1", "heelr-park", 1)
    open_pane(fake, order[1])
    fake.calls.clear()
    assert heelr("auto") == 0 and heelr("auto") == 0
    assert mutating(fake) == []
    log = (core.state_dir() / "auto.log").read_text()
    assert log.count("heelr-park") == 1


def test_a_pane_opened_while_another_change_runs_is_tiled_after_it(fake):
    tab, order = laid_out(fake)
    new = open_pane(fake, order[1])
    _hold_lock_for(0.3)
    assert heelr("auto") == 0
    assert_layout(fake, tab, "main-right", order + [new])


def test_a_lock_held_too_long_is_logged_and_changes_nothing(fake, monkeypatch):
    monkeypatch.setattr(cli, "AUTO_WAIT", 0.1)
    tab, order = laid_out(fake)
    open_pane(fake, order[1])
    fake.calls.clear()
    _hold_lock_for(0.5)
    assert heelr("auto") == 0
    assert mutating(fake) == []
    assert "busy" in (core.state_dir() / "auto.log").read_text()
    assert fake.notifications == []
    time.sleep(0.5)


def test_a_failure_is_logged_with_one_notification(fake):
    tab, order = laid_out(fake)
    open_pane(fake, order[1])
    fake.fail_at = fake.mutations + 1
    assert heelr("auto") == 0
    assert "error" in (core.state_dir() / "auto.log").read_text()
    assert len(fake.notifications) == 1


def test_the_log_stays_small(fake):
    p = core.state_dir() / "auto.log"
    p.write_text(("y" * 99 + "\n") * 1000)          # 100 KB
    cli._auto_log("hello")
    assert p.stat().st_size <= cli.AUTO_LOG_MAX and p.read_text().endswith("hello\n")


def test_agents_logs_never_opens_a_pane_when_only_agents_are_left(fake):
    tab, ids = fake.add_tab("w1", "main", 3, agents=(0, 1))
    assert heelr("apply", "agents-logs", "--tab", tab) == 0
    auto_on()
    fake.call("pane", "close", ids[2])                       # the only shell goes
    assert heelr("auto") == 0
    assert sorted(fake.panes(tab)) == sorted(ids[:2])


# ------------------------------------------------------ heelr auto-toggle

def test_the_toggle_switches_the_tab_off_and_on_and_says_so(fake, capsys):
    tab, order = laid_out(fake)
    assert heelr("auto-toggle", "--tab", tab) == 0
    assert core.tab_record(tab)["auto"] is False
    assert heelr("auto-toggle", "--tab", tab) == 0
    assert core.tab_record(tab)["auto"] is True
    assert [n.split("--body ", 1)[1] for n in fake.notifications] == \
        ["auto-tile off for this tab", "auto-tile on for this tab"]
    assert core.tab_record(tab)["panes"] == order               # the rest of the record is kept


def test_a_later_apply_keeps_the_tab_switched_off(fake):
    tab, _ = laid_out(fake)
    assert heelr("auto-toggle", "--tab", tab) == 0
    assert heelr("apply", "main-left", "--tab", tab) == 0
    assert core.tab_record(tab)["auto"] is False


def test_the_toggle_says_when_auto_tile_is_off_everywhere(fake):
    tab, _ = laid_out(fake, on=False)
    assert heelr("auto-toggle", "--tab", tab) == 0
    assert "auto_tile" in fake.notifications[-1]


def test_the_toggle_waits_for_the_lock(fake):
    tab, _ = laid_out(fake)
    _hold_lock_for(0.3)
    assert heelr("auto-toggle", "--tab", tab) == 0
    assert core.tab_record(tab)["auto"] is False


def test_a_tab_none_of_whose_recorded_panes_remain_is_left_alone(fake):
    """No recorded pane left: a reused tab id (e.g. after a Herdr restart), not this layout."""
    tab, order = laid_out(fake)
    core.update_tab_record(tab, panes=["w9:p1", "w9:p2", "w9:p3"])
    fake.calls.clear()
    assert heelr("auto") == 0
    assert mutating(fake) == []


# ------------------------------------------- review minors (M1-M4)

def test_a_repeating_error_notifies_once(fake):
    tab, order = laid_out(fake)
    open_pane(fake, order[1])
    fake.fail_requests.add(("api", "snapshot"))   # the same error every time (like an untested Herdr)
    for _ in range(3):
        assert heelr("auto") == 0
    assert len(fake.notifications) == 1
    assert (core.state_dir() / "auto.log").read_text().count("error") == 1


def test_a_different_error_notifies_again(fake, monkeypatch):
    tab, order = laid_out(fake)
    msgs = iter(["first problem", "second problem"])
    monkeypatch.setattr(core, "run", lambda *a, **k: (_ for _ in ()).throw(herdr.HerdrError(next(msgs))))
    for _ in range(2):
        open_pane(fake, order[1])
        assert heelr("auto") == 0
    assert len(fake.notifications) == 2


def test_interactive_commands_wait_briefly_for_a_running_change(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    _hold_lock_for(0.3)
    assert heelr("apply", "main-right", "--tab", tab) == 0
    _hold_lock_for(0.3)
    fake.focused_pane = fake.panes(tab)[0]
    assert heelr("promote", "--tab", tab) == 0
    _hold_lock_for(0.3)
    assert heelr("nudge", "+", "--tab", tab) == 0
    _hold_lock_for(0.3)
    assert heelr("back", "--tab", tab) == 0


def test_an_unexpected_error_in_one_tab_is_logged_and_the_others_still_tile(fake, monkeypatch):
    t1, o1 = laid_out(fake)
    t2, _ = fake.add_tab("w1", "two", 3)
    assert heelr("apply", "main-right", "--tab", t2) == 0
    o2 = core.tab_record(t2)["panes"]
    real = core.run

    def run(target, overflow, ts, *a, **k):
        if ts.tab_id == t1:
            raise TypeError("odd snapshot")
        return real(target, overflow, ts, *a, **k)
    monkeypatch.setattr(core, "run", run)
    open_pane(fake, o1[1])
    new2 = open_pane(fake, o2[1])
    assert heelr("auto") == 0
    assert core.tab_record(t2)["panes"] == o2 + [new2]
    assert "odd snapshot" in (core.state_dir() / "auto.log").read_text()
    assert len(fake.notifications) == 1


def test_a_lock_file_that_cant_be_opened_is_an_error_not_busy(fake):
    tab, order = laid_out(fake)
    lock = core.state_dir() / "apply.lock"
    lock.unlink(missing_ok=True)
    lock.mkdir()
    open_pane(fake, order[1])
    assert heelr("auto") == 0
    log = (core.state_dir() / "auto.log").read_text()
    assert "error" in log and "busy" not in log


def test_an_unexpected_error_outside_any_tab_never_fails_the_hook(fake, monkeypatch):
    laid_out(fake)
    monkeypatch.setattr(herdr, "snapshot", lambda: (_ for _ in ()).throw(TypeError("odd reply")))
    assert heelr("auto") == 0
    assert "odd reply" in (core.state_dir() / "auto.log").read_text()


# ------------------------------------------- review follow-ups (N1, N2)

def test_a_late_pane_is_tiled_even_when_a_later_tab_had_nothing_to_do(fake):
    """The re-check pass runs if *any* tab changed, not just the last one looked at."""
    t1, o1 = laid_out(fake)
    t2, _ = fake.add_tab("w1", "two", 3)                     # later in snapshot order
    assert heelr("apply", "main-right", "--tab", t2) == 0
    first = open_pane(fake, o1[1])
    late = []

    def hook(args):
        if args[:2] == ("pane", "move") and not late:
            late.append(open_pane(fake, o1[0]))
    fake.on_call = hook
    assert heelr("auto") == 0
    fake.on_call = None
    assert core.tab_record(t1)["panes"] == o1 + [first] + late


def test_an_error_that_clears_and_comes_back_notifies_again(fake):
    tab, order = laid_out(fake)
    open_pane(fake, order[1])
    fake.fail_requests.add(("api", "snapshot"))
    assert heelr("auto") == 0                                # error: notified
    fake.fail_requests.clear()
    assert heelr("auto") == 0                                # recovered
    assert (core.state_dir() / "auto.log").read_text().splitlines()[-1].endswith("ok: auto-tile working again")
    fake.fail_requests.add(("api", "snapshot"))
    assert heelr("auto") == 0                                # the same error again: notified again
    assert len(fake.notifications) == 2


def test_a_good_run_with_no_earlier_error_logs_nothing(fake):
    tab, order = laid_out(fake)
    open_pane(fake, order[1])
    assert heelr("auto") == 0
    assert not (core.state_dir() / "auto.log").exists()


def test_a_lasting_error_in_one_tab_notifies_once(fake, monkeypatch):
    tab, order = laid_out(fake)
    monkeypatch.setattr(core, "run", lambda *a, **k: (_ for _ in ()).throw(herdr.HerdrError("stuck")))
    open_pane(fake, order[1])
    for _ in range(3):
        assert heelr("auto") == 0
    assert len(fake.notifications) == 1
    assert "ok:" not in (core.state_dir() / "auto.log").read_text()
