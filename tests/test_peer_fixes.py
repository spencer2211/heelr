"""Fixes from the peer review of the placement branch (P1-P5, m1-m15)."""
import json
import time

import pytest

from pathlib import Path

from heelr import cli, core, facts, herdr, multi
from test_apply_safety import fake, heelr  # noqa: F401


# ------------------------------------------------------------------ P1

def _created_pane_moved_cross_workspace(fake):
    tb, (q,) = fake.add_tab("w9", "x", 1)
    tab, _ = fake.add_tab("w1", "main", 2, d="down")
    assert heelr("apply", "main-left", "--tab", tab) == 0
    new = fake.call("pane", "split", fake.panes(tab)[0], "--direction", "down", "--ratio", "0.5")["pane"]["pane_id"]
    core.add_created(tab, [{"pane": new, "command": None}])
    moved = herdr.move(new, tab=tb, target=q, split="right")["pane"]["pane_id"]
    assert moved != new
    facts.rekey(new, moved)                    # what the pane.moved hook does: the record follows the id
    return tab, tb, moved


def test_back_never_closes_a_created_pane_that_left_the_workspace(fake):          # P1
    tab, tb, moved = _created_pane_moved_cross_workspace(fake)
    assert heelr("back", "--tab", tab) in (0, 1, 3)
    assert moved in fake.all_panes() and fake.tab_of(moved) == tb


def test_multi_back_never_closes_a_created_pane_that_left_the_workspace(fake):    # P1
    from test_apply_safety import write_layout
    tb, (q,) = fake.add_tab("w9", "x", 1)
    t1, _ = fake.add_tab("w1", "one", 2, agents=(0, 1))
    write_layout("solo", 'name = "solo"\nkind = "multi"\n[[tabs]]\nlabel = "agents"\ngather = ["agent"]\n'
                         'layout = "agents-logs"\n')
    assert heelr("apply", "solo", "--tab", t1) == 0
    (c,) = multi.ws_created("w1")
    new = c["pane"]
    moved = herdr.move(new, tab=tb, target=q, split="right")["pane"]["pane_id"]
    assert moved != new
    facts.rekey(new, moved)
    ws_tab = next(t for t in fake.tabs if fake.tabs[t]["ws"] == "w1")
    heelr("back", "--tab", ws_tab)
    assert moved in fake.all_panes() and fake.tab_of(moved) == tb


def test_back_never_pulls_an_anchor_that_left_the_workspace(fake):                 # P1
    tb, (q,) = fake.add_tab("w9", "x", 1)
    tab, (a, b, c) = fake.add_tab("w1", "main", 3, d="down")
    assert heelr("apply", "main-left", "--tab", tab) == 0
    from heelr.tree import first_leaf, from_dict
    anchor = first_leaf(from_dict(json.loads(core._back_path(tab).read_text())["tree"])).pane
    moved = herdr.move(anchor, tab=tb, target=q, split="right")["pane"]["pane_id"]
    facts.rekey(anchor, moved)
    heelr("back", "--tab", tab)
    assert fake.tab_of(moved) == tb


# ------------------------------------------------------------------ P3

from heelr import key  # noqa: E402


@pytest.mark.parametrize("pat", [r"(a+)+$", r"(a|a)*$", r"(\d+)*x", r"(a*)*", r"(?:aa|a)+c", r"(a)\1", r"(?>a+)+b", r"a*a*a*b", r"a*a*a*a*a*a*b",
                                 "(a?|a?)" * 22 + "c", "a?" * 30 + "a" * 30 + "c", "(a?)" * 28 + "a" * 28 + "c",
                                 "(a|a?)" * 22 + "c", "(a?|a?){1}" * 22 + "c", "(a|a)" * 22 + "c", "a{99999999999}",
                                 "(" * 500 + "a" + ")" * 500, r"[A-Z]+-\d+|GH-\d+", r"(?=a)b", r"(?(1)a|b)", r"abc(-\d+)?"])
def test_dangerous_ticket_patterns_are_ignored_and_fast(pat):                      # P3
    import time
    key.take_failures()
    t = time.monotonic()
    assert key.extract_ticket("a" * 120, pat) == "a" * 120
    assert time.monotonic() - t < 0.5
    assert any("ticket" in f for f in key.take_failures())


