"""The stage for the README screenshots: a private X server (Xvfb :77), kitty on it, and a
throwaway Herdr server, all isolated from the real display and the live Herdr.

Safety, by construction:
  * every X client gets DISPLAY=:77 explicitly; kitty and xwd never see the caller's display;
  * every herdr/heelr process gets an allowlisted environment whose HOME and HERDR_SOCKET_PATH
    are inside a throwaway directory (heelr.selftest's guards, reused);
  * everything is torn down on exit, and only processes this run started are signalled.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from heelr import selftest  # noqa: E402

DISPLAY = ":77"
GROUND = REPO / "tools" / "screenshots"
STUB_CLAUDE = r"""#!/bin/bash
# Stand-in for Claude Code: sets the terminal title, draws an idle prompt box (Herdr reads the
# agent's state from the screen), then becomes a long-lived process whose argv[0] is "claude".
# It does nothing else. Usage: [STUB_TITLE=<task>] claude <task title> | --resume <session id>
title=${STUB_TITLE:-$1}
printf '\033]0;%s\007' "✳ $title"
tilde='~'
rule=$(printf '─%.0s' $(seq 20))
SCREEN=$(printf ' \033[38;5;208m✻\033[0m Claude Code \033[2m(demo)\033[0m\n \033[2m%s\033[0m\n %s\n > \n %s\n' \
  "${PWD/#$HOME/$tilde}" "$rule" "$rule")
export SCREEN
printf '%s\n' "$SCREEN"
if [ "$1" = --resume ]; then   # what Claude's SessionStart hook reports (heelr sleep needs it)
  "${HERDR_BIN_PATH:-herdr}" pane report-agent-session "$HERDR_PANE_ID" --source herdr:claude --agent claude \
    --agent-session-id "$2" >/dev/null 2>&1
fi
printf '\033[?25l'   # no cursor under the prompt box
# redraw the whole screen on resize (heelr apply resizes panes)
exec -a claude python3 -c 'import os, signal, sys, time
def redraw(*_):
    sys.stdout.write("\033[?25l\033[H\033[2J" + os.environ["SCREEN"]); sys.stdout.flush()
