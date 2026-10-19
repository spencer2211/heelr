"""Offline tests for plugins/tab-colours: every herdr/tmux subprocess is blocked or mocked."""
import importlib.util
import json
import subprocess
import os
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "tab-colours"


def _load():
    spec = importlib.util.spec_from_file_location("tab_colours", PLUGIN / "tab_colours.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tc = _load()
N = len(tc.PALETTE)


@pytest.fixture(autouse=True)
def no_real_herdr(monkeypatch):
    """Any real subprocess to herdr/tmux fails the test: the plugin must go through an injected runner."""
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


def tab(tid, label, number=1):
    return {"tab_id": tid, "label": label, "number": number, "workspace_id": tid.split(":")[0]}


def pane(pid, tid, tokens=None):
    p = {"pane_id": pid, "tab_id": tid, "workspace_id": tid.split(":")[0]}
    if tokens is not None:
        p["tokens"] = tokens
    return p


def snap(tabs, panes):
    return {"tabs": tabs, "panes": panes}


# --- slot assignment -------------------------------------------------------------------------

def test_every_tab_gets_a_distinct_slot_when_they_fit():
    tabs = [tab(f"w1:t{i}", f"tab-{i}") for i in range(1, N + 1)]
    slots, _ = tc.assign(tabs, {})
    assert sorted(slots.values()) == list(range(1, N + 1))


def test_slot_is_stable_for_a_tab_id_even_when_its_label_changes():
    slots1, state = tc.assign([tab("w1:t1", "a"), tab("w1:t2", "b")], {})
    slots2, _ = tc.assign([tab("w1:t1", "renamed"), tab("w1:t2", "b")], json.loads(json.dumps(state)))
    assert slots2 == slots1


def test_new_tab_ids_recover_the_slot_by_label():
    slots1, state = tc.assign([tab("w1:t1", "alpha"), tab("w1:t2", "beta")], {})
    slots2, _ = tc.assign([tab("w9:t1", "beta"), tab("w9:t2", "alpha")], state)
    assert slots2["w9:t2"] == slots1["w1:t1"]
    assert slots2["w9:t1"] == slots1["w1:t2"]


def test_new_tab_avoids_slots_in_use():
    tabs = [tab(f"w1:t{i}", f"x{i}") for i in range(1, N)]
    slots, state = tc.assign(tabs, {})
    free = (set(range(1, N + 1)) - set(slots.values())).pop()
    slots2, _ = tc.assign(tabs + [tab("w1:t99", "newcomer")], state)
    assert slots2["w1:t99"] == free


def test_new_tab_takes_the_lowest_free_slot_so_few_tabs_get_the_brightest_colours():
    slots, state = tc.assign([tab("w1:t1", "solo")], {})
    assert slots["w1:t1"] == 1
    slots, _ = tc.assign([tab("w1:t1", "solo"), tab("w1:t2", "x"), tab("w1:t3", "y")], state)
    assert [slots[t] for t in ("w1:t1", "w1:t2", "w1:t3")] == [1, 2, 3]


def test_more_tabs_than_slots_still_get_valid_slots():
    tabs = [tab(f"w1:t{i}", f"t{i}") for i in range(N * 2 + 3)]
    slots, _ = tc.assign(tabs, {})
    assert len(slots) == len(tabs)
    assert all(1 <= s <= N for s in slots.values())
    counts = [list(slots.values()).count(s) for s in range(1, N + 1)]
    assert max(counts) - min(counts) <= 1  # spread evenly


def test_duplicate_slots_in_state_are_resolved():
    state = {"tabs": {"w1:t1": 3, "w1:t2": 3}, "labels": {}}
    slots, _ = tc.assign([tab("w1:t1", "a"), tab("w1:t2", "b")], state)
    assert slots["w1:t1"] == 3 and slots["w1:t2"] != 3


def test_closed_tab_is_pruned_and_its_slot_freed_but_label_remembered():
    slots, state = tc.assign([tab("w1:t1", "a"), tab("w1:t2", "b")], {})
    _, state2 = tc.assign([tab("w1:t1", "a")], state)
    assert "w1:t2" not in state2["tabs"]
    assert state2["labels"]["b"] == slots["w1:t2"]


def test_out_of_range_slot_in_state_is_reassigned():
    slots, _ = tc.assign([tab("w1:t1", "a")], {"tabs": {"w1:t1": N + 5}, "labels": {}})
    assert 1 <= slots["w1:t1"] <= N


def test_label_history_is_bounded():
    state = {"tabs": {}, "labels": {f"old{i}": 1 for i in range(tc.MAX_LABELS + 50)}}
    _, state2 = tc.assign([tab("w1:t1", "a")], state)
    assert len(state2["labels"]) <= tc.MAX_LABELS
    assert "a" in state2["labels"]


# --- token patches ---------------------------------------------------------------------------

def test_pane_gets_its_tabs_slot_token_with_the_tab_label():
    s = snap([tab("w1:t1", "alpha")], [pane("w1:p1", "w1:t1")])
    p = tc.patches(s, {"w1:t1": 4})
    assert p == [("w1:p1", {"tabc4": "alpha"}, [])]


def test_no_patch_when_tokens_already_correct():
    s = snap([tab("w1:t1", "alpha")], [pane("w1:p1", "w1:t1", {"tabc4": "alpha", "other": "x"})])
    assert tc.patches(s, {"w1:t1": 4}) == []


def test_moved_pane_clears_old_slot_and_sets_new():
    s = snap([tab("w1:t2", "beta")], [pane("w1:p1", "w1:t2", {"tabc1": "alpha", "model": "opus"})])
    assert tc.patches(s, {"w1:t2": 2}) == [("w1:p1", {"tabc2": "beta"}, ["tabc1"])]


def test_renamed_tab_updates_the_value():
    s = snap([tab("w1:t1", "new")], [pane("w1:p1", "w1:t1", {"tabc4": "old"})])
    assert tc.patches(s, {"w1:t1": 4}) == [("w1:p1", {"tabc4": "new"}, [])]


def test_foreign_tokens_are_never_cleared():
    s = snap([tab("w1:t1", "a")], [pane("w1:p1", "w1:t1", {"tabcolour": "x", "tabc": "y", "tabcx": "z"})])
    assert tc.patches(s, {"w1:t1": 1}) == [("w1:p1", {"tabc1": "a"}, [])]


def test_empty_label_falls_back_to_tab_number():
    s = snap([tab("w1:t3", "  ", number=3)], [pane("w1:p1", "w1:t3")])
    assert tc.patches(s, {"w1:t3": 1}) == [("w1:p1", {"tabc1": "3"}, [])]


def test_label_is_normalised_like_herdr_so_it_converges():
    long = "x" * 100
    s = snap([tab("w1:t1", f" {long}\x07 ")], [pane("w1:p1", "w1:t1", {"tabc1": "x" * 80})])
    assert tc.patches(s, {"w1:t1": 1}) == []


def test_a_sleeping_tab_keeps_its_colour_pin_and_shown_label():
    """heelr sleep puts '💤 ' before a sleeping tab's label: the tab is still the same tab."""
    pins = [("end-game", "#b8bb26")]
    awake = [tab("w1:t1", "end-game"), tab("w1:t2", "docs")]
    asleep = [tab("w1:t1", "💤 end-game"), tab("w1:t2", "💤 docs")]
    a_slots, a_state = tc.assign(awake, {}, pins)
    s_slots, _ = tc.assign(asleep, a_state, pins)
    assert s_slots == a_slots and a_slots["w1:t1"] == 0
    s = snap(asleep, [pane("w1:p1", "w1:t1"), pane("w1:p2", "w1:t2")])
    assert [p[1] for p in tc.patches(s, s_slots)] == [{"tabc0": "end-game"}, {f"tabc{s_slots['w1:t2']}": "docs"}]


def test_pane_of_unknown_tab_is_skipped():
    s = snap([], [pane("w1:p1", "w1:t9")])
    assert tc.patches(s, {}) == []


# --- run (mocked herdr) ----------------------------------------------------------------------

class FakeHerdr:
    def __init__(self, snapshot, fail_snapshot=False):
        self.snapshot, self.fail = snapshot, fail_snapshot
        self.calls = []

    def __call__(self, *args):
        self.calls.append(list(args))
        if args[:2] == ("api", "snapshot"):
            if self.fail:
                return subprocess.CompletedProcess(args, 1, "", "no server")
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"result": {"snapshot": self.snapshot}}), "")
        if args[:2] == ("pane", "report-metadata"):
            pid, rest = args[2], args[3:]
            p = next(p for p in self.snapshot["panes"] if p["pane_id"] == pid)
            toks = p.setdefault("tokens", {})
            it = iter(rest)
            for flag in it:
                val = next(it)
                if flag == "--token":
                    k, v = val.split("=", 1)
                    toks[k] = v
                elif flag == "--clear-token":
                    toks.pop(val, None)
            return subprocess.CompletedProcess(args, 0, "{}", "")
        raise AssertionError(f"unexpected herdr call {args}")

    def reports(self):
        return [c for c in self.calls if c[:2] == ["pane", "report-metadata"]]