@pytest.mark.parametrize("pat", [r"GH-\d+", r"([A-Z]+-\d+)", r"[A-Z]{2,5}-\d+", r"(?:fix|feat)/(\w+)"])
def test_ordinary_ticket_patterns_still_work(pat):                                 # P3
    key.take_failures()
    key.extract_ticket("fix/GH-330-crash", pat)
    assert key.take_failures() == []
    assert key.extract_ticket("fix/GH-330-crash", r"GH-\d+") == "GH-330"


def test_ticket_search_text_is_capped():                                           # P3
    assert key.extract_ticket("x" * 500 + "GH-9", r"GH-\d+") == "x" * 120


def test_pane_key_with_a_hostile_repo_ticket_returns_quickly(tmp_path, monkeypatch):    # P3
    import time
    from test_placement_facts import make_repo
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    r = make_repo(tmp_path / "heelr", branch="a" * 40 + "!")
    (r / ".heelr.toml").write_text("ticket = '(a+)+$'\n")
    t = time.monotonic()
    k = key.pane_key({"pane_id": "p", "cwd": str(r)}, key.PlacementConfig(), [])
    assert time.monotonic() - t < 0.5 and k.task == ("a" * 40 + "!")[:120]


# ------------------------------------------------------------------ m4 m5 m7

@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    return tmp_path


def test_rekey_rewrites_burst_events_under_the_burst_lock(state, monkeypatch):     # m4
    import contextlib
    from heelr import burst
    burst.note("w1:p7", 100.0)
    entered = []
    real = burst._locked

    @contextlib.contextmanager
    def spy():
        entered.append(1)
        with real():
            yield
    monkeypatch.setattr(burst, "_locked", spy)
    facts.rekey("w1:p7", "w2:p1")
    assert entered and json.loads((core.state_dir() / "burst.json").read_text())[0][0] == "w2:p1"


def _hold(path):
    import fcntl
    f = open(path, "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def test_pane_store_writes_serialise_on_one_lock(state, monkeypatch, capsys):     # m5
    import io, sys
    monkeypatch.setattr(facts, "STORE_WAIT", 0.1)
    f = _hold(core.state_dir() / "panes.lock")
    with pytest.raises(herdr.HerdrError):                      # tag errors
        facts.set_tag("w1:p1", "GH-1")
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"cwd": "/a"}'))
    assert cli.main(["report", "--pane", "w1:p1"]) == 0         # report stays silent
    assert capsys.readouterr().err == "" and not facts._pane_path("w1:p1").exists()
    with pytest.raises(herdr.HerdrError):
        facts.rekey("w1:p1", "w2:p1")
    with pytest.raises(herdr.HerdrError):
        facts.forget("w1:p1")
    f.close()
    facts.set_tag("w1:p1", "GH-1")
    assert facts.load_pane("w1:p1")["tag"] == "GH-1"


def test_forget_drops_and_rekey_moves_the_classify_cache_entry(state):             # m7
    core.write_atomic(core.state_dir() / "classify-cache.json",
                      json.dumps({"w1:p1": {"hash": "h", "out": {}}, "w1:p2": {"hash": "h", "out": {}}}))
    facts.forget("w1:p1")
    assert list(json.loads((core.state_dir() / "classify-cache.json").read_text())) == ["w1:p2"]
    facts.rekey("w1:p2", "w2:p9")
    assert list(json.loads((core.state_dir() / "classify-cache.json").read_text())) == ["w2:p9"]


def test_classify_cache_write_is_skipped_when_the_store_lock_is_busy(state, monkeypatch):    # m7
    monkeypatch.setattr(facts, "STORE_WAIT", 0.1)
    monkeypatch.setattr(key, "_run_script", lambda s, d, timeout=1.0: ({"task": "T"}, True))
    f = _hold(core.state_dir() / "panes.lock")
    assert key._classify_cached("c.sh", "p", {"pane_id": "p"}) == {"task": "T"}
    f.close()
    assert not (core.state_dir() / "classify-cache.json").exists()


