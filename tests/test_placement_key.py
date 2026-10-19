# tests/test_placement_key.py
import json
import os
import subprocess

import pytest

from heelr import facts, key
from heelr.facts import GitFacts
from test_placement_facts import make_repo, make_worktree, state  # noqa: F401

ROLES = [{"name": "agent", "agent": True}, {"name": "shell", "fallback": True}]


def sp(pane="w1:p1", cwd="/", agent=None):
    return {"pane_id": pane, "cwd": cwd, "foreground_cwd": cwd, "agent": agent,
            "agent_status": None, "terminal_title": ""}


def test_defaults_are_off(tmp_path):
    assert key.load_config(tmp_path / "missing.toml").enabled is False
    (tmp_path / "bad.toml").write_text("not = [toml")
    assert key.load_config(tmp_path / "bad.toml").enabled is False


def test_ticket_extraction():
    assert key.extract_ticket("fix/GH-330-crash", r"GH-\d+") == "GH-330"
    assert key.extract_ticket("feat-ABC-12", r"([A-Z]+-\d+)") == "ABC-12"   # group 1 when present
    assert key.extract_ticket("tab-sleep", r"GH-\d+") == "tab-sleep"         # no match keeps it whole
    assert key.extract_ticket("anything", None) == "anything"


def test_task_chain_tag_beats_report_beats_worktree(state):
    r = make_repo(state / "heelr")
    wt = make_worktree(r, "GH-330", "fix/GH-330")
    cfg = key.PlacementConfig(enabled=True, ticket=r"GH-\d+")
    assert key.pane_key(sp(cwd=str(wt)), cfg, ROLES) == key.Key("shell", "heelr", "GH-330")
    facts.save_report("w1:p1", {"worktree": "GH-9-x"})
    assert key.pane_key(sp(cwd=str(wt)), cfg, ROLES).task == "GH-9"
    facts.set_tag("w1:p1", "GH-1")
    assert key.pane_key(sp(cwd=str(wt)), cfg, ROLES).task == "GH-1"


def test_main_checkout_on_default_branch_is_main(state):
    r = make_repo(state / "heelr")
    assert key.pane_key(sp(cwd=str(r)), key.PlacementConfig(), ROLES) == key.Key("shell", "heelr", "main")


def test_feature_branch_in_main_checkout_uses_branch(state):
    r = make_repo(state / "heelr", branch="GH-5-thing")
    assert key.pane_key(sp(cwd=str(r)), key.PlacementConfig(ticket=r"GH-\d+"), ROLES).task == "GH-5"


def test_not_in_git_is_unsorted(state):
    (state / "notes").mkdir()
    k = key.pane_key(sp(cwd=str(state / "notes")), key.PlacementConfig(), ROLES)
    assert k == key.Key("shell", "notes", key.UNSORTED)


def test_repo_config_overrides_name_and_ticket(state):
    r = make_repo(state / "heelr", branch="FAD-12-x")
    (r / ".heelr.toml").write_text('name = "hl"\nticket = \'FAD-\\d+\'\n')
    k = key.pane_key(sp(cwd=str(r)), key.PlacementConfig(ticket=r"GH-\d+"), ROLES)
    assert (k.repo, k.task) == ("hl", "FAD-12")


def test_classify_fields_win_and_missing_fall_back(state, monkeypatch):
    r = make_repo(state / "heelr")
    monkeypatch.setattr(key, "_run_script", lambda s, d, timeout=1.0: ({"task": "X"}, True))
    k = key.pane_key(sp(cwd=str(r)), key.PlacementConfig(classify="c.sh"), ROLES)
    assert (k.repo, k.task) == ("heelr", "X")