signal.signal(signal.SIGWINCH, redraw); signal.signal(signal.SIGCHLD, signal.SIG_IGN)
while True: time.sleep(1e9)' "$@"
"""


def sleep(s: float) -> None:
    time.sleep(s)


class Stage:
    def __init__(self, width: int = 1400, height: int = 800, font_size: float = 12.0):
        self.width, self.height, self.font_size = width, height, font_size
        self.procs: list[subprocess.Popen] = []
        self.home: Path | None = None
        self.env: dict[str, str] = {}
        self.binary: Path | None = None
        self.kitty_sock: str | None = None
        self.tmp: Path | None = None

    # ---------------------------------------------------------------- lifecycle
    def __enter__(self):
        if Path(f"/tmp/.X11-unix/X{DISPLAY[1:]}").exists():
            raise SystemExit(f"display {DISPLAY} is already in use; refusing to share it")
        self.binary = selftest.resolve_bin(None)
        version = selftest.binary_version(self.binary)
        self.home = selftest.make_home()
        self.tmp = Path(selftest.tempfile.mkdtemp(prefix="hs-", dir=selftest._base_dir()))
        self.env = selftest.child_env(self.home, self.binary, version)
        self.home_real = os.path.expanduser("~")
        stub = self.tmp / "bin"
        stub.mkdir()
        (stub / "claude").write_text(STUB_CLAUDE)
        (stub / "claude").chmod(0o755)
        self.env.update(PATH=f"{stub}:{REPO / 'bin'}:/usr/local/bin:/usr/bin:/bin",
                        SHELL="/bin/bash", TERM="xterm-kitty", COLORTERM="truecolor",
                        LANG=os.environ.get("LANG") or "C.UTF-8")
        selftest.assert_isolated(self.env)
        (self.home / ".bashrc").write_text("PS1='\\[\\e[32m\\]\\w\\[\\e[0m\\] $ '\nexport PS1\n")
        (self.home / ".bash_profile").write_text(". ~/.bashrc\n")
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        for p in reversed(self.procs):
            if p.poll() is None:
                try:
                    if p.args and str(self.binary) in map(str, p.args) and "server" in p.args:
                        selftest._stop(self.binary, self.env, p, self.home)
                        continue
                    os.killpg(p.pid, signal.SIGTERM)
                except OSError:
                    pass
        for p in reversed(self.procs):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
        if self.home:
            selftest._sweep(self.home)
            shutil.rmtree(self.home, ignore_errors=True)
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- processes
    def x_env(self) -> dict[str, str]:
        """Environment for X clients: the private display and nothing else of the caller's."""
        return {"HOME": self.home_real, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "DISPLAY": DISPLAY,
                "LANG": self.env["LANG"]}

    def start_xvfb(self):
        p = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", f"{self.width}x{self.height}x24", "-nolisten", "tcp"],
                             env=self.x_env(), start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        for _ in range(50):
            if Path(f"/tmp/.X11-unix/X{DISPLAY[1:]}").exists():
                return
            sleep(0.1)
        raise SystemExit("Xvfb did not start")

    def start_server(self, config: str):
        (self.home / ".config" / "herdr" / "config.toml").write_text(config)
        with open(self.home / "server.out", "w") as out:
            p = subprocess.Popen([str(self.binary), "server"], env=self.env, cwd=self.home, stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append(p)
        selftest._wait_ready(self.binary, self.env, p)
        selftest.verify_fresh(self.binary, self.env, self.home)

    def start_kitty(self):
        """kitty on the private display running the Herdr client against the throwaway server."""
        self.kitty_sock = str(self.tmp / "kitty.sock")
        client_env = [f"{k}={v}" for k, v in self.env.items()]
        cmd = ["kitty", "--listen-on", f"unix:{self.kitty_sock}",
               "-o", "allow_remote_control=socket-only", "-o", f"initial_window_width={self.width}",
               "-o", f"initial_window_height={self.height}", "-o", "remember_window_size=no",
               "-o", f"font_size={self.font_size}", "-o", "window_padding_width=0", "-o", "cursor=none",
               "-o", "confirm_os_window_close=0", "-o", "hide_window_decorations=yes",
               "env", "-i", *client_env, str(self.binary)]
        p = subprocess.Popen(cmd, env=self.x_env(),
                             cwd=self.home, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        for _ in range(100):
            if Path(self.kitty_sock).exists():
                return
            sleep(0.1)
        raise SystemExit("kitty did not start")

    # ---------------------------------------------------------------- driving
    def herdr(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run([str(self.binary), *args], env=self.env, capture_output=True, text=True, timeout=30)
        if check and r.returncode != 0:
            raise SystemExit(f"herdr {' '.join(args)}: {r.stderr or r.stdout}")
        return r

    def heelr(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, "-m", "heelr", *args], env=self.env, capture_output=True, text=True,
                           timeout=60)
        if check and r.returncode != 0:
            raise SystemExit(f"heelr {' '.join(args)}: {r.stderr or r.stdout}")
        return r

    def snap(self) -> dict:
        import json
        return json.loads(self.herdr("api", "snapshot").stdout)["result"]["snapshot"]

    def pane_status(self, pane: str) -> str | None:
        import json
        return json.loads(self.herdr("pane", "get", pane).stdout)["result"]["pane"].get("agent_status")

    def kitten(self, *args: str):
        subprocess.run(["kitten", "@", "--to", f"unix:{self.kitty_sock}", *args], env=self.x_env(), check=True,
                       capture_output=True, timeout=15)

    def refresh(self):
        """Make Herdr resize every pane's terminal to its current share of the screen. After a layout
        change done through the API it only does so on the next client resize; nudge the window."""
        for w in (self.width - 20, self.width):
            self.kitten("resize-os-window", "--action=resize", "--width", str(w), "--height", str(self.height),
                        "--unit", "pixels")
            sleep(1.0)

    def keys(self, *keys: str):
        """Key presses into the Herdr client, e.g. keys("ctrl+b", "m")."""
        self.kitten("send-key", *keys)

    def grab(self, name: str):
        """Capture the private display only; returns the image."""
        sys.path.insert(0, str(GROUND))
        import pnglib
        path = self.tmp / f"{name}.xwd"
        subprocess.run(["xwd", "-root", "-silent", "-out", str(path)], env=self.x_env(), check=True, timeout=30)
        img = pnglib.read_xwd(str(path))
        path.unlink()
        return img