# ------------------------------------------------------------------ P4 P5

def test_nonzero_exit_is_transient_and_not_cached(state, monkeypatch, tmp_path):   # P4
    s = tmp_path / "c.sh"
    s.write_text("#!/bin/sh\nexit 3\n")
    s.chmod(0o755)
    from test_placement_key import fake_popen
    key.take_failures()
    fake_popen(monkeypatch, tmp_path, '{"task": "T"}', 3)
    assert key._run_script(str(s), {"pane_id": "p"}) == ({}, False)
    assert "exited 3" in key.take_failures()[0]
    fake_popen(monkeypatch, tmp_path, "[1]", 0)
    assert key._run_script(str(s), {"pane_id": "p"}) == ({}, True)      # unusable output from exit 0 stays cached


def test_editing_the_classify_script_invalidates_its_cache(state, tmp_path, monkeypatch):    # P5
    s = tmp_path / "c.sh"
    s.write_text("#!/bin/sh\ncat >/dev/null; echo '{\"task\": \"one\"}'\n")
    s.chmod(0o755)
    monkeypatch.setattr(key, "_run_script", lambda sc, d, timeout=1.0: (
        {"task": Path(sc).read_text().split("task")[1][4:7]}, True))
    stdin = {"pane_id": "p"}
    assert key._classify_cached(str(s), "p", stdin) == {"task": "one"}
    s.write_text("#!/bin/sh\ncat >/dev/null; echo '{\"task\": \"two2\"}'\n")
    assert key._classify_cached(str(s), "p", stdin) == {"task": "two"}


# ------------------------------------------------------------------ minors

def test_own_pane_ttl_covers_the_hook_worst_case():                                # m3
    from heelr import place
    assert place.OWN_TTL == 30.0
    assert 5.0 + cli.AUTO_WAIT + 10.0 < place.OWN_TTL        # burst_window max + lock wait + snapshots and classify


def test_a_mark_refresh_gives_classify_only_the_remaining_budget(state, monkeypatch, tmp_path):   # m8
    from test_placement_cli import on  # noqa: F401
    seen = []
    monkeypatch.setattr(key, "_run_script", lambda s, d, timeout=1.0: (seen.append(timeout) or {}, False))
    key.set_deadline(time.monotonic() + 0.3)
    key._classify_cached("c.sh", "p", {"pane_id": "p"})
    assert seen and seen[0] <= 0.3
    key.set_deadline(time.monotonic() - 1)                  # out of budget: the script is not run at all
    seen.clear()
    assert key._classify_cached("c.sh", "p", {"pane_id": "p"}) == {} and seen == []
    key.set_deadline(None)
    key._classify_cached("c.sh", "p", {"pane_id": "p"})
    assert seen == [1.0]


@pytest.mark.parametrize("field", ["workspace", "tab"])
@pytest.mark.parametrize("val", ['""', '"  "'])
def test_empty_template_is_the_default(tmp_path, field, val):                       # m9
    f = tmp_path / "placement.toml"
    f.write_text(f"[placement]\nenabled = true\n{field} = {val}\n")
    c = key.load_config(f)
    assert c.enabled and getattr(c, field) == {"workspace": "{repo}", "tab": "{task}"}[field]


def test_layout_lock_default_socket_honours_xdg_config_home(tmp_path, monkeypatch):    # m11
    monkeypatch.delenv("HERDR_SOCKET_PATH", raising=False)
    (tmp_path / "xdg/herdr").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert core.layout_lock_path() == tmp_path / "xdg/herdr/herdr.sock.layout.lock"


def test_git_file_reads_are_capped(tmp_path):                                      # m12
    p = tmp_path / "HEAD"
    p.write_text("ref: refs/heads/main\n" + "x" * 1_000_000)
    assert len(facts._read(p)) <= 65536 and facts._read(p).startswith("ref: refs/heads/main")


