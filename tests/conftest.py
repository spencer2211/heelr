"""Repo-wide safety net: no test may reach a real process, signal or socket unless it opts out.

Nothing in the suite should touch the user's live herdr/tmux, so every spawn, exec, fork, signal and
socket connect is replaced by a stub that raises. A test that needs one stubs that specific function
itself (monkeypatch overrides this guard). Three explicit, narrow opt-outs exist:

- the `real_subprocess` fixture restores subprocess.run/Popen only, for a test that runs a known-safe
  local program (the update tests run a fake herdr binary written to tmp_path);
- the `real_signals` fixture restores os.kill/os.killpg only, for a test that signals a process it
  started itself (the selftest teardown tests, which stop their own fake server);
- `@pytest.mark.real_processes` lifts the whole guard, for tests/test_live.py, which drives a throwaway
  server under `heelr selftest` (HEELR_LIVE=1).
"""
import os
import pty
import signal
import socket
import subprocess

import _posixsubprocess
import pytest

# The real functions, captured at import (before any fixture blocks them), for the opt-outs.
REAL_RUN, REAL_POPEN = subprocess.run, subprocess.Popen
REAL_FORK_EXEC = _posixsubprocess.fork_exec
REAL_KILL, REAL_KILLPG = os.kill, os.killpg

SUBPROCESS_CALLS = ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput")
OS_CALLS = ("system", "kill", "killpg", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
            "execlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp", "popen", "fork")
# the layers below those: pty forks, the raw fork+exec under subprocess, signals by pidfd/thread,
# and datagrams sent without connect()
LOW_LEVEL = [(os, "forkpty"), (pty, "fork"), (_posixsubprocess, "fork_exec"), (signal, "pthread_kill"),
             (signal, "pidfd_send_signal"), (socket.socket, "sendto"), (socket.socket, "sendmsg"),
             (socket.socket, "connect"), (socket.socket, "connect_ex")]


def pytest_configure(config):
    config.addinivalue_line("markers", "real_processes: lift the conftest process/signal/socket guard "
                                       "(live tests against a throwaway server only)")


def _blocked(name):
    def f(*args, **kw):
        raise AssertionError(f"unmocked real side effect: {name}{args!r}")
    f.heelr_guard = True  # lets a test tell the stub from the real function without calling it
    return f


@pytest.fixture(autouse=True)
def no_real_side_effects(request, monkeypatch):
    if request.node.get_closest_marker("real_processes"):
        return
    for name in SUBPROCESS_CALLS:
        monkeypatch.setattr(subprocess, name, _blocked(f"subprocess.{name}"))
    for name in OS_CALLS:
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _blocked(f"os.{name}"))
    for mod, name in LOW_LEVEL:
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, _blocked(f"{getattr(mod, '__name__', mod)}.{name}"))


@pytest.fixture
def real_subprocess(monkeypatch):
    """Opt-in: restore subprocess.run/Popen (run needs Popen) for a test that runs a known-safe local
    program. Everything else (os.kill, exec, sockets) stays blocked."""
    monkeypatch.setattr(subprocess, "Popen", REAL_POPEN)
    monkeypatch.setattr(subprocess, "run", REAL_RUN)
    monkeypatch.setattr(_posixsubprocess, "fork_exec", REAL_FORK_EXEC)
    return REAL_RUN


@pytest.fixture
def real_signals(monkeypatch):
    """Opt-in: restore os.kill/os.killpg for a test that signals only processes it started itself
    (or the test process). Spawning stays blocked unless real_subprocess is also requested."""
    monkeypatch.setattr(os, "kill", REAL_KILL)
    monkeypatch.setattr(os, "killpg", REAL_KILLPG)


ORIGINAL_SOCKET_PATH = os.environ.get("HERDR_SOCKET_PATH")   # captured before any fixture runs


@pytest.fixture(autouse=True)
def isolated_layout_lock(request, tmp_path, monkeypatch):
    """apply_lock takes <HERDR_SOCKET_PATH>.layout.lock; never the live one.
    Live tests (real_processes) drive a throwaway server named by that variable: leave it alone."""
    if request.node.get_closest_marker("real_processes"):
        return
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
