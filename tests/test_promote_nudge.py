"""promote and nudge, end to end against the offline Herdr."""
import fcntl

import pytest

from heelr import core
from heelr.layouts import expand_rule
from heelr.tree import Leaf, Split, leaves, same_structure
from test_apply_safety import fake, heelr, lock_held  # noqa: F401


def calls(fake, *kinds):
    return [c for c in fake.calls if c[:2] in kinds]


MUTATING = (("pane", "move"), ("pane", "split"), ("pane", "swap"), ("pane", "resize"), ("pane", "close"))


# ------------------------------------------------- the fake, vs real Herdr 0.9.3

def test_fake_swap_exchanges_two_panes_and_focuses_the_source(fake):
    # seen on a throwaway 0.9.3: right(0.40)[down[p3,p2],p1], swap p3 -> p1
    tab, (a, b, c) = fake.add_tab("w1", "main", 3)
    fake.tabs[tab]["tree"] = Split("right", 0.4, Split("down", 0.5, Leaf(pane=c), Leaf(pane=b)), Leaf(pane=a))
    r = fake.call("pane", "swap", "--source-pane", c, "--target-pane", a)
    assert r["swap"]["changed"] is True
    assert [l.pane for l in leaves(fake.tree(tab))] == [a, b, c]
    assert fake.focused_pane == c


def test_fake_resize_moves_the_nearest_divider_on_that_side(fake):
    # seen on 0.9.3: main p1 in right(0.40)[stack, p1]: left 0.1 -> 0.30; right 0.1 -> back to 0.40
    tab, (a, b, c) = fake.add_tab("w1", "main", 3)
    fake.tabs[tab]["tree"] = Split("right", 0.4, Split("down", 0.5, Leaf(pane=c), Leaf(pane=b)), Leaf(pane=a))
    fake.call("pane", "resize", "--pane", a, "--direction", "left", "--amount", "0.1")
    assert abs(fake.tree(tab).ratio - 0.3) < 1e-9
    fake.call("pane", "resize", "--pane", c, "--direction", "right", "--amount", "0.1")
    assert abs(fake.tree(tab).ratio - 0.4) < 1e-9
    fake.call("pane", "resize", "--pane", a, "--direction", "left", "--amount", "5")
    assert abs(fake.tree(tab).ratio - 0.1) < 1e-9          # 0.9.3 stops at 0.1
    # main-center: the main pane's left edge is the outer split, its right edge the inner one
    fake.tabs[tab]["tree"] = Split("right", 0.25, Leaf(pane=c), Split("right", 2 / 3, Leaf(pane=a), Leaf(pane=b)))
    fake.call("pane", "resize", "--pane", a, "--direction", "left", "--amount", "0.1")
    assert abs(fake.tree(tab).ratio - 0.15) < 1e-9 and abs(fake.tree(tab).b.ratio - 2 / 3) < 1e-9
    fake.call("pane", "resize", "--pane", a, "--direction", "right", "--amount", "0.1")
    assert abs(fake.tree(tab).b.ratio - (2 / 3 + 0.1)) < 1e-9


# ------------------------------------------------------------------- promote

def _main_right(fake, n=3):
    tab, ids = fake.add_tab("w1", "main", n)
    assert heelr("apply", "main-right", "--tab", tab) == 0
    order = [l.pane for l in leaves(fake.tree(tab))]   # stack..., main
    fake.calls.clear()
    return tab, order


def test_promote_swaps_the_focused_pane_with_the_largest(fake):
    tab, order = _main_right(fake, 4)
    s1, s2, s3, main = order
    fake.focused_pane = s2
    assert heelr("promote", "--tab", tab) == 0
    assert [l.pane for l in leaves(fake.tree(tab))] == [s1, main, s3, s2]
    assert fake.focused_pane == s2
    assert calls(fake, *MUTATING) == [("pane", "swap", "--source-pane", s2, "--target-pane", main)]
    assert "heelr-park" not in fake.labels()


def test_promote_when_already_main_changes_nothing_and_says_so(fake, capsys):
    tab, order = _main_right(fake)
    fake.focused_pane = order[-1]
    assert heelr("promote", "--tab", tab) == 0
    assert calls(fake, *MUTATING) == []
    assert "already main" in capsys.readouterr().out
    assert any("already main" in n for n in fake.notifications)   # stderr isn't a tty here