def test_a_stale_report_folder_no_longer_beats_the_live_one(state):                # m13
    from test_placement_facts import make_repo
    a, b = make_repo(state / "aaa"), make_repo(state / "bbb")
    facts.save_report("w1:p1", {"cwd": str(a)})
    rec = facts.load_pane("w1:p1")
    cfg = key.PlacementConfig()
    pane = {"pane_id": "w1:p1", "cwd": str(b), "foreground_cwd": str(b)}
    assert key.pane_key(pane, cfg, []).repo == "aaa"                               # fresh: the report wins
    rec["report_at"] = time.time() - 700
    facts._save_pane("w1:p1", rec)
    assert key.pane_key(pane, cfg, []).repo == "bbb"                               # stale: live folder
    rec["report"]["task"] = "GH-5"
    facts._save_pane("w1:p1", rec)
    assert key.pane_key(pane, cfg, []).task == "GH-5"                              # but its task still counts


def test_classify_stdin_carries_the_role_hint(state, monkeypatch):                 # m14
    seen = []
    monkeypatch.setattr(key, "_classify_cached", lambda s, p, stdin: (seen.append(stdin), {})[1])
    roles = [{"name": "agent", "agent": True}, {"name": "shell", "fallback": True}]
    pane = {"pane_id": "w1:p1", "cwd": "/", "foreground_cwd": "/", "agent": "claude"}
    key.pane_key(pane, key.PlacementConfig(classify="c.sh"), roles)
    assert seen[0]["role_hint"] == "agent"
    key.pane_key({**pane, "agent": None}, key.PlacementConfig(classify="c.sh"), roles)
    assert seen[1]["role_hint"] == "shell"


def test_claude_hooks_example_is_a_hooks_block():                                  # m1
    d = json.loads((Path(__file__).parent.parent / "examples/adapters/claude-hooks.json").read_text())
    assert list(d) == ["hooks"] and "CwdChanged" in d["hooks"]


def test_version_flag_exits_zero(capsys):                                          # m15
    import heelr
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0 and capsys.readouterr().out.strip() == f"heelr {heelr.__version__}"


@pytest.mark.parametrize("pat", [r"[A-Z]+-\d+", r"GH-\d+", r"(?:fix|feat)/(\w+)"])
def test_two_repeat_patterns_stay_accepted(pat):                                   # P3 follow-up
    key.take_failures()
    key.extract_ticket("fix/GH-330-crash", pat)
    assert key.take_failures() == []


def test_two_adjacent_quantifiers_worst_case_is_fast():                            # P3 follow-up
    import time
    t = time.monotonic()
    assert key.extract_ticket("a" * 120, r"a*a*b") == "a" * 120
    assert time.monotonic() - t < 0.5


def test_load_config_survives_deeply_nested_toml(tmp_path):                         # minor
    f = tmp_path / "placement.toml"
    f.write_text("a = " + "[" * 5000)
    assert key.load_config(f) == key.PlacementConfig()


def test_every_placement_command_survives_a_hostile_config(tmp_path, monkeypatch, capsys):   # minor
    import io, sys
    from test_placement_place import fake as _f  # noqa: F401
    cfgdir = tmp_path / "cfg/heelr"
    cfgdir.mkdir(parents=True)
    (cfgdir / "placement.toml").write_text("a = " + "[" * 5000)
    (cfgdir / "config.toml").write_text("a = " + "[" * 5000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from fakeherdr import FakeHerdr
    f = FakeHerdr()
    f.version = "0.9.3"
    monkeypatch.setattr(herdr, "call", f.call)
    monkeypatch.setattr(herdr, "run_in", f.run_in)
    w = f.add_workspace("x")
    t, (p, q) = f.add_tab(w, "main", 2)
    monkeypatch.setenv("HERDR_PLUGIN_EVENT", "pane.created")
    monkeypatch.setenv("HERDR_PLUGIN_EVENT_JSON", json.dumps({"data": {"pane": {"pane_id": q}}}))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert cli.main(["--allow-untested", "auto"]) == 0
    for argv in (["sort"], ["pull", "GH-1"], ["drift-mark"], ["report", "--pane", p]):
        assert cli.main(["--allow-untested", *argv]) == 0, argv


def test_hostile_repo_toml_is_ignored(tmp_path):                                    # minor
    (tmp_path / ".heelr.toml").write_text("a = " + "[" * 5000)
    assert key.repo_config(str(tmp_path)) == {}
