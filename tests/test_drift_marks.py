"""Drift marks: `<label> ↗N` on each tab holding N panes that `heelr sort --all` would move."""
import io
import json
import sys
from pathlib import Path

import pytest

from heelr import cli, core, marks, place, sleep
from test_placement_place import fake  # noqa: F401
from test_placement_facts import make_repo, make_worktree
from test_placement_cli import on, h, focus, state_files  # noqa: F401


def label(f, tid):
    return f.tabs[tid]["label"]


@pytest.fixture
def drifted(on, tmp_path):
    """Workspace heelr: tab main holds a (focused), b and c, all in worktree GH-1; tab GH-1 holds d."""
    wt = make_worktree(make_repo(tmp_path / "heelr"), "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b, c) = on.add_tab(w, "main", 3, cwd=str(wt))
    t2, (d,) = on.add_tab(w, "GH-1", 1, cwd=str(wt))
    focus(on, t1, a)
    return on, w, t1, t2, (a, b, c, d), wt


# ------------------------------------------------------------------ labels

@pytest.mark.parametrize("raw,base", [
    ("main ↗2", "main"), ("💤 main ↗12", "main"), ("💤 main", "main"), ("main", "main"),
    ("a ↗ b", "a ↗ b"), ("↗2", "↗2"), ("main ↗", "main ↗"), ("", "")])
def test_base_label_drops_the_sleep_and_drift_marks(raw, base):
    assert core.base_label({"label": raw}) == base


@pytest.mark.parametrize("cur,n,want", [
    ("main", 2, "main ↗2"), ("main ↗2", 0, "main"), ("main ↗2", 3, "main ↗3"),
    ("💤 main", 1, "💤 main ↗1"), ("💤 main ↗1", 0, "💤 main"), ("main", 0, "main")])
def test_marked_label(cur, n, want):
    assert marks.marked(cur, n) == want


# ------------------------------------------------------------------ refresh

def test_refresh_marks_each_tab_with_all_its_drifted_panes_the_focused_one_too(drifted):
    on, w, t1, t2, (a, b, c, d), wt = drifted
    marks.refresh(allow_untested=True)
    assert label(on, t1) == "main ↗3"       # a (focused), b and c
    focus(on, t2, d)
    marks.refresh(allow_untested=True)
    assert label(on, t1) == "main ↗3"       # the same number wherever the focus is
    assert label(on, t2) == "GH-1"          # d is home


def test_refresh_clears_the_mark_once_the_tab_is_clean(drifted):
    on, w, t1, t2, (a, b, c, d), wt = drifted
    marks.refresh(allow_untested=True)
    assert h("sort") == 0                   # sort refreshes too
    assert label(on, t1) == "main ↗1"       # sort leaves the focused pane a where it is
    assert label(on, t2) == "GH-1"
    focus(on, t2, d)
    assert h("sort", "--all") == 0          # a moves too; main, now empty, closes
    assert [t["label"] for t in on.tabs.values() if t["ws"] == w] == ["GH-1"]


def test_refresh_renames_only_what_changed(drifted):
    on, *_ = drifted
    marks.refresh(allow_untested=True)
    n = sum(1 for c in on.calls if c[:2] == ("tab", "rename"))
    marks.refresh(allow_untested=True)
    assert sum(1 for c in on.calls if c[:2] == ("tab", "rename")) == n == 1


def test_refresh_follows_a_rename_by_hand(drifted):
    on, w, t1, *_ = drifted
    marks.refresh(allow_untested=True)
    on.tabs[t1]["label"] = "renamed ↗3"
    marks.refresh(allow_untested=True)
    assert label(on, t1) == "renamed ↗3"


def test_refresh_skips_a_tab_renamed_after_its_snapshot(drifted, monkeypatch):
    """tab-autoname (or the user) renamed the tab while the counts were worked out: leave it."""
    on, w, t1, *_ = drifted
    real = place.drifted
    def rename_meanwhile(*a, **k):
        on.tabs[t1]["label"] = "newname"
        return real(*a, **k)
    monkeypatch.setattr(place, "drifted", rename_meanwhile)
    marks.refresh(allow_untested=True)
    assert label(on, t1) == "newname"


def test_refresh_does_nothing_with_placement_off(drifted, tmp_path):
    on, w, t1, *_ = drifted
    (tmp_path / "cfg/heelr/placement.toml").write_text("[placement]\nenabled = false\n")
    marks.refresh(allow_untested=True)
    assert label(on, t1) == "main"


def test_turning_placement_off_takes_heelrs_marks_away_but_not_a_hand_made_one(drifted, tmp_path):
    on, w, t1, t2, *_ = drifted
    marks.refresh(allow_untested=True)
    on.tabs[t2]["label"] = "mine ↗9"         # typed by hand: not heelr's
    (tmp_path / "cfg/heelr/placement.toml").write_text("[placement]\nenabled = false\n")
    marks.refresh(allow_untested=True)
    assert label(on, t1) == "main"
    assert label(on, t2) == "mine ↗9"


def test_refresh_respects_the_version_gate(drifted):
    on, w, t1, *_ = drifted
    on.version = "0.0.1"
    marks.refresh()
    assert label(on, t1) == "main"


def test_refresh_never_fails(drifted, monkeypatch):
    on, w, t1, *_ = drifted
    on.fail_requests.add(("tab", "rename"))
    marks.refresh(allow_untested=True)
    monkeypatch.setattr(cli.herdr, "snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    marks.refresh(allow_untested=True)


def test_refresh_out_of_budget_changes_nothing(drifted, monkeypatch):
    on, w, t1, *_ = drifted
    monkeypatch.setattr(marks, "BUDGET", -1.0)
    marks.refresh(allow_untested=True)
    assert label(on, t1) == "main"


def test_refresh_never_takes_the_layout_lock(drifted, monkeypatch):
    def no_lock(*a, **k):
        raise AssertionError("marks must not take the layout lock")
    monkeypatch.setattr(core, "apply_lock", no_lock)
    on, w, t1, *_ = drifted
    marks.refresh(allow_untested=True)
    assert label(on, t1) == "main ↗3"


def test_refresh_never_logs_or_notifies_a_classify_failure(drifted, tmp_path, monkeypatch):
    on, *_ = drifted
    (tmp_path / "cfg/heelr/placement.toml").write_text(
        "[placement]\nenabled = true\nticket = 'GH-\\d+'\nclassify = '/nonexistent/classify.sh'\n")
    from heelr import key as keymod
    keymod.take_failures()
    monkeypatch.setattr(keymod.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    marks.refresh(allow_untested=True)
    assert not on.notifications
    assert not (core.state_dir() / "placement.log").exists()


# ------------------------------------------------------------------ what triggers it

def test_drift_mark_command(drifted):
    on, w, t1, *_ = drifted
    assert h("drift-mark") == 0
    assert label(on, t1) == "main ↗3"


def test_report_and_tag_refresh(drifted, monkeypatch, tmp_path):
    on, w, t1, t2, (a, b, c, d), wt = drifted
    h("tag", "GH-1", "--pane", d)            # no change: d already home
    assert label(on, t1) == "main ↗3"
    h("tag", "main", "--pane", b)
    assert label(on, t1) == "main ↗2"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(tmp_path / "heelr")})))
    assert h("report", "--pane", c) == 0     # c is in the main checkout now: it belongs in main
    assert label(on, t1) == "main ↗1"


