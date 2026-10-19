"""`heelr check-update`, with the HTTP fetch and herdr calls mocked."""
import io
import http.client
import json
import subprocess
import urllib.error

import pytest

from heelr import herdr, update

LATEST = {"version": "0.9.3", "protocol": 22, "endpoint_generation": 1,
          "sha256": {"linux-x86_64": "ab" * 32}, "assets": {}}


@pytest.fixture(autouse=True)
def tested(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("_HEELR_SELFTEST_ALLOW_VERSION", raising=False)
    monkeypatch.setattr(herdr, "_PACKAGE_TESTED", tmp_path / "pkg.txt")
    (tmp_path / "pkg.txt").write_text("0.9.1\n0.9.3\n")


def opener_returning(body: bytes):
    def op(req, timeout):
        assert req.full_url == update.LATEST_URL and timeout > 0
        assert "urllib" not in (req.get_header("User-agent") or "urllib")  # herdr.dev 403s urllib's UA
        return io.BytesIO(body)
    return op


def test_fetch_latest_parses_json():
    assert update.fetch_latest(opener=opener_returning(json.dumps(LATEST).encode()))["version"] == "0.9.3"


@pytest.mark.parametrize("exc", [urllib.error.URLError("dns"), TimeoutError("slow"), OSError("reset")])
def test_fetch_latest_network_errors_are_clear(exc):
    def op(req, timeout):
        raise exc
    with pytest.raises(update.UpdateError, match="could not fetch https://herdr.dev/latest.json"):
        update.fetch_latest(opener=op)


@pytest.mark.parametrize("body", [b"<html>", b"[]", b'{"protocol": 22}', b'{"version": 3}'])
def test_fetch_latest_bad_payload(body):
    with pytest.raises(update.UpdateError):
        update.fetch_latest(opener=opener_returning(body))


def test_up_to_date():
    rc, lines = update.status(LATEST, installed="0.9.3", server="0.9.3")
    assert rc == update.UP_TO_DATE == 0
    assert "up to date" in lines[0]


def test_patch_update_available_and_gate_accepts_with_warning():
    rc, lines = update.status({**LATEST, "version": "0.9.4"}, installed="0.9.3", server="0.9.3")
    assert rc == update.UPDATE_AVAILABLE == 10
    text = "\n".join(lines)
    assert "0.9.4" in text and "warning" in text


def test_minor_update_available_and_gate_refuses():
    rc, lines = update.status({**LATEST, "version": "0.10.0", "protocol": 23}, installed="0.9.3", server="0.9.3",
                              server_protocol=22)
    assert rc == 10
    text = "\n".join(lines)
    assert "refuse" in text and "heelr selftest" in text
    assert "protocol 22 -> 23" in text


def test_update_to_already_tested_version():
    rc, lines = update.status(LATEST, installed="0.9.1", server="0.9.1")
    assert rc == 10 and "already tested" in "\n".join(lines)


def test_restart_pending():
    rc, lines = update.status(LATEST, installed="0.9.3", server="0.9.1")
    assert rc == update.RESTART_PENDING == 11
    assert "restart" in "\n".join(lines)


def test_update_available_wins_over_restart_pending_but_both_reported():
    rc, lines = update.status({**LATEST, "version": "0.9.4"}, installed="0.9.3", server="0.9.1")
    assert rc == 10
    assert "restart" in "\n".join(lines)


def test_no_server_running_is_not_an_error():
    rc, lines = update.status(LATEST, installed="0.9.3", server=None)
    assert rc == 0 and "no running Herdr server" in "\n".join(lines)


def test_unparseable_versions_are_errors():
    with pytest.raises(update.UpdateError):
        update.status({**LATEST, "version": "0.9.4-rc1"}, installed="0.9.3", server=None)
    with pytest.raises(update.UpdateError):
        update.status(LATEST, installed="weird", server=None)


def test_cli_check_update(monkeypatch, capsys, tmp_path, real_subprocess):
    from heelr import cli, selftest
    monkeypatch.setattr(update, "fetch_latest", lambda: {**LATEST, "version": "0.9.4"})
    monkeypatch.setattr(selftest, "resolve_bin", lambda arg: tmp_path / "herdr.bin")
    monkeypatch.setattr(selftest, "binary_version", lambda b: "0.9.3")
    calls = []

    def call(*a, **kw):  # only the read-only snapshot is allowed
        calls.append(a)
        return {"snapshot": {"version": "0.9.3", "protocol": 22}}
    monkeypatch.setattr(herdr, "call", call)
    assert cli.main(["check-update"]) == 10
    assert calls == [("api", "snapshot")]
    assert "0.9.4" in capsys.readouterr().out


def test_cli_check_update_network_error(monkeypatch, capsys):
    from heelr import cli

    def boom():
        raise update.UpdateError("could not fetch https://herdr.dev/latest.json: dns")
    monkeypatch.setattr(update, "fetch_latest", boom)
    monkeypatch.setattr(herdr, "call", lambda *a, **kw: pytest.fail("must not notify the server"))
    assert cli.main(["check-update"]) == 1
    assert "could not fetch" in capsys.readouterr().err


# --- the running server: `herdr status --json` first, snapshot as fallback ---

def status_json(running=True, version="0.9.3", protocol=22, restart_needed=False, stale=False):
    srv = {"status": "running" if running else "not_running", "running": running,
           "version": version if running else None, "protocol": protocol if running else None,
           "restart_needed": restart_needed, "server_binary_stale": stale}
    return json.dumps({"client": {"version": "0.9.4", "protocol": 23}, "server": srv,
                       "update": {"restart_needed": restart_needed, "server_binary_stale": stale}})


def fake_bin(tmp_path, stdout="", rc=0):
    p = tmp_path / "herdr.bin"
    (tmp_path / "status.out").write_text(stdout)
    p.write_text(f'#!/bin/sh\n[ "$1 $2" = "status --json" ] || exit 99\ncat "{tmp_path}/status.out"\nexit {rc}\n')
    p.chmod(0o755)
    return p


def no_snapshot(*a, **kw):
    raise AssertionError(f"must not fall back to herdr {a}")


def test_server_info_from_status_json(tmp_path, monkeypatch, real_subprocess):
    monkeypatch.setattr(herdr, "call", no_snapshot)
    b = fake_bin(tmp_path, status_json(version="0.9.1", protocol=21))
    assert update.server_info(b) == {"version": "0.9.1", "protocol": 21, "restart_needed": False}


@pytest.mark.parametrize("flags", [{"restart_needed": True}, {"stale": True}])
def test_server_info_restart_flags(tmp_path, monkeypatch, flags, real_subprocess):
    monkeypatch.setattr(herdr, "call", no_snapshot)
    b = fake_bin(tmp_path, status_json(**flags))
    assert update.server_info(b)["restart_needed"] is True


def test_server_info_not_running_is_none_without_fallback(tmp_path, monkeypatch, real_subprocess):
    monkeypatch.setattr(herdr, "call", no_snapshot)
    assert update.server_info(fake_bin(tmp_path, status_json(running=False))) is None


@pytest.mark.parametrize("out,rc", [("not json", 0), ('{"client": {}}', 0), (status_json(), 3),
                                    ('{"server": {"running": true, "version": null}}', 0)])
def test_server_info_falls_back_to_snapshot(tmp_path, monkeypatch, out, rc, real_subprocess):
    monkeypatch.setattr(herdr, "call", lambda *a, **kw: {"snapshot": {"version": "0.9.2", "protocol": 22}})
    assert update.server_info(fake_bin(tmp_path, out, rc)) == \
        {"version": "0.9.2", "protocol": 22, "restart_needed": False}


def test_snapshot_fallback_uses_the_given_binary(tmp_path, monkeypatch, real_subprocess):
    """--bin names the binary for the fallback too, not $HERDR_BIN_PATH or `herdr` on PATH."""
    monkeypatch.setenv("HERDR_BIN_PATH", "/live/herdr.bin")
    b = fake_bin(tmp_path, "not json")
    seen = []
    real_run = subprocess.run

    def run(cmd, **kw):
        if cmd[1:] == ["api", "snapshot"]:
            seen.append(cmd[0])
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": {"snapshot": {"version": "0.9.2"}}}), "")
        return real_run(cmd, **kw)
    monkeypatch.setattr(subprocess, "run", run)
    assert update.server_info(b)["version"] == "0.9.2"
    assert seen == [str(b)]


def test_server_info_missing_binaries_is_none_not_a_crash(tmp_path, monkeypatch, real_subprocess):
    """--bin given and no `herdr` on PATH: neither status nor the snapshot fallback can run."""
    monkeypatch.delenv("HERDR_BIN_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert update.server_info(tmp_path / "missing.bin") is None


def test_restart_needed_flag_is_restart_pending_at_same_version():
    rc, lines = update.status(LATEST, installed="0.9.3", server="0.9.3", restart_needed=True)
    assert rc == update.RESTART_PENDING
    assert "restart" in "\n".join(lines)


def test_cli_check_update_after_protocol_bump_reports_restart(monkeypatch, capsys, tmp_path, real_subprocess):
    """Installed 0.9.4 can't speak the old server's protocol, so a snapshot through it fails;
    `status --json` still names the running version, so the restart is reported (11), not 'no server' (0)."""
    from heelr import cli, selftest
    b = fake_bin(tmp_path, status_json(version="0.9.3", protocol=22, restart_needed=True))
    monkeypatch.setattr(update, "fetch_latest", lambda: {**LATEST, "version": "0.9.4", "protocol": 23})
    monkeypatch.setattr(selftest, "binary_version", lambda b: "0.9.4")

    def incompatible(*a, **kw):
        raise herdr.HerdrError("protocol mismatch")
    monkeypatch.setattr(herdr, "call", incompatible)
    assert cli.main(["check-update", "--bin", str(b)]) == update.RESTART_PENDING
    out = capsys.readouterr().out
    assert "restart" in out and "no running Herdr server" not in out


def test_cli_check_update_bin_without_herdr_on_path(monkeypatch, capsys, tmp_path, real_subprocess):
    from heelr import cli, selftest
    b = fake_bin(tmp_path, "garbage", rc=1)
    monkeypatch.delenv("HERDR_BIN_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(update, "fetch_latest", lambda: LATEST)
    monkeypatch.setattr(selftest, "binary_version", lambda b: "0.9.3")
    assert cli.main(["check-update", "--bin", str(b)]) == 0
    assert "no running Herdr server" in capsys.readouterr().out


# --- review C1: a stuck server must not hang check-update ---

def test_server_info_timeout_is_an_error_without_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(herdr, "call", no_snapshot)

    def stuck(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(update.subprocess, "run", stuck)
    with pytest.raises(update.UpdateError, match="timed out"):
        update.server_info(tmp_path / "herdr.bin")


def test_snapshot_fallback_has_a_timeout(tmp_path, monkeypatch):
    """When status --json is merely unreadable, the fallback snapshot call is bounded too."""
    seen = {}

    def fake_run(cmd, **kw):
        seen["timeout"] = kw.get("timeout")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(herdr.subprocess, "run", fake_run)
    monkeypatch.setenv("HERDR_BIN_PATH", "/nonexistent/herdr")
    real_run = update.subprocess.run

    def status_garbage(cmd, **kw):
        if cmd[1:] == ["status", "--json"]:
            return subprocess.CompletedProcess(cmd, 0, "not json", "")
        return real_run(cmd, **kw)
    monkeypatch.setattr(update.subprocess, "run", status_garbage)
    with pytest.raises(update.UpdateError, match="timed out"):
        update.server_info(tmp_path / "herdr.bin")
    assert seen["timeout"]


def test_cli_check_update_stuck_server_exits_1(monkeypatch, capsys, tmp_path):
    from heelr import cli, selftest
    monkeypatch.setattr(update, "fetch_latest", lambda: LATEST)
    monkeypatch.setattr(selftest, "resolve_bin", lambda a: tmp_path / "herdr.bin")
    monkeypatch.setattr(selftest, "binary_version", lambda b: "0.9.3")
    monkeypatch.setattr(herdr, "call", no_snapshot)

    def stuck(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(update.subprocess, "run", stuck)
    assert cli.main(["check-update"]) == update.ERROR
    assert "timed out" in capsys.readouterr().err


# --- review C2: HTTP protocol errors and a hung --bin are UpdateErrors, not tracebacks ---

@pytest.mark.parametrize("exc", [http.client.IncompleteRead(b"x"), http.client.BadStatusLine("junk")])
def test_fetch_latest_http_client_errors_are_clear(exc):
    def op(req, timeout):
        raise exc
    with pytest.raises(update.UpdateError, match="could not fetch"):
        update.fetch_latest(opener=op)


def test_cli_check_update_hung_bin_exits_1(monkeypatch, capsys, tmp_path):
    from heelr import cli, selftest
    b = tmp_path / "herdr.bin"
    b.write_text("#!/bin/sh\n")
    b.chmod(0o755)
    monkeypatch.setattr(update, "fetch_latest", lambda: LATEST)

    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(selftest.subprocess, "run", run)
    assert cli.main(["check-update", "--bin", str(b)]) == update.ERROR
    assert "timed out" in capsys.readouterr().err


# --- review minors: "no server" once; protocol only when both are integers ---

def test_no_server_is_reported_once():
    rc, lines = update.status(LATEST, installed="0.9.3", server=None)
    assert rc == update.UP_TO_DATE
    assert sum("server" in ln and ("no running" in ln or "none" in ln) for ln in lines) == 1


@pytest.mark.parametrize("bad", ["22; rm -rf", "x\x1b[31m", 22.5, True, None, [22]])
def test_protocol_shown_only_when_both_are_integers(bad):
    _, lines = update.status({**LATEST, "version": "0.9.4"}, "0.9.3", "0.9.3", server_protocol=bad)
    assert "protocol" not in lines[0]
    _, lines = update.status({**LATEST, "version": "0.9.4", "protocol": bad}, "0.9.3", "0.9.3", server_protocol=22)
    assert "protocol" not in lines[0]


def test_protocol_change_still_shown():
    _, lines = update.status({**LATEST, "version": "0.9.4", "protocol": 23}, "0.9.3", "0.9.3", server_protocol=22)
    assert "protocol 22 -> 23" in lines[0]