def test_promote_breaks_ties_top_left_first(fake):
    tab, (a, b) = fake.add_tab("w1", "main", 2)               # right(0.5)[a, b]: equal
    fake.focused_pane = b
    assert heelr("promote", "--tab", tab) == 0
    assert [l.pane for l in leaves(fake.tree(tab))] == [b, a]


def test_back_undoes_a_promote(fake):
    tab, order = _main_right(fake, 4)
    before = fake.tree(tab)
    fake.focused_pane = order[0]
    assert heelr("promote", "--tab", tab) == 0
    assert heelr("back", "--tab", tab) == 0
    assert same_structure(before, fake.tree(tab), tol=0.02) == []


def test_promote_holds_the_lock_while_it_swaps(fake):
    tab, order = _main_right(fake)
    fake.focused_pane = order[0]
    seen = []
    fake.on_call = lambda args: seen.append(lock_held()) if args[:2] == ("pane", "swap") else None
    assert heelr("promote", "--tab", tab) == 0
    assert seen == [True]


def test_promote_refuses_while_another_heelr_change_runs(fake, capsys):
    tab, order = _main_right(fake)
    fake.focused_pane = order[0]
    with open(core.state_dir() / "apply.lock", "w") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        assert heelr("promote", "--tab", tab) == 1
    assert "another layout change is running" in capsys.readouterr().err
    assert calls(fake, *MUTATING) == []


def test_promote_refuses_while_a_park_tab_is_open(fake):
    tab, order = _main_right(fake)
    fake.add_tab("w1", "heelr-park", 1)
    fake.focused_tab, fake.focused_pane = tab, order[0]
    assert heelr("promote", "--tab", tab) == 1
    assert calls(fake, *MUTATING) == []


# --------------------------------------------------------------------- nudge

def _ratios(tree):
    out = []
    def walk(n):
        if isinstance(n, Split):
            out.append(round(n.ratio, 4))
            walk(n.a)
            walk(n.b)
    walk(tree)
    return out


@pytest.mark.parametrize("name,n", [("main-right", 3), ("main-left", 4), ("main-top", 3),
                                    ("main-bottom", 3), ("main-left-grid", 5), ("main-center", 4),
                                    ("main-center", 5), ("two-mains", 4), ("agents-logs", 4)])
def test_nudge_resizes_in_place_to_the_layout_with_the_new_ratio(fake, name, n):
    tab, _ = fake.add_tab("w1", "main", n, agents=(0, 1) if name == "agents-logs" else ())
    assert heelr("apply", name, "--tab", tab) == 0
    from heelr.layouts import load
    base = load(name).main_ratio
    fake.calls.clear()
    assert heelr("nudge", "+", "--tab", tab) == 0
    want = round(min(0.8, base + 0.1), 2)
    assert core.tab_ratio(tab, name) == want
    kinds = {c[:2] for c in calls(fake, *MUTATING)}
    assert kinds <= {("pane", "resize")}, kinds              # no pane moved
    expect = expand_rule(name, n, want, 2 if name == "agents-logs" else None)
    assert [round(r, 2) for r in _ratios(fake.tree(tab))] == [round(r, 2) for r in _ratios(expect)]
    assert heelr("nudge", "-", "--tab", tab) == 0
    assert core.tab_ratio(tab, name) == round(base, 2)
    expect = expand_rule(name, n, base, 2 if name == "agents-logs" else None)
    assert [round(r, 2) for r in _ratios(fake.tree(tab))] == [round(r, 2) for r in _ratios(expect)]


def test_nudge_stops_at_the_limits(fake, capsys):
    tab, _ = _main_right(fake)
    for _ in range(5):
        heelr("nudge", "+", "--tab", tab)
    assert core.tab_ratio(tab, "main-right") == 0.8
    fake.calls.clear()
    assert heelr("nudge", "+", "--tab", tab) == 0
    assert calls(fake, *MUTATING) == []
    assert "limit" in capsys.readouterr().out
    for _ in range(8):
        heelr("nudge", "-", "--tab", tab)
    assert core.tab_ratio(tab, "main-right") == 0.2


