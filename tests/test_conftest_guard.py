"""The repo-wide guard in tests/conftest.py: no test, in any file, reaches a real process, signal or socket.

This module has no guard of its own, so it proves the conftest one applies everywhere. Nothing here may
run a real herdr/tmux: calls that must be blocked use harmless programs (`true`, a stub on a temp PATH)
and the dangerous ones (exec, fork, kill) are checked by identity, never called.
"""
import os
import signal
import socket
import subprocess

import pytest


@pytest.fixture
def stub_path(tmp_path, monkeypatch):
    """PATH holding only inert `herdr` and `tmux` stubs, so even an unblocked call cannot reach the real ones."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    for name in ("herdr", "tmux", "true"):
        p = bin_dir / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HERDR_BIN_PATH", "/nonexistent")
    return bin_dir


def is_blocked(fn):
    return getattr(fn, "heelr_guard", False) is True


@pytest.mark.parametrize("argv", [["herdr", "--version"], ["tmux", "ls"], ["true"]])
@pytest.mark.parametrize("call", [subprocess.run, subprocess.check_output, subprocess.call])
def test_subprocess_calls_are_blocked(stub_path, call, argv):
    with pytest.raises(AssertionError, match="unmocked"):
        call(argv)


def test_popen_is_blocked(stub_path):
    with pytest.raises(AssertionError, match="unmocked"):
        subprocess.Popen(["herdr", "--version"])


def test_os_system_and_popen_are_blocked(stub_path):
    with pytest.raises(AssertionError, match="unmocked"):
        os.system("true")
    with pytest.raises(AssertionError, match="unmocked"):
        os.popen("true")


def test_socket_connect_is_blocked(tmp_path):
    s = socket.socket(socket.AF_UNIX)
    try:
        with pytest.raises(AssertionError, match="unmocked"):
            s.connect(str(tmp_path / "nothing.sock"))
    finally:
        s.close()


def test_kill_with_signal_zero_is_blocked():
    with pytest.raises(AssertionError, match="unmocked"):
        os.kill(os.getpid(), 0)


@pytest.mark.parametrize("mod,name", [
    (os, "fork"), (os, "forkpty"), (os, "execv"), (os, "execvp"), (os, "execve"), (os, "killpg"),
    (os, "posix_spawn"), (os, "spawnv"), (signal, "pthread_kill"), (signal, "pidfd_send_signal"),
    (socket.socket, "sendto"), (socket.socket, "sendmsg"), (socket.socket, "connect_ex"),
])
def test_dangerous_primitives_are_replaced_not_called(mod, name):
    if not hasattr(mod, name):
        pytest.skip(f"{name} not on this platform")
    assert is_blocked(getattr(mod, name)), f"{name} is the real function"


def test_real_subprocess_opts_back_in_for_a_safe_program(real_subprocess, stub_path):
    assert real_subprocess(["true"]).returncode == 0
    assert subprocess.run(["true"]).returncode == 0
    assert is_blocked(os.kill)  # the opt-in is narrow


@pytest.mark.real_processes
def test_real_processes_marker_disables_the_guard(stub_path):
    assert subprocess.run(["true"]).returncode == 0
    assert not is_blocked(os.kill) and not is_blocked(socket.socket.connect)


def test_guard_is_per_test_not_leaked_by_the_opt_outs(stub_path):
    assert is_blocked(subprocess.run) and is_blocked(subprocess.Popen)


def test_real_signals_restores_only_kill(real_signals):
    os.kill(os.getpid(), 0)  # signal 0 to ourselves: a liveness check, delivers nothing
    assert not is_blocked(os.kill) and not is_blocked(os.killpg)
    assert is_blocked(subprocess.run) and is_blocked(os.fork) and is_blocked(socket.socket.connect)


@pytest.mark.real_processes
def test_live_tests_keep_the_real_socket_env(monkeypatch):
    """The layout-lock isolation must not redirect the throwaway server live tests drive."""
    import conftest
    assert os.environ.get("HERDR_SOCKET_PATH") == conftest.ORIGINAL_SOCKET_PATH


def test_normal_tests_get_an_isolated_socket_env(tmp_path):
    assert os.environ["HERDR_SOCKET_PATH"].startswith(str(tmp_path))


def test_real_subprocess_also_covers_the_posix_spawn_path(real_subprocess, monkeypatch):
    """CPython's subprocess takes os.posix_spawn instead of _posixsubprocess.fork_exec on newer glibc
    (CI on Python 3.13). Force that path here so the opt-out is proven on every interpreter."""
    monkeypatch.setattr(subprocess, "_USE_POSIX_SPAWN", True)
    assert not is_blocked(os.posix_spawn)
    real_spawn, calls = os.posix_spawn, []

    def recording(*args, **kw):
        calls.append(args[0])
        return real_spawn(*args, **kw)

    monkeypatch.setattr(os, "posix_spawn", recording)
    assert subprocess.run(["/bin/true"], close_fds=False).returncode == 0
    assert calls == ["/bin/true"]  # the posix_spawn path really ran
    assert is_blocked(os.kill)


def test_posix_spawn_stays_blocked_without_the_opt_out():
    assert is_blocked(os.posix_spawn)
    if hasattr(os, "posix_spawnp"):
        assert is_blocked(os.posix_spawnp)


def test_forced_posix_spawn_path_without_opt_out_hits_the_guard(monkeypatch):
    """The default guard must also stop CPython's posix_spawn path, not only fork_exec."""
    from conftest import REAL_POPEN
    monkeypatch.setattr(subprocess, "_USE_POSIX_SPAWN", True)
    with pytest.raises(AssertionError, match="unmocked real side effect: os.posix_spawn"):
        REAL_POPEN(["/bin/true"], close_fds=False)