def test_the_auto_hook_refreshes(drifted, monkeypatch):
    on, w, t1, *_ = drifted
    monkeypatch.delenv("HERDR_PLUGIN_EVENT", raising=False)
    assert h("auto") == 0
    assert label(on, t1) == "main ↗3"


def test_pull_and_back_refresh(drifted):
    on, w, t1, t2, (a, b, c, d), wt = drifted
    focus(on, t2, d)
    assert h("pull", "GH-1") == 0
    labels = lambda: sorted(t["label"] for t in on.tabs.values() if t["ws"] == w)
    assert labels() == ["GH-1"]              # main emptied and closed
    assert h("back") == 0
    assert labels() == ["GH-1", "main ↗3"]   # back recreates main with a, b and c


def test_drift_status_is_gone():
    with pytest.raises(SystemExit):
        cli.main(["drift-status"])


# ------------------------------------------------------------------ everything else ignores the mark

def test_placement_finds_a_marked_tab(drifted):
    on, w, t1, t2, (a, b, c, d), wt = drifted
    on.tabs[t2]["label"] = "GH-1 ↗3"
    moves = [place.Move(b, place.Dest("heelr", "GH-1"), False)]
    place.execute(on.snapshot(), moves, place_cfg())
    assert on.tab_of(b) == t2                # no second GH-1 tab