def test_run_reports_tokens_and_is_idempotent(tmp_path):
    s = snap([tab("w1:t1", "alpha"), tab("w1:t2", "beta")],
             [pane("w1:p1", "w1:t1"), pane("w1:p2", "w1:t2"), pane("w1:p3", "w1:t2")])
    fake = FakeHerdr(s)
    state = tmp_path / "state.json"
    tc.run(fake, state)
    reps = fake.reports()
    assert len(reps) == 3
    assert all(r[3:5] == ["--source", tc.SOURCE] for r in reps)
    toks = {p["pane_id"]: p["tokens"] for p in s["panes"]}
    assert toks["w1:p2"] == toks["w1:p3"] != toks["w1:p1"]
    assert json.loads(state.read_text())["tabs"].keys() == {"w1:t1", "w1:t2"}
    fake.calls.clear()
    tc.run(fake, state)
    assert fake.reports() == []


def test_run_after_pane_move_recolours_only_that_pane(tmp_path):
    s = snap([tab("w1:t1", "alpha"), tab("w1:t2", "beta")],
             [pane("w1:p1", "w1:t1"), pane("w1:p2", "w1:t2")])
    fake = FakeHerdr(s)
    state = tmp_path / "state.json"
    tc.run(fake, state)
    s["panes"][0]["tab_id"] = "w1:t2"
    s["tabs"] = [t for t in s["tabs"] if t["tab_id"] == "w1:t2"]  # moving the only pane closes t1
    fake.calls.clear()
    tc.run(fake, state)
    assert [r[2] for r in fake.reports()] == ["w1:p1"]
    assert s["panes"][0]["tokens"] == s["panes"][1]["tokens"]


