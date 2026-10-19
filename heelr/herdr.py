"""Thin wrapper over the `herdr` CLI (JSON over the socket API)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Tested versions are data, not code: the shipped list plus a per-user file that
# `heelr selftest --record` appends to.
_PACKAGE_TESTED = Path(__file__).with_name("tested_versions.txt")
# Internal: `heelr selftest` sets this in its child processes only, so the live tests
# can run on the version under test before it is recorded. Not a user setting; use
# --allow-untested instead.
SELFTEST_ALLOW_ENV = "_HEELR_SELFTEST_ALLOW_VERSION"
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class HerdrError(RuntimeError):
    pass


class HerdrTimeout(HerdrError):
    pass


class Reply(dict):
    """A JSON object from Herdr. A missing field is a HerdrError naming the
    field, instead of a bare KeyError."""

    def __missing__(self, key):
        raise HerdrError(f"Herdr's reply has no {key!r} field; this Herdr version may have "
                         f"changed its API (run `heelr selftest` to check it)")


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def call(*args: str, timeout: float | None = None, binary=None) -> dict:
    """Run `herdr <args>` (or `<binary> <args>`) and return its JSON `result`.
    No timeout unless asked for one."""
    try:
        proc = subprocess.run([str(binary or herdr_bin()), *args], capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise HerdrTimeout(f"herdr {' '.join(args)}: no answer after {timeout:g}s") from e
    if proc.returncode != 0:
        raise HerdrError(f"herdr {' '.join(args)} -> rc={proc.returncode}: "
                         f"{(proc.stderr or proc.stdout).strip()[:400]}")
    try:
        out = json.loads(proc.stdout, object_hook=Reply)
        if not isinstance(out, dict) or "result" not in out:
            raise KeyError("result")
        return out["result"]
    except (json.JSONDecodeError, KeyError) as e:
        raise HerdrError(f"herdr {' '.join(args)}: unexpected output: {proc.stdout[:400]}") from e


def run_in(pane: str, command: str) -> None:
    """Type a command into a pane's shell (`pane run` prints no JSON)."""
    proc = subprocess.run([herdr_bin(), "pane", "run", pane, command], capture_output=True, text=True)
    if proc.returncode != 0:
        raise HerdrError(f"herdr pane run {pane} -> rc={proc.returncode}: "
                         f"{(proc.stderr or proc.stdout).strip()[:400]}")


def snapshot(timeout: float | None = None) -> dict:
    return call("api", "snapshot", timeout=timeout)["snapshot"]


def user_tested_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "heelr" / "tested-versions"


def _read_versions(path: Path) -> set[str]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return set()
    return {ln.split("#", 1)[0].strip() for ln in lines} - {""}


def tested_versions() -> set[str]:
    return _read_versions(_PACKAGE_TESTED) | _read_versions(user_tested_file())


def record_tested(version: str) -> bool:
    """Add `version` to the user's tested list; False if it was already tested."""
    if version in tested_versions():
        return False
    f = user_tested_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        lead = "\n" if f.read_bytes()[-1:] not in (b"", b"\n") else ""  # file lacks a final newline
    except OSError:
        lead = ""
    with open(f, "a") as fh:
        fh.write(lead + version + "\n")
    return True


def parse_version(v) -> tuple[int, int, int] | None:
    """Strict X.Y.Z; anything else (pre-release suffixes, 'v' prefixes) is None."""
    m = _VERSION_RE.fullmatch(v) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def gate(v) -> str:
    """'tested', 'patch' (untested, but major.minor matches a tested version), or 'untested'."""
    tested = tested_versions()
    if v in tested or (v and v == os.environ.get(SELFTEST_ALLOW_ENV)):
        return "tested"
    pv = parse_version(v)
    if pv and any((t := parse_version(x)) and t[:2] == pv[:2] for x in tested):
        return "patch"
    return "untested"


def check_version(snap: dict, allow_untested: bool = False) -> None:
    v = snap.get("version")
    if allow_untested:
        return
    verdict = gate(v)
    if verdict == "patch":
        print(f"heelr: warning: Herdr {v} is an untested patch release; proceeding "
              f"(run `heelr selftest --record` to mark it tested)", file=sys.stderr)
    elif verdict == "untested":
        raise HerdrError(f"Herdr {v} is untested (tested: {', '.join(sorted(tested_versions()))}); "
                         f"run `heelr selftest --record`, or pass --allow-untested")


def cmdline(pane_id: str) -> str:
    info = call("pane", "process-info", "--pane", pane_id)["process_info"]
    procs = info.get("foreground_processes") or []
    return " | ".join(p.get("cmdline") or p.get("name", "") for p in procs)


def move(pane: str, *, tab: str | None = None, target: str | None = None, split: str | None = None,
         ratio: float | None = None, new_tab: bool = False, workspace: str | None = None,
         label: str | None = None, focus: bool = False) -> dict:
    args = ["pane", "move", pane]
    if new_tab:
        args.append("--new-tab")
        if workspace:
            args += ["--workspace", workspace]
        if label:
            args += ["--label", label]
    else:
        args += ["--tab", tab, "--split", split]
        if target:
            args += ["--target-pane", target]
        if ratio is not None:
            args += ["--ratio", f"{ratio:.4f}"]
    args.append("--focus" if focus else "--no-focus")
    return call(*args)["move_result"]


def split(pane: str, direction: str, ratio: float) -> dict:
    return call("pane", "split", pane, "--direction", direction, "--ratio", f"{ratio:.4f}",
                "--no-focus")["pane"]


def layout(pane: str) -> dict:
    return call("pane", "layout", "--pane", pane)["layout"]
