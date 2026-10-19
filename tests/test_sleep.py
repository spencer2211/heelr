"""Tab sleep and wake (heelr/sleep.py), offline: a fake Herdr and a fake process table."""
import json
import signal

import pytest

from heelr import cli, herdr, sleep
from heelr.sleep import Proc

TAB = "w1:t1"


class World:
    """Panes in one tab, each with a shell; an agent pane's foreground group is a claude
    leader (+ an MCP child in its group). Processes live in `procs`."""

    def __init__(self, tmp_path):
        self.procs: dict[int, Proc] = {}
        self.panes: dict[str, dict] = {}       # pane id -> snapshot entry
        self.fg: dict[str, int] = {}           # pane id -> foreground pgid
        self.shell: dict[str, int] = {}
        self.typed: list[tuple[str, str]] = []
        self.tabs: list[dict] = [{"tab_id": TAB, "label": "work"}]
        self.renames: list[tuple[str, str]] = []
        self.rename_fails = False
        self.stubborn: set[int] = set()        # pgids that ignore SIGTERM
        self.killed: list[int] = []
        self.next = 1000
        self.projects = tmp_path / "claude" / "projects"
        self.projects.mkdir(parents=True)

    def pid(self):
        self.next += 1
        return self.next

    def add_shell(self, pane, tab=TAB):
        sh = self.pid()
        self.procs[sh] = Proc(sh, 1, sh, 4 << 20, ["/bin/bash"], 0)
        self.shell[pane], self.fg[pane] = sh, sh
        self.panes[pane] = {"pane_id": pane, "tab_id": tab, "cwd": f"/work/{pane}", "agent_status": "unknown"}
        return sh

    def add_agent(self, pane, sid, name="worker", status="idle", tab=TAB, argv=None):
        sh = self.add_shell(pane, tab)
        lead = self.pid()
        self.procs[lead] = Proc(lead, sh, lead, 250 << 20, argv or ["claude", "--resume", sid], 0)
        mcp = self.pid()
        self.procs[mcp] = Proc(mcp, lead, lead, 50 << 20, ["uvx", "mcp-server-fetch"], 0)
        self.fg[pane] = lead
        self.panes[pane].update(agent="claude", agent_status=status, terminal_title_stripped=name or "",
                                agent_session={"agent": "claude", "kind": "id", "value": sid})
        self.transcript(sid, name)
        return lead

    def transcript(self, sid, name):
        d = self.projects / "-work"
        d.mkdir(exist_ok=True)
        lines = [{"type": "user", "message": "hi"}]
        if name:
            lines += [{"type": "custom-title", "customTitle": "old"}, {"type": "custom-title", "customTitle": name}]
        (d / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))

    def add_task(self, lead, age=1260):
        t = self.pid()
        self.procs[t] = Proc(t, lead, t, 6 << 20, ["/bin/bash", "-c", "sleep 9"], 100000 - age)
        return t

    # Herdr
    def call(self, *args, timeout=None):
        if args[:2] == ("api", "snapshot"):
            return {"snapshot": {"panes": [dict(p) for p in self.panes.values()], "focused_tab_id": TAB,
                                 "tabs": [dict(t) for t in self.tabs]}}
        if args[:2] == ("pane", "process-info"):
            pane = args[-1]
            if pane not in self.panes:
                raise herdr.HerdrError(f"no pane {pane}")
            fg = self.fg[pane]
            members = [p for p in self.procs.values() if p.pgid == fg]
            return {"process_info": {"shell_pid": self.shell[pane], "foreground_process_group_id": fg,
                                     "foreground_processes": [
                                         {"pid": p.pid, "argv": p.argv, "name": p.argv[0].rsplit("/", 1)[-1],
                                          "cmdline": " ".join(p.argv)} for p in members]}}
        if args[:2] == ("tab", "rename"):
            if self.rename_fails:
                raise herdr.HerdrError("rename failed")
            tab = next(t for t in self.tabs if t["tab_id"] == args[2])
            tab["label"] = " ".join(args[3:])
            self.renames.append((args[2], tab["label"]))
            return {}
        if args[:2] == ("notification", "show"):
            return {}
        raise AssertionError(f"unexpected herdr call {args}")

    def killpg(self, pgid, sig):
        assert sig == signal.SIGTERM
        self.killed.append(pgid)
        if pgid in self.stubborn:
            return
        for pid in [p.pid for p in self.procs.values() if p.pgid == pgid]:
            del self.procs[pid]
        for pane, fg in self.fg.items():
            if fg == pgid:
                self.fg[pane] = self.shell[pane]   # Herdr's flags stay stale, as in spike 2

    def type(self, pane, command):
        self.typed.append((pane, command))


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = World(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("HERDR_BIN_PATH", "/nonexistent/herdr-must-not-run")
    monkeypatch.setattr(herdr, "call", world.call)
    return world


def do_sleep(w, **kw):
    clock = iter(range(0, 1000)).__next__
    opts = dict(read_procs=lambda: dict(w.procs), killpg=w.killpg, clock=clock, pause=lambda s: None,
                now=lambda: 100000.0)
    return sleep.do_sleep(TAB, **{**opts, **kw})


def do_wake(w):
    return sleep.do_wake(TAB, read_procs=lambda: dict(w.procs), typer=w.type)


# ------------------------------------------------------------------ classification

def test_classify_agent_stale_busy_shell(w):
    lead = w.add_agent("w1:p1", "s1")
    w.add_shell("w1:p2")
    w.add_shell("w1:p3")
    dev = w.pid()
    w.procs[dev] = Proc(dev, w.shell["w1:p3"], dev, 1, ["npm", "run", "dev"], 0)
    w.fg["w1:p3"] = dev
    w.add_shell("w1:p4")
    w.panes["w1:p4"].update(agent="claude", agent_status="idle")   # stale flags, no claude
    kinds = {p.id: (p.kind, p.leader) for p in sleep.read_tab_panes(w.call("api", "snapshot")["snapshot"], TAB)}
    assert kinds == {"w1:p1": ("agent", lead), "w1:p2": ("shell", None), "w1:p3": ("busy", None),
                     "w1:p4": ("stale", None)}


def test_a_slept_agent_with_stale_flags_is_stale_not_live(w):
    w.add_agent("w1:p1", "s1", name="a")
    assert do_sleep(w)[0]
    p, = sleep.read_tab_panes(w.call("api", "snapshot")["snapshot"], TAB)
    assert w.panes["w1:p1"]["agent"] == "claude"     # Herdr still says agent
    assert p.kind == "stale"


def test_an_undetected_claude_is_an_agent_and_is_refused_for_no_session(w):
    w.add_shell("w1:p1")
    lead = w.pid()
    w.procs[lead] = Proc(lead, w.shell["w1:p1"], lead, 1, ["/home/u/.local/bin/claude"], 0)
    w.fg["w1:p1"] = lead
    ok, msg = do_sleep(w)
    assert not ok and "no session id" in msg and w.killed == []


# ------------------------------------------------------------------ names

def test_session_name_is_the_last_custom_title(w):
    w.transcript("s1", "final")
    w.transcript("s2", None)
    assert sleep.session_name("s1") == "final"
    assert sleep.session_name("s2") is None
    assert sleep.session_name("nope") is sleep.NO_TRANSCRIPT


# ------------------------------------------------------------------ refusals

def test_refusal_names_every_blocking_pane_and_kills_nothing(w):
    w.add_agent("w1:p1", "s1", name="ok")
    w.add_agent("w1:p2", "s2", name=None)
    w.add_agent("w1:p3", "s3", name="busy", status="working")
    lead = w.add_agent("w1:p4", "s4", name="tasky")
    w.add_task(lead)
    w.add_agent("w1:p5", "s5", name="gone")
    (w.projects / "-work" / "s5.jsonl").unlink()
    ok, msg = do_sleep(w)
    assert not ok and w.killed == []
    assert "p2 is not a named session" in msg
    assert "'busy' is working" in msg
    assert "'tasky' has a running task: bash (running 21m)" in msg
    assert "p5 'gone' is unnamed (no transcript found)" in msg
    assert "p1" not in msg
    assert not sleep.record_path(TAB).exists()


def test_mcp_children_in_the_group_are_not_tasks(w):
    w.add_agent("w1:p1", "s1", name="a")
    ok, msg = do_sleep(w)
    assert ok, msg


# ------------------------------------------------------------------ sleep

def test_sleep_kills_each_group_and_records(w):
    a = w.add_agent("w1:p1", "s1", name="one")
    b = w.add_agent("w1:p2", "s2", name="two")
    w.add_shell("w1:p3")
    ok, msg = do_sleep(w)
    assert ok, msg
    assert sorted(w.killed) == sorted([a, b])
    assert not [p for p in w.procs.values() if p.pgid in (a, b)]       # MCP children gone too
    assert "slept 2 agents, freed 600 MiB" in msg
    rec = sleep.load_record(TAB)
    assert [(e["pane"], e["session"], e["name"], e["status"], e["cwd"]) for e in rec["panes"]] == [
        ("w1:p1", "s1", "one", "slept", "/work/w1:p1"), ("w1:p2", "s2", "two", "slept", "/work/w1:p2")]


def test_shells_running_something_are_left_running_and_named(w):
    w.add_agent("w1:p1", "s1", name="a")
    w.add_shell("w1:p2")
    dev = w.pid()
    w.procs[dev] = Proc(dev, w.shell["w1:p2"], dev, 1, ["npm", "run", "dev"], 0)
    w.fg["w1:p2"] = dev
    ok, msg = do_sleep(w)
    assert ok and dev in w.procs and "left running: p2 (npm run dev)" in msg


def test_a_group_that_outlives_the_deadline_is_failed_and_not_woken(w):
    w.add_agent("w1:p1", "s1", name="a")
    b = w.add_agent("w1:p2", "s2", name="b")
    w.stubborn.add(b)
    ok, msg = do_sleep(w)
    assert not ok and "p2 still running after 10s" in msg
    st = {e["pane"]: e["status"] for e in sleep.load_record(TAB)["panes"]}
    assert st == {"w1:p1": "slept", "w1:p2": "failed"}
    assert do_wake(w)[0]
    assert [p for p, _ in w.typed] == ["w1:p1"]


def test_failed_panes_are_rechecked_and_overwritten_on_the_next_sleep(w):
    b = w.add_agent("w1:p2", "s2", name="b")
    w.stubborn.add(b)
    do_sleep(w)
    w.stubborn.clear()
    ok, msg = do_sleep(w)
    assert ok, msg
    assert [(e["pane"], e["status"]) for e in sleep.load_record(TAB)["panes"]] == [("w1:p2", "slept")]


def test_merge_drops_the_old_failed_entry(w, monkeypatch):
    b = w.add_agent("w1:p2", "s2", name="b")
    w.stubborn.add(b)
    do_sleep(w)
    rec = sleep.load_record(TAB)
    rec["panes"].append({"pane": "w1:p7", "session": "s7", "status": "slept"})  # keep the record
    sleep.save_record(rec)
    w.stubborn.clear()
    assert do_sleep(w)[0]
    assert [(e["pane"], e["status"]) for e in sleep.load_record(TAB)["panes"]] == [
        ("w1:p7", "slept"), ("w1:p2", "slept")]


def test_sleep_on_a_half_slept_tab_merges(w):
    w.add_agent("w1:p1", "s1", name="a")
    do_sleep(w)
    w.add_agent("w1:p2", "s2", name="b")
    ok, msg = do_sleep(w)
    assert ok and "p1: already asleep or stale" in msg
    assert [e["pane"] for e in sleep.slept(sleep.load_record(TAB))] == ["w1:p1", "w1:p2"]


def test_an_agent_that_changes_before_the_kill_is_left_awake(w, monkeypatch):
    a = w.add_agent("w1:p1", "s1", name="a")
    real = w.call
    calls = {"n": 0}

    def flip(*args, timeout=None):
        if args[:2] == ("api", "snapshot"):
            calls["n"] += 1
            if calls["n"] == 2:
                w.panes["w1:p1"]["agent_status"] = "working"
        return real(*args)
    monkeypatch.setattr(herdr, "call", flip)
    ok, msg = do_sleep(w)
    assert not ok and "p1 changed; left awake" in msg and w.killed == [] and a in w.procs


def test_no_agents_is_a_no_op(w):
    w.add_shell("w1:p1")
    ok, msg = do_sleep(w)
    assert ok and msg.startswith("no agents to sleep")


# ------------------------------------------------------------------ wake

def test_wake_types_cd_and_resume_and_deletes_the_record(w):
    w.add_agent("w1:p1", "s1", name="one")
    w.panes["w1:p1"]["cwd"] = "/work/it's here"
    do_sleep(w)
    ok, msg = do_wake(w)
    assert ok and msg == "woke 1 agent"
    assert w.typed == [("w1:p1", "cd -- '/work/it'\"'\"'s here' && claude --resume s1")]
    assert not sleep.record_path(TAB).exists()


def test_a_second_wake_types_nothing(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    do_wake(w)
    # the typed resume is running again in the pane
    w.add_agent("w1:p1", "s1", name="one")
    ok, msg = do_wake(w)
    assert ok and msg == "nothing asleep in this tab" and len(w.typed) == 1


def test_wake_drops_a_session_already_running_elsewhere_without_typing(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    w.add_agent("w1:p9", "s1", name="one", tab="w1:t9")   # resumed by hand in another tab
    ok, msg = do_wake(w)
    assert ok and "p1 'one' is already running" in msg and w.typed == []
    assert not sleep.record_path(TAB).exists()


@pytest.mark.parametrize("argv", [["claude", "--resume", "one"], ["claude", "-r", "s1"],
                                  ["claude", "--resume=one"]])
def test_wake_sees_a_claude_resumed_by_name_or_id_outside_herdr(w, argv):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    x = w.pid()
    w.procs[x] = Proc(x, 1, x, 1, argv, 0)
    ok, msg = do_wake(w)
    assert ("already running" in msg or "a session named 'one' is running" in msg) and w.typed == []
    assert "s1" in msg or "already running" in msg


@pytest.mark.parametrize("argv", [["claude", "--resume", "on"], ["claude", "--model", "one"],
                                  ["vim", "--resume", "one"], ["claude", "--resume", "s1x"]])
def test_running_match_is_the_exact_resume_token(w, argv):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    x = w.pid()
    w.procs[x] = Proc(x, 1, x, 1, argv, 0)
    assert do_wake(w)[1] == "woke 1 agent"


def test_stale_flags_elsewhere_dont_count_as_running(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    w.add_shell("w1:p9", tab="w1:t9")
    w.panes["w1:p9"].update(agent="claude", agent_session={"value": "s1"})
    assert do_wake(w)[1] == "woke 1 agent"


def test_a_gone_pane_is_dropped_so_the_next_toggle_sleeps(w, monkeypatch):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    del w.panes["w1:p1"]
    ok, msg = do_wake(w)
    assert "p1 is gone; resume 'one' with `claude --resume s1`" in msg and w.typed == []
    assert not sleep.record_path(TAB).exists()
    assert not sleep.asleep(TAB)


def test_a_busy_pane_stays_slept(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    x = w.pid()
    w.procs[x] = Proc(x, w.shell["w1:p1"], x, 1, ["htop"], 0)
    w.fg["w1:p1"] = x
    ok, msg = do_wake(w)
    assert not ok and "p1 busy; retry wake later" in msg and w.typed == []
    assert sleep.asleep(TAB)


# ------------------------------------------------------------------ CLI

def test_toggle_sleeps_then_wakes(w, monkeypatch):
    w.add_agent("w1:p1", "s1", name="one")
    monkeypatch.setattr(sleep, "read_procs", lambda: dict(w.procs))
    monkeypatch.setattr(sleep, "uptime", lambda: 100000.0)
    monkeypatch.setattr(sleep.os, "killpg", w.killpg)
    monkeypatch.setattr(sleep, "_type", w.type)
    assert cli.main(["sleep-toggle", "--tab", TAB]) == 0
    assert sleep.asleep(TAB)
    assert cli.main(["sleep-toggle", "--tab", TAB]) == 0
    assert not sleep.asleep(TAB) and len(w.typed) == 1


def label(w):
    return next(t["label"] for t in w.tabs if t["tab_id"] == TAB)


def test_sleep_marks_the_tab_and_wake_restores_its_label(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    assert label(w) == "💤 work"
    assert sleep.load_record(TAB)["label"] == "work"
    do_wake(w)
    assert label(w) == "work" and w.renames == [(TAB, "💤 work"), (TAB, "work")]


def test_a_second_sleep_keeps_the_original_label(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    w.add_agent("w1:p2", "s2", name="two")      # resumed by hand meanwhile, say
    do_sleep(w)
    assert label(w) == "💤 work" and len(w.renames) == 1
    assert sleep.load_record(TAB)["label"] == "work"


def test_wake_keeps_a_label_renamed_while_asleep(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    w.tabs[0]["label"] = "mine now"
    do_wake(w)
    assert label(w) == "mine now"


def test_a_tab_still_partly_asleep_keeps_its_mark(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    x = w.pid()
    w.procs[x] = Proc(x, w.shell["w1:p1"], x, 1, ["htop"], 0)
    w.fg["w1:p1"] = x
    ok, msg = do_wake(w)
    assert not ok and "retry wake later" in msg
    assert label(w) == "💤 work"


def test_no_mark_when_nothing_slept(w):
    lead = w.add_agent("w1:p1", "s1", name="one")
    w.stubborn.add(lead)
    ok, msg = do_sleep(w)
    assert not ok and label(w) == "work" and not w.renames


def test_a_failed_rename_does_not_fail_sleep_or_wake(w):
    w.add_agent("w1:p1", "s1", name="one")
    w.rename_fails = True
    ok, msg = do_sleep(w)
    assert ok and "slept 1 agent" in msg
    ok, msg = do_wake(w)
    assert ok and msg == "woke 1 agent"


def test_resume_arg():
    assert sleep.resume_arg(["claude", "--resume", "x"]) == "x"
    assert sleep.resume_arg(["claude", "-r", "x"]) == "x"
    assert sleep.resume_arg(["claude", "--resume=x"]) == "x"
    assert sleep.resume_arg(["claude", "--resume"]) is None
    assert sleep.resume_arg(["claude"]) is None


def test_read_procs_sees_this_process():
    import os
    me = sleep.read_procs()[os.getpid()]
    assert me.pgid == os.getpgid(0) and me.rss > 0 and me.argv


# ------------------------------------------------------------------ review round 1

def test_resumed_by_hand_in_the_same_pane_then_slept_again_wakes_once(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    w.add_agent("w1:p1", "s1", name="one")          # the user resumes s1 by hand in p1
    assert do_sleep(w)[0]
    assert len(sleep.load_record(TAB)["panes"]) == 1
    do_wake(w)
    assert w.typed == [("w1:p1", "cd -- /work/w1:p1 && claude --resume s1")]


def test_resumed_by_hand_in_another_pane_then_slept_again_wakes_one_copy(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    w.add_agent("w1:p2", "s1", name="one")          # the user resumes s1 by hand in p2
    assert do_sleep(w)[0]
    assert [e["pane"] for e in sleep.load_record(TAB)["panes"]] == ["w1:p2"]
    do_wake(w)
    assert [p for p, _ in w.typed] == ["w1:p2"]


def test_wake_types_each_session_once_even_with_duplicate_entries(w):
    sleep.save_record({"tab": TAB, "panes": [
        {"pane": "w1:p1", "session": "s1", "name": "one", "cwd": "/a", "status": "slept"},
        {"pane": "w1:p2", "session": "s1", "name": "one", "cwd": "/a", "status": "slept"},
        {"pane": "w1:p1", "session": "s3", "name": "three", "cwd": "/a", "status": "slept"}]})
    w.add_shell("w1:p1")
    w.add_shell("w1:p2")
    ok, msg = do_wake(w)
    assert [p for p, _ in w.typed] == ["w1:p1"]
    assert "p2 'one' is already running" in msg
    assert "already resuming another session there" in msg and "claude --resume s3" in msg
    assert not sleep.record_path(TAB).exists()


def test_a_killpg_error_marks_the_pane_failed_and_the_rest_still_sleep(w):
    a = w.add_agent("w1:p1", "s1", name="a")
    b = w.add_agent("w1:p2", "s2", name="b")
    real = w.killpg

    def killpg(pgid, sig):
        if pgid == a:
            raise PermissionError(1, "Operation not permitted")
        real(pgid, sig)
    ok, msg = do_sleep(w, killpg=killpg)
    assert not ok and "p1: can't signal it (Operation not permitted)" in msg
    st = {e["pane"]: e["status"] for e in sleep.load_record(TAB)["panes"]}
    assert st == {"w1:p1": "failed", "w1:p2": "slept"} and b not in {p.pgid for p in w.procs.values()}


def test_an_exception_mid_wait_still_resolves_every_pending_entry(w):
    a = w.add_agent("w1:p1", "s1", name="a")
    calls = {"n": 0}

    def procs():
        calls["n"] += 1
        if calls["n"] == 3:                          # the first wait poll
            raise RuntimeError("boom")
        return dict(w.procs)
    with pytest.raises(RuntimeError):
        sleep.do_sleep(TAB, read_procs=procs, killpg=w.killpg, clock=iter(range(99)).__next__,
                       pause=lambda s: None, now=lambda: 100000.0)
    assert [e["status"] for e in sleep.load_record(TAB)["panes"]] == ["slept"]
    assert a not in w.procs


def test_pending_left_by_a_crash_is_resolved_on_the_next_run(w):
    sleep.save_record({"tab": TAB, "panes": [
        {"pane": "w1:p1", "session": "s1", "name": "one", "cwd": "/a", "status": "pending", "pgid": 4242}]})
    assert sleep.asleep(TAB)                        # the toggle goes to wake, not sleep
    w.add_shell("w1:p1")
    ok, msg = do_wake(w)
    assert msg == "woke 1 agent"                     # group 4242 is gone: it had slept


def test_the_leader_sharing_the_shells_group_is_never_signalled(w, monkeypatch):
    w.add_agent("w1:p1", "s1", name="a")
    sh = w.shell["w1:p1"]
    real = w.call

    def call(*args, timeout=None):
        out = real(*args)
        if args[:2] == ("pane", "process-info"):    # report the shell's group as the foreground
            out["process_info"]["shell_pid"] = sh + 1000
        return out
    monkeypatch.setattr(herdr, "call", call)
    w.procs[sh + 1000] = Proc(sh + 1000, 1, w.fg["w1:p1"], 1, ["/bin/bash"], 0)
    ok, msg = do_sleep(w)
    assert not ok and "not a claude process group" in msg and w.killed == []


def test_wake_does_not_type_into_a_pane_where_another_claude_is_live(w):
    w.add_agent("w1:p1", "s1", name="one")
    do_sleep(w)
    w.add_agent("w1:p1", "s2", name="other")        # a different session now runs in p1
    ok, msg = do_wake(w)
    assert not ok and "p1 busy; retry wake later" in msg and w.typed == []


def test_a_task_that_appears_before_the_kill_leaves_the_agent_awake(w):
    a = w.add_agent("w1:p1", "s1", name="a")
    n = {"i": 0}

    def procs():
        n["i"] += 1
        if n["i"] == 2:                              # the re-read right before the kill
            w.add_task(a)
        return dict(w.procs)
    ok, msg = sleep.do_sleep(TAB, read_procs=procs, killpg=w.killpg, clock=iter(range(99)).__next__,
                             pause=lambda s: None, now=lambda: 100000.0)
    assert not ok and "p1 changed; left awake" in msg and w.killed == []


def test_cwd_is_the_leaders_own(w, monkeypatch):
    w.add_agent("w1:p1", "s1", name="a")
    real = w.call

    def call(*args, timeout=None):
        out = real(*args)
        if args[:2] == ("pane", "process-info"):
            for p in out["process_info"]["foreground_processes"]:
                p["cwd"] = "/real/agent/dir"
        return out
    monkeypatch.setattr(herdr, "call", call)
    do_sleep(w)
    assert sleep.load_record(TAB)["panes"][0]["cwd"] == "/real/agent/dir"


def test_parse_stat_skips_zombies_and_names_with_parens():
    line = "123 (a) b) S 7 99 99 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 4242 0 0"
    assert sleep.parse_stat(line) == (7, 99, 4242)
    assert sleep.parse_stat(line.replace(") S ", ") Z ")) is None


def test_a_new_sleep_drops_failed_entries_of_other_panes(w):
    sleep.save_record({"tab": TAB, "panes": [
        {"pane": "w1:p9", "session": "s9", "status": "failed"},
        {"pane": "w1:p8", "session": "s8", "status": "slept"}]})
    w.add_agent("w1:p1", "s1", name="a")
    assert do_sleep(w)[0]
    assert [e["pane"] for e in sleep.load_record(TAB)["panes"]] == ["w1:p8", "w1:p1"]


# ------------------------------------------------------------------ review round 2

def test_a_newer_session_in_the_same_pane_replaces_the_old_entry(w):
    w.add_agent("w1:p1", "s1", name="old")
    do_sleep(w)
    w.add_agent("w1:p1", "s2", name="new")          # a different session started in p1
    assert do_sleep(w)[0]
    assert [(e["pane"], e["session"]) for e in sleep.load_record(TAB)["panes"]] == [("w1:p1", "s2")]
    do_wake(w)
    assert w.typed == [("w1:p1", "cd -- /work/w1:p1 && claude --resume s2")]


def test_sleep_keeps_a_crash_pending_entry_whose_group_is_gone(w):
    sleep.save_record({"tab": TAB, "panes": [
        {"pane": "w1:p8", "session": "s8", "name": "eight", "cwd": "/a", "status": "pending", "pgid": 4242}]})
    w.add_agent("w1:p1", "s1", name="a")
    assert do_sleep(w)[0]
    st = {e["pane"]: e["status"] for e in sleep.load_record(TAB)["panes"]}
    assert st == {"w1:p8": "slept", "w1:p1": "slept"}


def test_a_pending_entry_whose_group_is_alive_resolves_to_failed(w):
    lead = w.add_agent("w1:p1", "s1", name="a")
    rec = {"tab": TAB, "panes": [{"pane": "w1:p1", "session": "s1", "status": "pending", "pgid": lead},
                                 {"pane": "w1:p2", "session": "s2", "status": "pending", "pgid": 4242}]}
    sleep.resolve_pending(rec, dict(w.procs))
    assert [e["status"] for e in rec["panes"]] == ["failed", "slept"]
