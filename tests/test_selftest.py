"""`heelr selftest` orchestration, against a fake herdr binary (no real server)."""
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from heelr import herdr, selftest

LIVE_SOCK = "/home/someone/.config/herdr/herdr.sock"

FAKE = textwrap.dedent(f"""\
    #!{sys.executable}
    # Fake herdr: enough of --version / server / server stop / api snapshot.
    import json, os, signal, socket, sys, time
    home = os.environ.get("HOME", "/nonexistent")
    sock = os.environ.get("HERDR_SOCKET_PATH") or os.path.join(home, ".config/herdr/herdr.sock")
    pidf = os.path.join(home, "fake.pid")
    a = sys.argv[1:]
    if a == ["--version"]:
        print("herdr " + os.environ.get("FAKE_VERSION", "9.8.7")); sys.exit(0)
    # behaviour switches are marker files in HOME (the child env is an allowlist)
    flag = lambda name: os.path.exists(os.path.join(home, name))
    if a == ["server"]:
        os.makedirs(os.path.dirname(sock), exist_ok=True)
        s = socket.socket(socket.AF_UNIX); s.bind(sock); s.listen()
        open(pidf, "w").write(str(os.getpid()))
        if flag("noisy"):
            print("fake server: something went wrong", flush=True)
        if flag("pane"):  # a pane process in its own session that ignores SIGHUP/SIGTERM
            import subprocess
            subprocess.Popen([sys.executable, "-c", "import signal, time; "
                              "signal.signal(signal.SIGHUP, signal.SIG_IGN); "
                              "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"],
                             start_new_session=True)
        signal.signal(signal.SIGTERM, (lambda *_: None) if flag("stuck") else (lambda *_: sys.exit(0)))
        while True: time.sleep(0.1)
    if a == ["server", "stop"]:
        os.kill(int(open(pidf).read()), signal.SIGTERM); sys.exit(0)
    if a == ["api", "snapshot"]:
        if not os.path.exists(sock): sys.exit(1)
        busy = [{{"pane_id": "w1-1"}}] if flag("dirty") else []
        print(json.dumps({{"result": {{"snapshot": {{"version": "9.8.7", "protocol": 22, "agents": [],
              "workspaces": busy, "tabs": busy, "panes": busy}}}}}})); sys.exit(0)
    sys.exit(2)
""")


@pytest.fixture
def fake_bin(tmp_path):
    p = tmp_path / "bin" / "herdr.bin"
    p.parent.mkdir()
    p.write_text(FAKE)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