def test_a_marked_sleeping_tab_is_still_asleep_for_placement(drifted):
    on, w, t1, t2, (a, b, c, d), wt = drifted
    on.tabs[t2]["label"] = "💤 GH-1 ↗1"
    assert place._find_tab(on.snapshot(), w, "GH-1", None)[0] == place.SLEEPING


def test_sleep_keeps_the_drift_mark_and_restores_the_name_under_it(on):
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main ↗2", 1)
    rec = {"tab": t, "panes": [{"pane": p, "status": "slept"}]}
    sleep.label_tab(rec)
    assert label(on, t) == "💤 main ↗2" and rec["label"] == "main"
    on.tabs[t]["label"] = "💤 main ↗1"        # the count changed while asleep
    rec["panes"][0]["status"] = "woken"
    sleep.label_tab(rec)
    assert label(on, t) == "main ↗1"


def test_tab_colours_ignores_the_mark():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins/tab-colours"))
    import tab_colours
    assert tab_colours.display_label({"label": "💤 heelr ↗3"}) == tab_colours.display_label({"label": "heelr"})


def place_cfg():
    from heelr import key
    return key.PlacementConfig(enabled=True, ticket=r"GH-\d+")


# ------------------------------------------------------------------ review fixes

def test_the_park_tab_is_never_marked(drifted):                                      # I1
    from heelr import plan
    on, w, t1, *_ = drifted
    on.tabs[t1]["label"] = plan.PARK_LABEL       # mid-apply: drifted panes sit in the park tab
    marks.refresh(allow_untested=True)
    assert label(on, t1) == plan.PARK_LABEL


def test_tab_colours_never_colours_a_marked_park_tab():                               # I1
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins/tab-colours"))
    import tab_colours
    snap = {"tabs": [{"tab_id": "w1:t1", "label": "heelr-park ↗3"}, {"tab_id": "w1:t2", "label": "a"}]}
    assert [t["tab_id"] for t in tab_colours.usable_tabs(snap)] == ["w1:t2"]


def test_tab_colours_never_colours_a_sleeping_park_tab():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins/tab-colours"))
    import tab_colours
    snap = {"tabs": [{"tab_id": "w1:t1", "label": "💤 heelr-park ↗1"}, {"tab_id": "w1:t2", "label": "a"}]}
    assert [t["tab_id"] for t in tab_colours.usable_tabs(snap)] == ["w1:t2"]


def test_a_refresh_that_could_not_wait_is_run_again_by_the_lock_holder(drifted, monkeypatch):   # I2
    """B gives up waiting while A works from an older snapshot: A runs once more, so the last
    event's state wins."""
    on, w, t1, t2, (a, b, c, d), wt = drifted
    monkeypatch.setattr(marks, "LOCK_WAIT", 0.0)
    real = marks._refresh
    runs = []
    def holder(allow):
        runs.append(1)
        if len(runs) == 1:
            marks.refresh(allow_untested=True)   # B arrives while A holds the lock and gives up
            focus(on, t1, b)                     # ... after the state A will read has changed
        real(allow)
    monkeypatch.setattr(marks, "_refresh", holder)
    marks.refresh(allow_untested=True)
    assert len(runs) == 2
    assert not (core.state_dir() / "marks.pending").exists()


def test_a_refresh_reruns_at_most_twice(drifted, monkeypatch):                       # I2
    on, *_ = drifted
    runs = []
    def always_pending(allow):
        runs.append(1)
        (core.state_dir() / "marks.pending").touch()
    monkeypatch.setattr(marks, "_refresh", always_pending)
    marks.refresh(allow_untested=True)
    assert len(runs) == 3                        # the run, then at most 2 more
