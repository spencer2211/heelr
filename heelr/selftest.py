"""`heelr selftest`: run the live tests against a throwaway Herdr server.

The server runs with its own short HOME (under /run/user/$UID, for the 108-byte
unix socket limit) and an environment built from an allowlist, so neither it nor
the tests can reach the live server the caller may be running inside.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import herdr

SUN_PATH_MAX = 107  # sockaddr_un.sun_path is 108 bytes including the NUL
REPO = Path(__file__).resolve().parents[1]
LIVE_TESTS = REPO / "tests" / "test_live.py"
STOP_TIMEOUT = 10.0  # seconds for `server stop` to take effect before SIGKILL
_STOP_SIGNALS = {signal.SIGINT, signal.SIGTERM}
PASS_THROUGH = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "SHELL", "USER", "LOGNAME")
# Defaults only: no network checks, no onboarding or sound in the throwaway server.
CONFIG = """onboarding = false

[update]
manifest_check = false
version_check = false

[ui.sound]
enabled = false
"""


class SelftestError(RuntimeError):
    pass


def resolve_bin(arg: str | None) -> Path:
    """--bin, else $HERDR_BIN_PATH, else herdr.bin beside the `herdr` on PATH, else that `herdr`."""
    if arg:
        cand = Path(arg)
    elif os.environ.get("HERDR_BIN_PATH"):
        cand = Path(os.environ["HERDR_BIN_PATH"])
    else:
        found = shutil.which("herdr")
        if not found:
            raise SelftestError("no herdr on PATH; pass --bin")
        beside = Path(found).with_name("herdr.bin")
        cand = beside if beside.is_file() else Path(found)
    if not (cand.is_file() and os.access(cand, os.X_OK)):
        raise SelftestError(f"{cand} is not an executable file")
    return cand.resolve()


def _base_dir() -> Path:
    run = Path(f"/run/user/{os.getuid()}")
    return run if run.is_dir() else Path(tempfile.gettempdir())


def socket_path(home: Path) -> Path:
    return home / ".config" / "herdr" / "herdr.sock"


def make_home(base: Path | None = None) -> Path:
    base = base or _base_dir()
    # the longest socket Herdr makes is herdr-client.sock; check that one
    probe = len(os.fsencode(base / "hx-XXXXXXXX" / ".config" / "herdr" / "herdr-client.sock"))
    if probe > SUN_PATH_MAX:
        raise SelftestError(f"socket path under {base} would be {probe} bytes; unix sockets allow "
                            f"108 including the NUL")
    home = Path(tempfile.mkdtemp(prefix="hx-", dir=base))
    try:
        (home / ".config" / "herdr").mkdir(parents=True)
        (home / ".config" / "herdr" / "config.toml").write_text(CONFIG)
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise
    return home


def child_env(home: Path, binary: Path, version: str) -> dict[str, str]:
    """Allowlisted environment for the throwaway server and the tests.
    No HERDR_*/XDG_*/TMUX from the caller, so nothing can find the live socket."""
    env = {k: os.environ[k] for k in PASS_THROUGH if k in os.environ}
    env.update(HOME=str(home), HERDR_SOCKET_PATH=str(socket_path(home)), HERDR_BIN_PATH=str(binary),
               HEELR_LIVE="1", PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1")
    env[herdr.SELFTEST_ALLOW_ENV] = version
    return env


def assert_isolated(env: dict[str, str]) -> None:
    real_home = os.path.expanduser("~")
    live = {os.environ.get("HERDR_SOCKET_PATH"), os.path.join(real_home, ".config", "herdr", "herdr.sock")}
    sock = env.get("HERDR_SOCKET_PATH")
    if not sock or sock in live or os.path.realpath(env.get("HOME", "")) == os.path.realpath(real_home):
        raise SelftestError("refusing to run: the child environment points at the live Herdr")
    if not sock.startswith(env["HOME"] + os.sep):
        raise SelftestError(f"refusing to run: socket {sock} is outside the throwaway HOME")
    stray = {k for k in env if k.startswith("HERDR_")} - {"HERDR_SOCKET_PATH", "HERDR_BIN_PATH"}
    if stray:
        raise SelftestError(f"refusing to run: live Herdr variables in child env: {sorted(stray)}")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """subprocess.run with a timeout that surfaces as a SelftestError, not a traceback."""
    kw.setdefault("timeout", 10)
    try:
        return subprocess.run(cmd, capture_output=True, **kw)
    except subprocess.TimeoutExpired as e:
        raise SelftestError(f"`{' '.join(cmd[1:])}` timed out after {kw['timeout']:g}s: {cmd[0]}") from e


def binary_version(binary: Path) -> str:
    env = {k: os.environ[k] for k in PASS_THROUGH if k in os.environ}
    out = _run([str(binary), "--version"], text=True, env=env)
    m = re.search(r"(\d+\.\d+\.\d+\S*)", out.stdout)
    if out.returncode != 0 or not m:
        raise SelftestError(f"{binary} --version: unexpected output {out.stdout.strip()!r}")
    return m.group(1)


def _wait_ready(binary: Path, env: dict, server: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SelftestError(f"throwaway server exited early (rc={server.returncode})")
        r = _run([str(binary), "api", "snapshot"], env=env)
        if r.returncode == 0:
            return
        time.sleep(0.2)
    raise SelftestError(f"throwaway server not ready after {timeout:.0f}s")


def verify_fresh(binary: Path, env: dict[str, str], home: Path) -> None:
    """Defence in depth, before any test runs: the server answering is a fresh one on a
    socket inside the throwaway HOME, not the caller's (live) server."""
    sock = Path(env["HERDR_SOCKET_PATH"])
    if not sock.resolve().is_relative_to(home.resolve()) or not sock.is_socket():
        raise SelftestError(f"refusing to run: no throwaway server socket at {sock} under {home}")
    live = Path(os.environ.get("HERDR_SOCKET_PATH") or socket_path(Path.home()))
    try:
        if os.path.samefile(sock, live):
            raise SelftestError(f"refusing to run: {sock} is the live Herdr socket")
    except OSError:  # no live socket: nothing to confuse it with
        pass
    r = _run([str(binary), "api", "snapshot"], text=True, env=env)
    try:
        snap = json.loads(r.stdout)["result"]["snapshot"]
    except (ValueError, KeyError, TypeError) as e:
        raise SelftestError(f"refusing to run: unreadable snapshot from {sock}") from e
    busy = {k: len(snap.get(k) or []) for k in ("workspaces", "tabs", "panes")}
    if any(busy.values()):
        raise SelftestError(f"refusing to run: the server on {sock} is not a fresh throwaway server "
                            f"({', '.join(f'{n} {k}' for k, n in busy.items())})")