@pytest.fixture
def short():
    """Short base dir: pytest's tmp_path is too deep for a unix socket."""
    import shutil
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="hst-", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Parent env looks like a shell inside the live Herdr."""
    for k, v in {"HERDR_SOCKET_PATH": LIVE_SOCK, "HERDR_PANE_ID": "p1", "HERDR_TAB_ID": "t1",
                 "HERDR_WORKSPACE_ID": "w1", "HERDR_ENV": "1", "HERDR_BIN_PATH": "/live/herdr.bin",
                 "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "XDG_STATE_HOME": "/live/state",
                 "XDG_RUNTIME_DIR": "/run/user/x", "TMUX": "/tmp/tmux,1,0", "_HEELR_SELFTEST_ALLOW_VERSION": "6.6.6"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(herdr, "_PACKAGE_TESTED", tmp_path / "pkg.txt")
    (tmp_path / "pkg.txt").write_text("0.9.3\n")
    return tmp_path


def test_child_env_has_no_live_herdr_state(env, tmp_path):
    home = tmp_path / "h"
    e = selftest.child_env(home, Path("/opt/herdr.bin"), "9.8.7")
    herdr_vars = {k for k in e if k.startswith("HERDR_")}
    assert herdr_vars == {"HERDR_BIN_PATH", "HERDR_SOCKET_PATH"}
    assert e["HERDR_SOCKET_PATH"] == str(home / ".config/herdr/herdr.sock") != LIVE_SOCK
    assert e["HERDR_BIN_PATH"] == "/opt/herdr.bin"
    assert e["HOME"] == str(home)
    assert e["_HEELR_SELFTEST_ALLOW_VERSION"] == "9.8.7" and e["HEELR_LIVE"] == "1"
    for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "TMUX"):
        assert k not in e
    assert LIVE_SOCK not in "".join(e.values())
    assert Path(e["PYTHONPATH"]) == Path(selftest.__file__).resolve().parents[1]


def test_assert_isolated_refuses_the_live_socket(env, tmp_path):
    e = selftest.child_env(tmp_path / "h", Path("/x"), "1.2.3")
    e["HERDR_SOCKET_PATH"] = LIVE_SOCK
    with pytest.raises(selftest.SelftestError, match="live"):
        selftest.assert_isolated(e)
    e = selftest.child_env(tmp_path / "h", Path("/x"), "1.2.3")
    e["HOME"] = os.path.expanduser("~")
    with pytest.raises(selftest.SelftestError):
        selftest.assert_isolated(e)


def test_make_home_is_short_and_checks_socket_length(tmp_path, short):
    home = selftest.make_home(base=short)
    try:
        assert home.parent == short and home.is_dir()
        assert (home / ".config/herdr/config.toml").exists()
    finally:
        import shutil
        shutil.rmtree(home)
    deep = tmp_path / ("d" * 100)
    deep.mkdir()
    with pytest.raises(selftest.SelftestError, match="108"):
        selftest.make_home(base=deep)
    assert list(deep.iterdir()) == []  # nothing left behind


def test_resolve_bin(tmp_path, monkeypatch, fake_bin):
    assert selftest.resolve_bin(str(fake_bin)) == fake_bin
    with pytest.raises(selftest.SelftestError):
        selftest.resolve_bin(str(tmp_path / "nope"))
    monkeypatch.delenv("HERDR_BIN_PATH", raising=False)
    wrapper = fake_bin.parent / "herdr"  # a `herdr` wrapper with herdr.bin beside it
    wrapper.write_text("#!/bin/sh\n")
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin.parent))
    assert selftest.resolve_bin(None) == fake_bin
    monkeypatch.setenv("HERDR_BIN_PATH", str(wrapper))
    assert selftest.resolve_bin(None) == wrapper
    monkeypatch.delenv("HERDR_BIN_PATH")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(selftest.SelftestError):
        selftest.resolve_bin(None)


def test_binary_version(fake_bin, env, real_subprocess):
    assert selftest.binary_version(fake_bin) == "9.8.7"


def _run(fake_bin, base, runner, record=False):
    homes = []

    def spy(cmd_env):
        homes.append(Path(cmd_env["HOME"]))
        assert (homes[0] / ".config/herdr/herdr.sock").exists()  # server is up while tests run
        return runner(cmd_env)
    rc = selftest.run(fake_bin, record=record, run_tests=spy, base=base, log=lambda *a: None)
    return rc, homes


def test_pass_records_and_tears_down(env, tmp_path, short, fake_bin, real_subprocess):
    rc, homes = _run(fake_bin, short, lambda e: (0, "3 passed"), record=True)
    assert rc == 0
    assert not homes[0].exists()
    assert "9.8.7" in herdr.tested_versions()
    assert (tmp_path / "cfg/heelr/tested-versions").read_text().split() == ["9.8.7"]


def test_pass_without_record_does_not_record(env, tmp_path, short, fake_bin, real_subprocess):
    rc, _ = _run(fake_bin, short, lambda e: (0, "3 passed"))
    assert rc == 0 and "9.8.7" not in herdr.tested_versions()


@pytest.mark.parametrize("result", [(1, "1 failed, 2 passed"), (0, "3 skipped"), (5, "no tests ran")])
def test_failure_or_skips_do_not_record_and_tear_down(env, tmp_path, short, fake_bin, result, real_subprocess):
    rc, homes = _run(fake_bin, short, lambda e: result, record=True)
    assert rc == 1
    assert not homes[0].exists()
    assert "9.8.7" not in herdr.tested_versions()


def test_interrupt_still_tears_down(env, tmp_path, short, fake_bin, real_subprocess, real_signals):
    pids = []

    def boom(e):
        pids.append(int((Path(e["HOME"]) / "fake.pid").read_text()))
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _run(fake_bin, short, boom, record=True)
    assert list(short.iterdir()) == []
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)  # fake server is gone (reaped)
    assert "9.8.7" not in herdr.tested_versions()


def test_cli_selftest_wires_bin_and_record(monkeypatch, fake_bin):
    from heelr import cli
    seen = {}
    monkeypatch.setattr(selftest, "run", lambda b, record=False: seen.update(bin=b, record=record) or 0)
    assert cli.main(["selftest", "--bin", str(fake_bin), "--record"]) == 0
    assert seen == {"bin": fake_bin.resolve(), "record": True}


def test_cli_selftest_error_is_reported_locally_not_notified(monkeypatch, capsys, tmp_path):
    from heelr import cli
    calls = []
    monkeypatch.setattr(herdr, "call", lambda *a: calls.append(a))
    assert cli.main(["selftest", "--bin", str(tmp_path / "missing")]) == 1
    assert "not an executable" in capsys.readouterr().err
    assert calls == []  # never talks to the (live) server, not even to notify


# --- defence in depth: fresh server on the throwaway socket, no leaks ---

def _home_with(markers):
    """make_home that also drops fake-herdr behaviour markers into the new HOME."""
    real = selftest.make_home

    def make(base=None):
        home = real(base)
        for m in markers:
            (home / m).touch()
        return home
    return make


def _gone(pid, timeout=5.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            return True
        if state == "Z":
            return True
        time.sleep(0.05)
    return False


def test_refuses_a_server_that_already_has_panes(env, short, fake_bin, monkeypatch, real_subprocess):
    monkeypatch.setattr(selftest, "make_home", _home_with(["dirty"]))
    ran = []
    with pytest.raises(selftest.SelftestError, match="not a fresh"):
        selftest.run(fake_bin, run_tests=lambda e: ran.append(1) or (0, "1 passed"), base=short,
                     log=lambda *a: None)
    assert ran == [] and list(short.iterdir()) == []


def test_verify_fresh_requires_the_socket_under_the_throwaway_home(env, short, fake_bin, tmp_path):
    home = selftest.make_home(base=short)
    try:
        e = selftest.child_env(home, fake_bin, "9.8.7")
        with pytest.raises(selftest.SelftestError, match="socket"):
            selftest.verify_fresh(fake_bin, e, home)  # no server yet: no socket
        e["HERDR_SOCKET_PATH"] = str(tmp_path / "elsewhere.sock")
        with pytest.raises(selftest.SelftestError, match="socket"):
            selftest.verify_fresh(fake_bin, e, home)
    finally:
        import shutil
        shutil.rmtree(home)


def test_verify_fresh_refuses_the_callers_own_socket(env, short, fake_bin, monkeypatch, real_subprocess):
    """Even under the throwaway HOME, the socket must not be the one the caller is using."""
    seen = []

    def check(cmd_env):
        monkeypatch.setenv("HERDR_SOCKET_PATH", cmd_env["HERDR_SOCKET_PATH"])
        with pytest.raises(selftest.SelftestError, match="live"):
            selftest.verify_fresh(fake_bin, cmd_env, Path(cmd_env["HOME"]))
        monkeypatch.setenv("HERDR_SOCKET_PATH", LIVE_SOCK)
        selftest.verify_fresh(fake_bin, cmd_env, Path(cmd_env["HOME"]))
        seen.append(1)
        return 0, "1 passed"
    assert selftest.run(fake_bin, run_tests=check, base=short, log=lambda *a: None) == 0
    assert seen == [1]


def test_make_home_cleans_up_if_setup_fails(short, monkeypatch):
    monkeypatch.setattr(selftest, "CONFIG", None)  # write_text(None) -> TypeError after mkdtemp
    with pytest.raises(TypeError):
        selftest.make_home(base=short)
    assert list(short.iterdir()) == []


def test_sigterm_while_creating_home_still_cleans_up(env, short, fake_bin, monkeypatch, real_subprocess, real_signals):
    """No window between mkdtemp and the cleanup `try`: stop signals are held until it's armed."""
    import signal
    real = selftest.make_home

    def make(base=None):
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])
        assert {signal.SIGINT, signal.SIGTERM} <= blocked
        home = real(base)
        os.kill(os.getpid(), signal.SIGTERM)  # pending until unblocked
        return home
    monkeypatch.setattr(selftest, "make_home", make)
    with pytest.raises(KeyboardInterrupt):
        selftest.run(fake_bin, run_tests=lambda e: pytest.fail("must not get this far"), base=short,
                     log=lambda *a: None)
    assert list(short.iterdir()) == []
    assert signal.SIGTERM not in signal.pthread_sigmask(signal.SIG_BLOCK, [])


