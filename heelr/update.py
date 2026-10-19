"""`heelr check-update`: compare herdr.dev/latest.json with the installed binary
and the running server. Read-only: it never installs, restarts or edits config."""
from __future__ import annotations

import http.client
import json
import subprocess
import urllib.request

from . import herdr

LATEST_URL = "https://herdr.dev/latest.json"
STATUS_TIMEOUT = 10.0  # seconds to wait on the running server
UP_TO_DATE, ERROR, UPDATE_AVAILABLE, RESTART_PENDING = 0, 1, 10, 11

GATE_TEXT = {
    "tested": "heelr: already tested with it",
    "patch": "heelr: would run on it with a warning (untested patch release)",
    "untested": "heelr: would refuse it until `heelr selftest --record` passes on it",
}


class UpdateError(RuntimeError):
    pass


def fetch_latest(opener=urllib.request.urlopen, timeout: float = 10) -> dict:
    # herdr.dev answers 403 to urllib's default User-Agent
    req = urllib.request.Request(LATEST_URL, headers={"User-Agent": "heelr-check-update",
                                                      "Accept": "application/json"})
    try:
        with opener(req, timeout=timeout) as resp:
            body = resp.read()
    except (OSError, ValueError, http.client.HTTPException) as e:
        # URLError, timeouts and resets are OSError; IncompleteRead/BadStatusLine are HTTPException
        raise UpdateError(f"could not fetch {LATEST_URL}: {e}") from e
    try:
        data = json.loads(body)
    except ValueError as e:
        raise UpdateError(f"{LATEST_URL}: not JSON") from e
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        raise UpdateError(f"{LATEST_URL}: no version field")
    return data


def server_info(binary) -> dict | None:
    """The running server as {"version", "protocol", "restart_needed"}, or None if none is running.

    Asks `<binary> status --json` first: it reports the server's version even when this
    client can't speak its protocol (e.g. just after an install that bumped it), plus
    Herdr's own restart_needed / server_binary_stale verdict. Falls back to the read-only
    `<binary> api snapshot` if that answer is missing or unreadable. A server that doesn't
    answer within STATUS_TIMEOUT is an UpdateError, not a reason to try again."""
    try:
        r = subprocess.run([str(binary), "status", "--json"], capture_output=True, text=True,
                           timeout=STATUS_TIMEOUT)
        srv = json.loads(r.stdout)["server"] if r.returncode == 0 else None
        if isinstance(srv, dict) and srv.get("running") is False:
            return None
        if isinstance(srv, dict) and srv.get("running") and isinstance(srv.get("version"), str):
            return {"version": srv["version"], "protocol": srv.get("protocol"),
                    "restart_needed": bool(srv.get("restart_needed") or srv.get("server_binary_stale"))}
    except subprocess.TimeoutExpired as e:
        raise UpdateError(f"the Herdr server timed out answering `status --json` "
                          f"({STATUS_TIMEOUT:g}s); is it stuck?") from e
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        pass
    try:
        snap = herdr.call("api", "snapshot", timeout=STATUS_TIMEOUT, binary=binary)["snapshot"]  # read-only
    except herdr.HerdrTimeout as e:
        raise UpdateError(f"the Herdr server timed out answering `api snapshot` "
                          f"({STATUS_TIMEOUT:g}s); is it stuck?") from e
    except (herdr.HerdrError, OSError):  # OSError: no `herdr` to run at all
        return None
    return {"version": snap.get("version"), "protocol": snap.get("protocol"), "restart_needed": False}


def _parse(what: str, v) -> tuple[int, int, int]:
    pv = herdr.parse_version(v)
    if pv is None:
        raise UpdateError(f"can't compare versions: {what} version is {v!r}")
    return pv


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def status(latest: dict, installed: str, server: str | None,
           server_protocol: int | None = None, restart_needed: bool = False) -> tuple[int, list[str]]:
    """(exit code, report lines). An available update outranks a pending restart."""
    lv, iv = _parse("latest", latest["version"]), _parse("installed", installed)
    lines: list[str] = []
    rc = UP_TO_DATE
    if lv > iv:
        rc = UPDATE_AVAILABLE
        line = f"Herdr {latest['version']} is available (installed: {installed})"
        proto = latest.get("protocol")
        if _is_int(proto) and _is_int(server_protocol) and proto != server_protocol:
            line += f"; protocol {server_protocol} -> {proto}"
        lines += [line, "  " + GATE_TEXT[herdr.gate(latest["version"])]]
    else:
        running = f", server running: {server}" if server else ""  # "no server" is said below
        lines.append(f"Herdr {installed} is up to date (latest: {latest['version']}{running})")
    if server is None:
        lines.append("no running Herdr server found")
    else:
        sv = herdr.parse_version(server)
        if sv is not None and sv < iv:
            lines.append(f"server is running {server}, older than the installed binary {installed}: "
                         f"restart pending")
            rc = rc or RESTART_PENDING
        elif restart_needed:
            lines.append(f"server is running {server}; Herdr reports its binary changed since it "
                         f"started: restart pending")
            rc = rc or RESTART_PENDING
        elif server != installed:
            lines.append(f"note: server is running {server}, installed binary is {installed}")
    return rc, lines