class FakeProc:
    """A Popen stand-in with real fds so select works: stdout is a file holding `out`."""
    def __init__(self, tmp_path, out, rc, hang=False):
        f = tmp_path / "out"
        f.write_bytes(out.encode())
        self.stdout, self.stdin = open(f, "rb"), open(tmp_path / "in", "wb")
        self.pid, self.returncode, self.hang = 99999, rc, hang
        self.killed = False

    def wait(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("c.sh", timeout)
        return self.returncode

    def kill(self):
        self.killed = True


def fake_popen(monkeypatch, tmp_path, out, rc, hang=False):
    proc = FakeProc(tmp_path, out, rc, hang)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: setattr(proc, "killed", True))
    return proc


BIG = json.dumps({"task": "x" * 200_000})


@pytest.mark.parametrize("out,rc", [("not json", 0), ("[1,2]", 0), ('{"task": 3}', 0), ("{}", 1),
                                    ("x" * 200_000, 0), (BIG, 0)])
def test_classify_bad_output_is_empty(monkeypatch, tmp_path, out, rc):   # Review Focus 4
    fake_popen(monkeypatch, tmp_path, out, rc)
    assert key.run_classify("c.sh", {"pane_id": "p"}) == {}


def test_classify_output_cap_kills_the_group(monkeypatch, tmp_path):
    proc = fake_popen(monkeypatch, tmp_path, BIG, 0)
    assert key._run_script("c.sh", {"pane_id": "p"}) == ({}, False)
    assert proc.killed


def test_classify_hang_after_closing_stdout_is_empty(monkeypatch, tmp_path):
    proc = fake_popen(monkeypatch, tmp_path, '{"task": "T"}', 0, hang=True)
    assert key._run_script("c.sh", {"pane_id": "p"}, timeout=0.2) == ({}, False)
    assert proc.killed