def test_run_survives_snapshot_failure(tmp_path):
    fake = FakeHerdr(snap([], []), fail_snapshot=True)
    tc.run(fake, tmp_path / "state.json")
    assert fake.reports() == []
    assert not (tmp_path / "state.json").exists()


def test_run_tolerates_corrupt_state(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{not json")
    fake = FakeHerdr(snap([tab("w1:t1", "a")], [pane("w1:p1", "w1:t1")]))
    tc.run(fake, state)
    assert len(fake.reports()) == 1


def test_herdr_runner_uses_herdr_bin_path(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("HERDR_BIN_PATH", "/opt/x/herdr")
    monkeypatch.setattr(tc.subprocess, "run", fake_run)
    tc.herdr("api", "snapshot")
    assert seen["argv"] == ["/opt/x/herdr", "api", "snapshot"]


def test_state_path_prefers_plugin_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))
    assert tc.state_path() == tmp_path / "slots.json"
    monkeypatch.delenv("HERDR_PLUGIN_STATE_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    # run by hand: the same dir Herdr gives the plugin, so both share one slot table
    assert tc.state_path() == tmp_path / ".local/state/herdr/plugins/heelr.tab-colours/slots.json"


# --- pins -----------------------------------------------------------------------------------

PINS = [("coordinator", "#fabd2f"), ("overflow", "#928374")]


def test_pinned_tab_gets_slot_zero_and_uses_no_colour_slot():
    tabs = [tab("w1:t1", "coordinator-2"), tab("w1:t2", "a")]
    slots, state = tc.assign(tabs, {}, PINS)
    assert slots == {"w1:t1": 0, "w1:t2": 1}
    assert "w1:t1" not in state["tabs"]


def test_auto_slots_avoid_colours_that_a_pin_uses():
    yellow = tc.PALETTE.index("#fabd2f") + 1
    tabs = [tab(f"w1:t{i}", f"x{i}") for i in range(1, N)]  # N-1 tabs: room to skip yellow
    slots, _ = tc.assign(tabs, {}, PINS)
    assert yellow not in slots.values()


def test_avoided_colour_is_still_used_once_the_others_are_taken():
    tabs = [tab(f"w1:t{i}", f"x{i}") for i in range(1, N + 1)]
    slots, _ = tc.assign(tabs, {}, PINS)
    assert sorted(slots.values()) == list(range(1, N + 1))


def test_a_tab_that_loses_its_pin_prefix_gets_a_colour_slot():
    _, state = tc.assign([tab("w1:t1", "overflow")], {}, PINS)
    slots, _ = tc.assign([tab("w1:t1", "renamed")], state, PINS)
    assert slots["w1:t1"] >= 1


def test_load_pins_reads_the_plugin_config(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('[pins]\n"coordinator" = "#fabd2f"\noverflow = "#928374"\n')
    assert tc.load_pins(f) == PINS
    assert tc.load_pins(tmp_path / "missing.toml") == []


def test_load_pins_rejects_a_bad_colour(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('[pins]\nx = "red"\n')
    with pytest.raises(ValueError):
        tc.load_pins(f)


def test_config_path_prefers_plugin_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path))
    assert tc.config_path() == tmp_path / "config.toml"
    monkeypatch.delenv("HERDR_PLUGIN_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert tc.config_path() == tmp_path / ".config/herdr/plugins/config/heelr.tab-colours/config.toml"


def test_run_applies_pins_from_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[pins]\noverflow = "#928374"\n')
    s = snap([tab("w1:t1", "overflow")], [pane("w1:p1", "w1:t1")])
    tc.run(FakeHerdr(s), tmp_path / "state.json", cfg)
    assert s["panes"][0]["tokens"] == {"tabc0": "overflow"}


# --- sidebar config generator ----------------------------------------------------------------

def _row(cfg_text):
    return tomllib.loads(cfg_text)["ui"]["sidebar"]["agents"]["rows"]


def test_sidebar_toml_has_one_styled_token_per_slot_then_state_text():
    rows = _row(tc.sidebar_toml())
    assert rows[0] == ["state_icon", "terminal_title_stripped"]
    toks = rows[1]
    assert [t["token"] for t in toks] == [f"$tabc{i}" for i in range(1, N + 1)] + ["state_text"]
    assert [t["fg"] for t in toks[:-1]] == tc.PALETTE
    assert all("rules" not in t for t in toks)
    assert len(set(tc.PALETTE)) == N


def test_sidebar_toml_pins_become_rules_on_a_leading_slot_zero_token():
    toks = _row(tc.sidebar_toml(PINS))[1]
    assert toks[0]["token"] == "$tabc0"
    assert toks[0]["rules"] == [{"starts_with": p, "fg": fg} for p, fg in PINS]
    assert all("rules" not in t for t in toks[1:])
    assert len(toks) == N + 2 <= 16


def test_example_files_match_the_generator():
    pins = tc.load_pins(PLUGIN / "config.example.toml")
    assert pins == [("coordinator", "#fabd2f"), ("review", "#8ec07c"), ("overflow", "#928374")]
    assert (PLUGIN / "sidebar.example.toml").read_text() == tc.sidebar_toml(pins)


def test_main_sidebar_prints_toml_for_a_config(capsys, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[pins]\nover = "#928374"\n')
    assert tc.main(["sidebar", "--config", str(cfg)]) == 0
    toks = _row(capsys.readouterr().out)[1]
    assert toks[0]["rules"] == [{"starts_with": "over", "fg": "#928374"}]


# --- manifest --------------------------------------------------------------------------------

COLOUR = ["python3", "tab_colours.py"]
AUTO = ["../../bin/heelr", "auto"]
MARK = ["../../bin/heelr", "drift-mark"]


def test_manifest_hooks_the_events_that_change_tab_membership_or_labels():
    m = tomllib.loads((PLUGIN / "herdr-plugin.toml").read_text())
    # the id keys Herdr's state and config dirs for the slot memory and pins: never change it
    assert m["id"] == "heelr.tab-colours" and m["min_herdr_version"]
    assert m["startup"] == [{"command": COLOUR}]
    ons = {e["on"] for e in m["events"] if e["command"] == COLOUR}
    assert ons == {"tab.created", "tab.renamed", "tab.closed", "pane.created", "pane.moved"}
    assert "layout.updated" not in {e["on"] for e in m["events"]}  # rejected by 0.9.3
    assert {e["command"][0] for e in m["events"]} <= {COLOUR[0], AUTO[0]}


def test_manifest_runs_no_hook_on_focus_changes():
    """A tab's ↗N counts the focused pane too, so a focus change never changes one: no hook for it."""
    m = tomllib.loads((PLUGIN / "herdr-plugin.toml").read_text())
    assert not any(e["command"] == MARK for e in m["events"])
    assert not {"pane.focused", "tab.focused", "workspace.focused"} & {e["on"] for e in m["events"]}
    assert "tab.renamed" not in {e["on"] for e in m["events"] if e["command"] == AUTO}   # no rename loop


def test_manifest_runs_this_checkouts_heelr_auto_when_panes_open_close_or_move():
    m = tomllib.loads((PLUGIN / "herdr-plugin.toml").read_text())
    assert m["name"] == "heelr"
    assert {e["on"] for e in m["events"] if e["command"] == AUTO} == {"pane.created", "pane.closed", "pane.moved"}
    heelr_bin = (PLUGIN / AUTO[0]).resolve()        # hooks run in the plugin root
    assert heelr_bin == (PLUGIN.parents[1] / "bin" / "heelr").resolve() and os.access(heelr_bin, os.X_OK)


def test_main_without_args_reconciles_into_the_plugin_state_dir(monkeypatch, tmp_path):
    fake = FakeHerdr(snap([tab("w1:t1", "a")], [pane("w1:p1", "w1:t1")]))
    monkeypatch.setattr(tc, "herdr", fake)
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    assert tc.main([]) == 0
    assert len(fake.reports()) == 1
    assert (tmp_path / "st" / "slots.json").exists()


# --- review E2: a bad plugin config must not break event handling ------------------------------

@pytest.mark.parametrize("body", ['[pins\n', '[pins]\nx = "red"\n', 'pins = "x"\n'])
def test_run_survives_a_bad_plugin_config(tmp_path, capsys, body):
    cfg = tmp_path / "config.toml"
    cfg.write_text(body)
    s = snap([tab("w1:t1", "a")], [pane("w1:p1", "w1:t1")])
    tc.run(FakeHerdr(s), tmp_path / "state.json", cfg)
    assert s["panes"][0]["tokens"] == {"tabc1": "a"}
    err = capsys.readouterr().err
    assert "config" in err and len(err.strip().splitlines()) == 1


def test_sidebar_command_stays_strict_about_a_bad_config(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[pins]\nx = "red"\n')
    with pytest.raises(ValueError):
        tc.main(["sidebar", "--config", str(cfg)])


# --- review E minors --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    '[1, 2]',
    '{"tabs": 5, "labels": []}',
    '{"tabs": {"w1:t1": "x"}, "labels": {"a": null}}',
])
def test_state_of_the_wrong_shape_is_ignored(tmp_path, text):
    f = tmp_path / "state.json"
    f.write_text(text)
    s = snap([tab("w1:t1", "a")], [pane("w1:p1", "w1:t1")])
    tc.run(FakeHerdr(s), f)
    assert s["panes"][0]["tokens"] == {"tabc1": "a"}


def test_tabs_without_an_id_or_with_a_non_string_label_do_not_crash(tmp_path):
    tabs = [tab("w1:t1", "a"), {"label": "no id", "number": 2}, {"tab_id": "w1:t3", "label": 7, "number": 3}]
    s = snap(tabs, [pane("w1:p1", "w1:t1"), pane("w1:p3", "w1:t3")])
    tc.run(FakeHerdr(s), tmp_path / "state.json")
    assert s["panes"][0]["tokens"] == {"tabc1": "a"}
    assert s["panes"][1]["tokens"] == {"tabc2": "3"}  # falls back to the tab number


def test_an_empty_pin_prefix_pins_nothing():
    slots, _ = tc.assign([tab("w1:t1", "a")], {}, [("", "#fabd2f")])
    assert slots == {"w1:t1": 1}


def test_load_pins_drops_empty_prefixes(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('[pins]\n"" = "#fabd2f"\nx = "#928374"\n')
    assert tc.load_pins(f) == [("x", "#928374")]


def test_heelrs_temporary_park_tab_gets_no_colour(tmp_path):
    s = snap([tab("w1:t1", "a"), tab("w1:t2", "heelr-park", 2)],
             [pane("w1:p1", "w1:t1"), pane("w1:p2", "w1:t2")])
    tc.run(FakeHerdr(s), tmp_path / "state.json")
    assert s["panes"][0]["tokens"] == {"tabc1": "a"}
    assert "tokens" not in s["panes"][1]


def test_state_file_keeps_non_ascii_labels_readable(tmp_path):
    s = snap([tab("w1:t1", "café")], [pane("w1:p1", "w1:t1")])
    f = tmp_path / "state.json"
    tc.run(FakeHerdr(s), f)
    assert "café" in f.read_text(encoding="utf-8")
    assert json.loads(f.read_text(encoding="utf-8"))["labels"] == {"café": 1}


# --- a burst of events costs at most two reconciles, and no run waits on the lock ---------------

def _hold(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path.with_suffix(".lock"), "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def test_a_run_that_finds_the_lock_busy_marks_it_and_returns_without_waiting(tmp_path):
    path = tmp_path / "slots.json"
    held, calls = _hold(path), []
    assert tc.coalesced(path, lambda: calls.append(1)) is False
    assert calls == [] and path.with_suffix(".dirty").exists()
    held.close()
    assert tc.coalesced(path, lambda: calls.append(1)) is True
    assert calls == [1] and not path.with_suffix(".dirty").exists()


def test_a_burst_of_busy_runs_collapses_into_one(tmp_path):
    path = tmp_path / "slots.json"
    held, calls = _hold(path), []
    for _ in range(5):
        tc.coalesced(path, lambda: calls.append(1))
    held.close()
    tc.coalesced(path, lambda: calls.append(1))
    assert calls == [1]


def test_an_event_during_a_run_makes_the_holder_run_once_more(tmp_path):
    path = tmp_path / "slots.json"
    calls = []

    def work():
        calls.append(1)
        if len(calls) == 1:  # another hook fires mid-run: the lock is busy, so it only marks
            assert tc.coalesced(path, lambda: calls.append("waiter")) is False

    assert tc.coalesced(path, work) is True
    assert calls == [1, 1]


def test_main_reconciles_through_the_coalescing_lock(monkeypatch, tmp_path):
    fake = FakeHerdr(snap([tab("w1:t1", "a")], [pane("w1:p1", "w1:t1")]))
    monkeypatch.setattr(tc, "herdr", fake)
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.setenv("HERDR_PLUGIN_CONFIG_DIR", str(tmp_path / "cfg"))
    held = _hold(tmp_path / "st" / "slots.json")
    assert tc.main([]) == 0
    assert fake.calls == []  # busy: marked for the holder, no snapshot taken
    held.close()


def test_a_mark_left_just_before_the_unlock_is_not_lost(monkeypatch, tmp_path):
    """A hook that marks and fails to lock just before the holder unlocks: the holder runs again."""
    import fcntl
    path, calls, real = tmp_path / "slots.json", [], fcntl.flock
    state = {"raced": False}

    def flock(f, op):
        if op == fcntl.LOCK_UN and not state["raced"]:
            state["raced"] = True
            path.with_suffix(".dirty").touch()
        return real(f, op)

    monkeypatch.setattr(tc.fcntl, "flock", flock)
    assert tc.coalesced(path, lambda: calls.append(1)) is True
    assert calls == [1, 1]


def test_concurrent_hooks_all_exit_and_leave_no_mark(tmp_path, real_subprocess):
    import sys
    path, log = tmp_path / "slots.json", tmp_path / "runs.log"
    code = (f"import importlib.util, time\n"
            f"spec = importlib.util.spec_from_file_location('tc', {str(PLUGIN / 'tab_colours.py')!r})\n"
            f"tc = importlib.util.module_from_spec(spec); spec.loader.exec_module(tc)\n"
            f"from pathlib import Path\n"
            f"def work():\n"
            f"    time.sleep(0.05)\n"
            f"    with open({str(log)!r}, 'a') as f: f.write('run\\n')\n"
            f"tc.coalesced(Path({str(path)!r}), work)\n")
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(8)]
    assert [p.wait(timeout=30) for p in procs] == [0] * 8
    runs = log.read_text().count("run")
    assert 1 <= runs < 8
    assert not path.with_suffix(".dirty").exists()


# --- a cold restart can hand old tab ids to different tabs ----------------------------------------

def test_reused_tab_ids_follow_their_labels_not_their_ids():
    _, state = tc.assign([tab("w1:t1", "alpha"), tab("w1:t2", "beta")], {})
    slots, _ = tc.assign([tab("w1:t1", "beta"), tab("w1:t2", "alpha")], state)
    assert slots == {"w1:t1": state["tabs"]["w1:t2"], "w1:t2": state["tabs"]["w1:t1"]}


def test_a_renamed_tab_keeps_its_slot_even_if_its_new_label_was_seen_before():
    _, state = tc.assign([tab("w1:t1", "alpha"), tab("w1:t2", "beta")], {})
    _, state = tc.assign([tab("w1:t2", "beta")], state)          # alpha closes; label remembered
    slots, _ = tc.assign([tab("w1:t2", "beta"), tab("w1:t3", "gamma")], state)
    slots, _ = tc.assign([tab("w1:t2", "alpha"), tab("w1:t3", "gamma")], state)  # beta renamed
    assert slots["w1:t2"] == state["tabs"]["w1:t2"]


def test_state_from_before_ids_were_recorded_still_keeps_slots():
    slots, state = tc.assign([tab("w1:t1", "alpha")], {"tabs": {"w1:t1": 3}, "labels": {}})
    assert slots == {"w1:t1": 3}
    assert state["ids"] == {"w1:t1": "alpha"}


def test_an_id_whose_old_label_is_gone_keeps_its_slot_through_a_restart():
    """Only a label that moved to another tab says the id was reused; a closed tab's id stays put."""
    _, state = tc.assign([tab("w1:t1", "alpha"), tab("w1:t2", "beta")], {})
    slots, _ = tc.assign([tab("w1:t1", "gamma"), tab("w1:t2", "beta")], state)  # alpha didn't return
    assert slots["w1:t1"] == state["tabs"]["w1:t1"]


def test_a_renamed_tab_keeps_its_slot_when_its_old_label_moved_to_a_new_tab():
    """The old label being elsewhere is not enough: this tab's label must have been another id's."""
    _, state = tc.assign([tab("w1:t1", "alpha"), tab("w1:t2", "beta")], {})
    slots, _ = tc.assign([tab("w1:t1", "fresh"), tab("w1:t2", "beta"), tab("w1:t3", "alpha")], state)
    assert slots["w1:t1"] == state["tabs"]["w1:t1"]


def test_a_rename_into_a_closed_tabs_old_label_keeps_the_slot():
    """tab-autoname reuses names all the time. Between two runs (coalesced, say) beta closes and
    alpha is renamed beta: that is a rename, not a restart that reused the id."""
    _, state = tc.assign([tab("w1:t1", "alpha"), tab("w1:t2", "beta")], {})
    slots, _ = tc.assign([tab("w1:t1", "beta")], state)
    assert slots["w1:t1"] == state["tabs"]["w1:t1"]