@pytest.mark.parametrize("markers", [["pane"], ["pane", "stuck"]])
def test_stop_leaves_no_pane_processes_behind(env, short, fake_bin, monkeypatch, markers, real_subprocess, real_signals):
    """A pane process in its own session survives the server (and killpg); stop sweeps it,
    after a clean `server stop` and after the SIGKILL fallback alike."""
    monkeypatch.setattr(selftest, "make_home", _home_with(markers))
    monkeypatch.setattr(selftest, "STOP_TIMEOUT", 1.0)
    found = {}

    def runner(e):
        import time
        home = Path(e["HOME"])
        found["server"] = int((home / "fake.pid").read_text())
        want = b"HOME=" + os.fsencode(home)
        for _ in range(100):
            kids = [int(d.name) for d in Path("/proc").iterdir() if d.name.isdigit()
                    and int(d.name) != found["server"] and _environ_has(d, want)]
            if kids:
                found["pane"] = kids[0]
                break
            time.sleep(0.05)
        return 0, "1 passed"
    assert selftest.run(fake_bin, run_tests=runner, base=short, log=lambda *a: None) == 0
    assert "pane" in found
    assert _gone(found["server"]) and _gone(found["pane"])
    assert list(short.iterdir()) == []


def _environ_has(d, want):
    try:
        return want in (d / "environ").read_bytes().split(b"\0")
    except OSError:
        return False


# --- review C2: a hung binary is a SelftestError, not a traceback ---