def test_a_nudged_ratio_sticks_to_later_applies_on_that_tab(fake):
    tab, _ = _main_right(fake)
    assert heelr("nudge", "+", "--tab", tab) == 0
    assert heelr("apply", "main-left", "--tab", tab) == 0
    assert heelr("apply", "main-right", "--tab", tab) == 0
    assert abs(fake.tree(tab).ratio - 0.3) < 1e-6


def test_nudge_after_the_tab_changed_shape_applies_the_layout_again(fake):
    tab, order = _main_right(fake)
    fake.call("pane", "split", order[0], "--direction", "down", "--ratio", "0.5")   # user adds a pane
    fake.calls.clear()
    assert heelr("nudge", "+", "--tab", tab) == 0
    t = fake.tree(tab)
    assert len(list(leaves(t))) == 4 and abs(t.ratio - 0.3) < 1e-6
    assert core.tab_record(tab)["layout"] == "main-right"
    assert heelr("back", "--tab", tab) == 0                    # a real apply: undo recorded


@pytest.mark.parametrize("name", ["grid", "even-columns", "even-rows"])
def test_nudge_refuses_layouts_with_no_main_area(fake, name, capsys):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert heelr("apply", name, "--tab", tab) == 0
    fake.calls.clear()
    assert heelr("nudge", "+", "--tab", tab) == 1
    assert "no main area" in capsys.readouterr().err
    assert calls(fake, *MUTATING) == []


def test_nudge_needs_a_recorded_layout(fake, capsys):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert heelr("nudge", "+", "--tab", tab) == 1
    assert "apply a layout first" in capsys.readouterr().err
    assert any("apply a layout first" in n for n in fake.notifications)


def test_nudge_holds_the_lock_while_it_resizes(fake):
    tab, _ = _main_right(fake)
    seen = []
    fake.on_call = lambda args: seen.append(lock_held()) if args[:2] == ("pane", "resize") else None
    assert heelr("nudge", "+", "--tab", tab) == 0
    assert seen and all(seen)


def test_nudge_refuses_while_a_park_tab_is_open(fake):
    tab, _ = _main_right(fake)
    fake.add_tab("w1", "heelr-park", 1)
    assert heelr("nudge", "+", "--tab", tab) == 1
    assert calls(fake, *MUTATING) == []


# ------------------------------------- the tab record follows back and multi applies

def test_nudge_after_back_does_not_reapply_the_undone_layout(fake, capsys):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert heelr("apply", "main-right", "--tab", tab) == 0
    assert heelr("back", "--tab", tab) == 0
    restored = fake.tree(tab)
    fake.calls.clear()
    assert heelr("nudge", "+", "--tab", tab) == 1
    assert "apply a layout first" in capsys.readouterr().err
    assert calls(fake, *MUTATING) == []
    assert same_structure(restored, fake.tree(tab)) == []


def test_back_keeps_the_tabs_nudged_ratios(fake):
    tab, _ = _main_right(fake)
    assert heelr("nudge", "+", "--tab", tab) == 0
    assert heelr("back", "--tab", tab) == 0
    rec = core.tab_record(tab)
    assert "layout" not in rec and "main-right" in rec["ratios"]


def test_a_dry_run_back_keeps_the_recorded_layout(fake):
    tab, _ = _main_right(fake)
    assert heelr("back", "--dry-run", "--tab", tab) == 0
    assert core.tab_record(tab)["layout"] == "main-right"


def test_a_multi_tab_apply_forgets_each_tabs_single_layout(fake):
    from test_apply_safety import FLEET, write_layout
    t1, _ = fake.add_tab("w1", "one", 2, agents=(0,))
    t2, _ = fake.add_tab("w1", "two", 2, agents=(1,))
    assert heelr("apply", "main-right", "--tab", t1) == 0
    assert heelr("apply", "main-top", "--tab", t2) == 0
    write_layout("fleet", FLEET)
    assert heelr("apply", "fleet", "--tab", t1) == 0
    for t in {t1, t2, *fake.tabs}:            # the tabs it closed too: Herdr can reuse their ids
        assert "layout" not in core.tab_record(t), t