def _sweep(home: Path) -> None:
    """SIGKILL every process of ours still running with HOME=<the throwaway home>: pane
    processes live in their own sessions, so they can outlive a killed server and its group."""
    want, me, uid = b"HOME=" + os.fsencode(home), os.getpid(), os.getuid()
    proc = Path("/proc")
    if not proc.is_dir():
        return
    for d in proc.iterdir():
        if not d.name.isdigit() or int(d.name) == me:
            continue
        try:
            if d.stat().st_uid == uid and want in (d / "environ").read_bytes().split(b"\0"):
                os.kill(int(d.name), signal.SIGKILL)
        except OSError:
            pass


def _stop(binary: Path, env: dict, server: subprocess.Popen, home: Path) -> None:
    if server.poll() is None:
        try:
            subprocess.run([str(binary), "server", "stop"], capture_output=True, env=env, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            server.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(server.pid, signal.SIGKILL)  # start_new_session: its group is its pid
            except OSError:
                server.kill()
            server.wait()
    _sweep(home)


def pytest_runner(env: dict[str, str]) -> tuple[int, str]:
    """Run tests/test_live.py in the isolated env; returns (rc, pytest's summary line)."""
    if not LIVE_TESTS.is_file():
        raise SelftestError(f"{LIVE_TESTS} not found (selftest needs a source checkout)")
    proc = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                             str(LIVE_TESTS)], env=env, cwd=env["HOME"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    last = ""
    try:
        for line in proc.stdout:
            sys.stdout.write("  " + line)
            if line.strip():
                last = line.strip()
        return proc.wait(), last
    finally:
        if proc.poll() is None:  # interrupted: don't leave pytest running against a dying server
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def passed(rc: int, summary: str) -> bool:
    """All collected tests ran and passed: a run that skipped (e.g. HEELR_LIVE unset) is not a pass."""
    s = summary.lower()
    return rc == 0 and " passed" in s and not any(w in s for w in ("skipped", "failed", "error", "deselected"))


def _show_output(path: Path, log, lines: int = 20) -> None:
    try:
        tail = path.read_text(errors="replace").splitlines()[-lines:]
    except OSError:
        return
    if tail:
        log(f"selftest: throwaway server output (last {len(tail)} lines):")
        for line in tail:
            log("  " + line)


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def run(binary: Path, record: bool = False, run_tests=pytest_runner, base: Path | None = None,
        log=lambda *a: print(*a, flush=True)) -> int:
    version = binary_version(binary)
    # Hold Ctrl-C/SIGTERM until the cleanup below is armed: no window in which the
    # throwaway HOME exists but nothing will remove it. A held signal fires on unblock.
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _STOP_SIGNALS)
    old_term = signal.signal(signal.SIGTERM, _raise_interrupt)
    home = server = None
    ok = interrupted = False
    try:
        try:
            home = make_home(base)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        env = child_env(home, binary, version)
        assert_isolated(env)
        log(f"selftest: Herdr {version} ({binary}) in throwaway HOME {home}")
        with open(home / "server.out", "w") as out:
            server = subprocess.Popen([str(binary), "server"], env=env, cwd=home, stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        _wait_ready(binary, env, server)
        verify_fresh(binary, env, home)
        rc, summary = run_tests(env)
        ok = passed(rc, summary)
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        # a second Ctrl-C must not cut the cleanup short: hold it until the end
        signal.pthread_sigmask(signal.SIG_BLOCK, _STOP_SIGNALS)
        try:
            if server is not None:
                _stop(binary, env, server, home)
            if home is not None:
                _sweep(home)  # also a server spawned but not yet assigned
                if not ok and not interrupted:
                    _show_output(home / "server.out", log)
                shutil.rmtree(home, ignore_errors=True)
        finally:
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
            finally:
                signal.signal(signal.SIGTERM, old_term)
    if not ok:
        log(f"selftest: FAIL on Herdr {version} ({summary or f'rc={rc}'}); not recorded")
        return 1
    log(f"selftest: PASS on Herdr {version} ({summary})")
    if record:
        if herdr.record_tested(version):
            log(f"recorded {version} as tested in {herdr.user_tested_file()}")
        else:
            log(f"{version} was already listed as tested")
    elif herdr.gate(version) != "tested":
        log("pass --record to add it to the tested versions")
    return 0