def _hang(monkeypatch):
    import subprocess

    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(selftest.subprocess, "run", run)


def test_binary_version_timeout_is_selftest_error(monkeypatch):
    _hang(monkeypatch)
    with pytest.raises(selftest.SelftestError, match="timed out"):
        selftest.binary_version(Path("/nonexistent/herdr"))


def test_wait_ready_timeout_is_selftest_error(monkeypatch):
    _hang(monkeypatch)

    class Alive:
        returncode = None

        def poll(self):
            return None
    with pytest.raises(selftest.SelftestError, match="timed out"):
        selftest._wait_ready(Path("/nonexistent/herdr"), {}, Alive())


def test_verify_fresh_timeout_is_selftest_error(monkeypatch, tmp_path):
    _hang(monkeypatch)
    monkeypatch.setattr(Path, "is_socket", lambda self: True)
    home = tmp_path / "home"
    (home / ".config" / "herdr").mkdir(parents=True)
    env = {"HERDR_SOCKET_PATH": str(selftest.socket_path(home))}
    with pytest.raises(selftest.SelftestError, match="timed out"):
        selftest.verify_fresh(Path("/nonexistent/herdr"), env, home)


# --- review minors: cleanup is robust to a second signal, and keeps what it needs ---

def test_second_interrupt_during_cleanup_still_tears_down(env, short, fake_bin, monkeypatch, real_subprocess,
                                                          real_signals):
    """A second Ctrl-C while cleaning up is held until the server and HOME are gone."""
    import signal
    real_stop, pids = selftest._stop, []

    def stop(binary, cenv, server, home):
        pids.append(server.pid)
        os.kill(os.getpid(), signal.SIGINT)  # the impatient second Ctrl-C
        real_stop(binary, cenv, server, home)

    def boom(e):
        raise KeyboardInterrupt
    monkeypatch.setattr(selftest, "_stop", stop)
    try:
        with pytest.raises(KeyboardInterrupt):
            selftest.run(fake_bin, run_tests=boom, base=short, log=lambda *a: None)
        assert _gone(pids[0])
        assert list(short.iterdir()) == []
        assert not {signal.SIGINT, signal.SIGTERM} & signal.pthread_sigmask(signal.SIG_BLOCK, [])
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_interrupt_between_popen_and_assignment_leaves_no_server(env, short, fake_bin, monkeypatch,
                                                                 real_subprocess, real_signals):
    import signal
    import subprocess
    real_popen, pids = subprocess.Popen, []

    def popen(cmd, *a, **kw):
        p = real_popen(cmd, *a, **kw)
        if cmd[1:] == ["server"]:
            pids.append(p.pid)
            raise KeyboardInterrupt  # spawned, but `server` never assigned
        return p
    monkeypatch.setattr(subprocess, "Popen", popen)
    try:
        with pytest.raises(KeyboardInterrupt):
            selftest.run(fake_bin, run_tests=lambda e: pytest.fail("must not get this far"), base=short,
                         log=lambda *a: None)
        assert _gone(pids[0])
        assert list(short.iterdir()) == []
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


@pytest.mark.parametrize("runner", [lambda e: (1, "1 failed"),
                                    lambda e: (_ for _ in ()).throw(selftest.SelftestError("broke"))])
def test_failure_shows_server_output_before_removing_it(env, short, fake_bin, monkeypatch, runner,
                                                        real_subprocess):
    monkeypatch.setattr(selftest, "make_home", _home_with(["noisy"]))
    lines = []
    try:
        selftest.run(fake_bin, run_tests=runner, base=short, log=lambda *a: lines.append(" ".join(map(str, a))))
    except selftest.SelftestError:
        pass
    assert "fake server: something went wrong" in "\n".join(lines)
    assert list(short.iterdir()) == []


def test_pass_does_not_show_server_output(env, short, fake_bin, monkeypatch, real_subprocess):
    monkeypatch.setattr(selftest, "make_home", _home_with(["noisy"]))
    lines = []
    assert selftest.run(fake_bin, run_tests=lambda e: (0, "1 passed"), base=short,
                        log=lambda *a: lines.append(" ".join(map(str, a)))) == 0
    assert "something went wrong" not in "\n".join(lines)


def test_an_interrupt_does_not_dump_server_output(env, short, fake_bin, monkeypatch, real_subprocess,
                                                  real_signals):
    """Ctrl-C is the user leaving, not a failure to explain."""
    monkeypatch.setattr(selftest, "make_home", _home_with(["noisy"]))
    lines = []

    def interrupted(e):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        selftest.run(fake_bin, run_tests=interrupted, base=short,
                     log=lambda *a: lines.append(" ".join(map(str, a))))
    assert "something went wrong" not in "\n".join(lines)
    assert list(short.iterdir()) == []