def test_classify_oserror_is_empty_and_transient(monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert key._run_script("c.sh", {"pane_id": "p"}) == ({}, False)


def test_classify_unusable_output_is_cacheable(monkeypatch, tmp_path):
    fake_popen(monkeypatch, tmp_path, "[1]", 0)
    assert key._run_script("c.sh", {"pane_id": "p"}) == ({}, True)


def test_classify_is_cached_by_input(state, monkeypatch):
    r = make_repo(state / "heelr")
    calls = []
    monkeypatch.setattr(key, "_run_script", lambda s, d, timeout=1.0: (calls.append(1) or {"task": "T"}, True))
    cfg = key.PlacementConfig(classify="c.sh")
    key.pane_key(sp(cwd=str(r)), cfg, ROLES)
    key.pane_key(sp(cwd=str(r)), cfg, ROLES)
    assert len(calls) == 1
    key.pane_key(sp(cwd=str(r), agent="claude"), cfg, ROLES)   # facts changed: run again
    assert len(calls) == 2


# ---- Review Focus 4 with real scripts: hang, megabytes, JSON list, bad exit; always {} and fast

def script(tmp_path, body):
    p = tmp_path / "c.sh"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return str(p)


@pytest.mark.parametrize("body", [
    "sleep 30",                       # hangs
    "sleep 30 &\nsleep 30",           # hangs, with a child holding the pipe
    "exec yes",                       # endless output
    "head -c 5000000 /dev/zero | tr '\\0' x",   # megabytes
    "echo '[\"a\"]'",                 # a JSON list
    "echo '{\"task\": \"T\"}'; exit 3",   # failing exit
    "printf '{\"task\": \"'; head -c 100000 /dev/zero | tr '\\0' x; echo '\"}'",   # valid JSON, over the cap
    "exec 1>&-; sleep 30",            # closes stdout, keeps running
])
def test_real_bad_scripts_give_empty_fast(tmp_path, real_subprocess, real_signals, body):
    import time
    t = time.monotonic()
    assert key.run_classify(script(tmp_path, body), {"pane_id": "p"}) == {}
    assert time.monotonic() - t < 2.5


def test_real_good_script_and_stdin(tmp_path, real_subprocess, real_signals):
    s = script(tmp_path, 'cat >/dev/null; echo \'{"task": "T", "repo": "r", "junk": "x"}\'')
    assert key.run_classify(s, {"pane_id": "p"}) == {"task": "T", "repo": "r"}


def test_missing_script_is_empty(real_subprocess):
    assert key.run_classify("/nonexistent/c.sh", {"pane_id": "p"}) == {}


def test_real_hang_is_killed_with_its_children(tmp_path, real_subprocess, real_signals):
    marker = tmp_path / "alive"
    s = script(tmp_path, f"(sleep 1.5; touch {marker}) &\nsleep 30")
    assert key.run_classify(s, {"pane_id": "p"}, timeout=0.3) == {}
    import time
    time.sleep(2)
    assert not marker.exists()      # the background child died with the group


def test_real_large_stdin_does_not_deadlock(tmp_path, real_subprocess, real_signals):
    s = script(tmp_path, "echo '{\"task\": \"T\"}'")    # never reads stdin
    assert key.run_classify(s, {"pane_id": "p", "pad": "x" * 500_000}, timeout=2.0) == {"task": "T"}


def test_timeout_is_not_cached_but_bad_output_is(state, monkeypatch):
    r = make_repo(state / "heelr")
    cfg = key.PlacementConfig(classify="c.sh")
    calls = []
    monkeypatch.setattr(key, "_run_script", lambda s, d, timeout=1.0: (calls.append(1) or {}, False))
    key.pane_key(sp(cwd=str(r)), cfg, ROLES)
    key.pane_key(sp(cwd=str(r)), cfg, ROLES)
    assert len(calls) == 2
    calls.clear()
    monkeypatch.setattr(key, "_run_script", lambda s, d, timeout=1.0: (calls.append(1) or {}, True))
    key._cache_path().unlink(missing_ok=True)
    key.pane_key(sp(cwd=str(r), agent="a"), cfg, ROLES)
    key.pane_key(sp(cwd=str(r), agent="a"), cfg, ROLES)
    assert len(calls) == 1


@pytest.mark.parametrize("toml", ['placement = "x"', "[placement]\nburst_window = \"fast\"",
                                  "[placement.shape]\nby_tab = [1]", "[placement]\nenabled = true\nshape = 3",
                                  "[placement]\nenabled = true\nworkspace = 5"])
def test_wrong_typed_config_gives_defaults(tmp_path, toml):
    p = tmp_path / "p.toml"
    p.write_text(toml)
    assert key.load_config(p) == key.PlacementConfig()


def test_cache_with_wrong_shapes_is_a_miss(state, monkeypatch):
    r = make_repo(state / "heelr")
    cfg = key.PlacementConfig(classify="c.sh")
    monkeypatch.setattr(key, "_run_script", lambda s, d, timeout=1.0: ({"task": "T"}, True))
    for bad in ('[1]', '{"w1:p1": 5}', '{"w1:p1": {"hash": 1, "out": 7}}', "{not json"):
        core_state = key._cache_path()
        core_state.parent.mkdir(parents=True, exist_ok=True)
        core_state.write_text(bad)
        assert key.pane_key(sp(cwd=str(r)), cfg, ROLES).task == "T"


def test_cache_hit_with_non_dict_out_is_a_miss(state, monkeypatch):
    r = make_repo(state / "heelr")
    cfg = key.PlacementConfig(classify="c.sh")
    monkeypatch.setattr(key, "_run_script", lambda s, d, timeout=1.0: ({"task": "T"}, True))
    key.pane_key(sp(cwd=str(r)), cfg, ROLES)
    c = json.loads(key._cache_path().read_text())
    c["w1:p1"]["out"] = ["x"]
    key._cache_path().write_text(json.dumps(c))
    assert key.pane_key(sp(cwd=str(r)), cfg, ROLES).task == "T"


@pytest.mark.parametrize("body", ["name = 3", "name = [1]\nticket = 'x'", "not = [toml"])
def test_repo_config_bad_values_give_empty(tmp_path, body):
    (tmp_path / ".heelr.toml").write_text(body)
    assert key.repo_config(str(tmp_path)) == {}


def test_ticket_optional_group_that_did_not_match():
    assert key.extract_ticket("abc", r"abc(-\d+)?") == "abc"


def test_task_from_report_branch(state):
    r = make_repo(state / "heelr")
    facts.save_report("w1:p1", {"branch": "fix/GH-7-x"})
    k = key.pane_key(sp(cwd=str(r)), key.PlacementConfig(ticket=r"GH-\d+"), ROLES)
    assert k.task == "GH-7"
    facts.save_report("w1:p1", {"task": "GH-8", "branch": "GH-7-x"})
    assert key.pane_key(sp(cwd=str(r)), key.PlacementConfig(), ROLES).task == "GH-8"


@pytest.mark.parametrize("out,rc,why", [("not json", 0, "not JSON"), ("[1]", 0, "JSON object"), ("{}", 3, "exited 3"),
                                        (BIG, 0, "size cap")])
def test_classify_failures_are_noted_with_a_reason(monkeypatch, tmp_path, out, rc, why):    # G4c
    key.take_failures()
    fake_popen(monkeypatch, tmp_path, out, rc)
    key.run_classify("c.sh", {"pane_id": "p"})
    (f,) = key.take_failures()
    assert "c.sh" in f and why in f
    assert key.take_failures() == []


def test_spawn_error_and_timeout_are_noted(monkeypatch, tmp_path):                         # G4c
    key.take_failures()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    key.run_classify("c.sh", {"pane_id": "p"})
    assert "could not start" in key.take_failures()[0]
    fake_popen(monkeypatch, tmp_path, '{"task": "T"}', 0, hang=True)
    key.run_classify("c.sh", {"pane_id": "p"}, timeout=0.2)
    assert "timed out" in key.take_failures()[0]


def test_a_good_run_notes_nothing(monkeypatch, tmp_path):                                    # G4c
    key.take_failures()
    fake_popen(monkeypatch, tmp_path, '{"task": "T"}', 0)
    assert key.run_classify("c.sh", {"pane_id": "p"}) == {"task": "T"}
    assert key.take_failures() == []


@pytest.mark.parametrize("where,exc", [("select", OSError), ("select", ValueError), ("read", OSError),
                                       ("set_blocking", OSError), ("set_blocking", ValueError)])
def test_io_errors_in_the_classify_pipe_never_raise(monkeypatch, tmp_path, where, exc):     # G7
    fake_popen(monkeypatch, tmp_path, '{"task": "T"}', 0)

    def boom(*a, **k):
        raise exc("boom")
    target = {"select": (key.select, "select"), "read": (os, "read"), "set_blocking": (os, "set_blocking")}[where]
    monkeypatch.setattr(*target, boom)
    assert key._run_script("c.sh", {"pane_id": "p"}) == ({}, False)
    assert key.run_classify("c.sh", {"pane_id": "p"}) == {}


@pytest.mark.parametrize("bad", [{"x": object()}, {"x": float("nan"), "y": {1, 2}}])
def test_unserialisable_stdin_never_raises(monkeypatch, tmp_path, bad):                      # G7
    fake_popen(monkeypatch, tmp_path, '{"task": "T"}', 0)
    assert key._run_script("c.sh", bad) == ({}, False)


@pytest.mark.parametrize("raw,want", [("0.75", 0.75), ("0", 0.0), ("5", 5.0), ("2.5", 2.5), ("-1", 0.75), ("5.01", 0.75),
                                      ("1000", 0.75), ("nan", 0.75), ("inf", 0.75), ("-inf", 0.75)])
def test_burst_window_outside_zero_to_five_is_the_default(tmp_path, raw, want):            # G6
    f = tmp_path / "placement.toml"
    f.write_text(f"[placement]\nenabled = true\nburst_window = {raw}\n")
    c = key.load_config(f)
    assert c.enabled and c.burst_window == want
