import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from heelr import cli

ROOT = Path(__file__).resolve().parent.parent
NASTY = 'we"ird\\na me'          # a double quote and a backslash in the folder name


@pytest.mark.parametrize("sh", ["bash", "zsh", "fish"])
def test_shell_hook_prints_snippet(sh, capsys):
    assert cli.main(["shell-hook", sh]) == 0
    out = capsys.readouterr().out
    assert "heelr report" in out and "HERDR_PANE_ID" in out


@pytest.mark.parametrize("sh", ["bash", "zsh", "fish"])
def test_hooks_are_silent_background_and_escape(sh, capsys):
    cli.main(["shell-hook", sh])
    out = capsys.readouterr().out
    assert ">/dev/null 2>&1 &" in out           # background, silent
    assert "$PWD" in out and "HERDR_PANE_ID" in out
    assert "\\\\" in out and '\\"' in out        # escapes backslash and double quote


def test_bash_hook_appends_never_replaces(capsys):
    cli.main(["shell-hook", "bash"])
    out = capsys.readouterr().out
    assert """PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND$'\\n'}__heelr_hook\"""" in out
    assert "$PROMPT_COMMAND;}" not in out
    assert "PROMPT_COMMAND=__heelr_hook" not in out


def test_zsh_hook_uses_add_zsh_hook_never_overwrites(capsys):
    cli.main(["shell-hook", "zsh"])
    out = capsys.readouterr().out
    assert "add-zsh-hook chpwd __heelr_hook" in out
    assert "precmd()" not in out and "precmd_functions=" not in out


def test_fish_hook_is_an_event_handler(capsys):
    cli.main(["shell-hook", "fish"])
    assert "--on-variable PWD" in capsys.readouterr().out


def test_example_json_parses():
    for f in ("claude-hooks.json", "codex-hooks.json"):
        json.loads((ROOT / "examples/adapters" / f).read_text())


def test_claude_example_has_no_worktree_create():
    assert "WorktreeCreate" not in json.loads((ROOT / "examples/adapters/claude-hooks.json").read_text())


def test_example_files_exist():
    for f in ("claude-statusline.md", "opencode-heelr.js"):
        assert (ROOT / "examples/adapters" / f).is_file()


# --- run the snippet in a real shell against a fake `heelr` on PATH -------------------------------
# (a local, known-safe program: real_subprocess opts out of the spawn guard for these only)

def _fake_heelr(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sink = tmp_path / "sink"
    f = bindir / "heelr"
    f.write_text('#!/bin/sh\necho "$@" > "$SINK.args"\ncat > "$SINK.tmp" && mv "$SINK.tmp" "$SINK.json"\n'
                 'echo x >> "$SINK.count"\n')
    f.chmod(0o755)
    return bindir, sink


def _snippet(sh, capsys):
    cli.main(["shell-hook", sh])
    return capsys.readouterr().out


def _env(tmp_path, bindir, sink, pane="p1"):
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "SINK": str(sink), "HOME": str(tmp_path)}
    if pane:
        env["HERDR_PANE_ID"] = pane
    return env


def _wait(path, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if path.exists():
            time.sleep(0.2)
            return True
        time.sleep(0.02)
    return False


def test_bash_hook_json_valid_with_quote_and_backslash(tmp_path, capsys, real_subprocess):
    bindir, sink = _fake_heelr(tmp_path)
    d = tmp_path / NASTY
    d.mkdir()
    script = _snippet("bash", capsys) + f'cd "$1"\n__heelr_hook\n'
    r = subprocess.run(["bash", "-c", script, "bash", str(d)], env=_env(tmp_path, bindir, sink),
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert _wait(Path(f"{sink}.json"))
    assert json.loads(Path(f"{sink}.json").read_text()) == {"cwd": str(d)}
    assert Path(f"{sink}.args").read_text().split() == ["report", "--pane", "p1"]


def test_bash_hook_silent_without_pane_and_only_on_change(tmp_path, capsys, real_subprocess):
    bindir, sink = _fake_heelr(tmp_path)
    snippet = _snippet("bash", capsys)
    r = subprocess.run(["bash", "-c", snippet + "__heelr_hook\n"], env=_env(tmp_path, bindir, sink, pane=None),
                       capture_output=True, text=True, timeout=20)
    time.sleep(0.3)
    assert r.returncode == 0 and not Path(f"{sink}.json").exists()
    r = subprocess.run(["bash", "-c", snippet + "__heelr_hook; __heelr_hook; __heelr_hook\n"],
                       env=_env(tmp_path, bindir, sink), capture_output=True, text=True, timeout=20)
    assert _wait(Path(f"{sink}.count"))
    assert Path(f"{sink}.count").read_text().count("x") == 1     # same $PWD: reported once


def _bash_version():
    out = subprocess.run(["bash", "-c", 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'],
                         capture_output=True, text=True).stdout.split()
    return tuple(int(x) for x in out)


def test_bash_prompt_command_scalar_and_array_are_kept(tmp_path, capsys, real_subprocess):
    snippet = _snippet("bash", capsys)
    r = subprocess.run(["bash", "-c", 'PROMPT_COMMAND="echo one"\n' + snippet + snippet +
                        'printf "%s" "$PROMPT_COMMAND"'], capture_output=True, text=True, timeout=20)
    assert r.stdout == "echo one\n__heelr_hook"                  # appended once, idempotent
    if _bash_version() >= (5, 1):
        r = subprocess.run(["bash", "-c", 'PROMPT_COMMAND=("echo one" "echo two")\n' + snippet + snippet +
                            'printf "%s|" "${PROMPT_COMMAND[@]}"'], capture_output=True, text=True, timeout=20)
        assert r.stdout == "echo one|echo two|__heelr_hook|"    # array: element appended, others kept


@pytest.mark.parametrize("sh", ["zsh", "fish"])
def test_other_shell_json_valid_when_installed(sh, tmp_path, capsys, real_subprocess):
    if not shutil.which(sh):
        pytest.skip(f"{sh} not installed")
    bindir, sink = _fake_heelr(tmp_path)
    d = tmp_path / NASTY
    d.mkdir()
    script = _snippet(sh, capsys) + ('cd $argv[1]\n' if sh == "fish" else 'cd "$1"\n')
    cmd = ["fish", "-c", script, str(d)] if sh == "fish" else ["zsh", "-c", script, "zsh", str(d)]
    subprocess.run(cmd, env={**_env(tmp_path, bindir, sink), "PATH": f"{bindir}:/usr/bin:/bin"},
                   capture_output=True, text=True, timeout=20)
    assert _wait(Path(f"{sink}.json"))
    assert json.loads(Path(f"{sink}.json").read_text()) == {"cwd": str(d)}


# --- fix round 1 -----------------------------------------------------------------------------------

def _last_line(sh, capsys):
    return [ln.split("   #")[0].strip() for ln in _snippet(sh, capsys).splitlines() if ln.strip()][-1]


def test_zsh_and_fish_report_the_starting_folder_once_at_load(capsys):
    assert _last_line("zsh", capsys) == "__heelr_hook"
    assert _last_line("fish", capsys) == "__heelr_report"


def test_headers_are_per_shell(capsys):
    assert "AFTER any line that sets PROMPT_COMMAND" in _snippet("bash", capsys)
    assert "AFTER" not in _snippet("zsh", capsys) and "AFTER" not in _snippet("fish", capsys)
    assert "heelr shell-hook fish | source" in _snippet("fish", capsys)


@pytest.mark.parametrize("name,value", [
    ("trailing semicolon", "echo orig >> $LOG;"),
    ("background job", "echo orig >> $LOG &"),
    ("trailing newline", "echo orig >> $LOG\n"),
    ("trailing comment", "echo orig >> $LOG # a comment"),
])
def test_bash_scalar_prompt_command_shapes_still_run_both(name, value, tmp_path, capsys, real_subprocess):
    bindir, sink = _fake_heelr(tmp_path)
    log = tmp_path / "log"
    snippet = _snippet("bash", capsys)
    script = f'PROMPT_COMMAND=$\'{value}\'\n{snippet}{snippet}eval "$PROMPT_COMMAND"\n'
    r = subprocess.run(["bash", "-c", script], env={**_env(tmp_path, bindir, sink), "LOG": str(log)},
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and r.stderr == "", r.stderr
    assert _wait(log) and log.read_text() == "orig\n"                  # original command ran
    assert _wait(Path(f"{sink}.json"))                                 # and so did the hook
    assert Path(f"{sink}.count").read_text().count("x") == 1           # re-eval did not add it twice


def test_bash_hook_preserves_dollar_question(tmp_path, capsys, real_subprocess):
    bindir, sink = _fake_heelr(tmp_path)
    snippet = _snippet("bash", capsys)
    for env_pane, pre in ((True, ""), (False, ""), (True, "__heelr_hook;")):   # reports / no pane / unchanged
        env = _env(tmp_path, bindir, sink, pane="p1" if env_pane else None)
        r = subprocess.run(["bash", "-c", snippet + pre + "false; __heelr_hook; echo rc=$?"], env=env,
                           capture_output=True, text=True, timeout=20)
        assert r.stdout == "rc=1\n", (env_pane, pre, r.stdout)


@pytest.mark.parametrize("sh", ["zsh", "fish"])
def test_other_shell_reports_starting_folder_when_installed(sh, tmp_path, capsys, real_subprocess):
    if not shutil.which(sh):
        pytest.skip(f"{sh} not installed")
    bindir, sink = _fake_heelr(tmp_path)
    d = tmp_path / NASTY
    d.mkdir()
    cmd = [sh, "-c", _snippet(sh, capsys)]
    subprocess.run(cmd, cwd=d, env={**_env(tmp_path, bindir, sink), "PWD": str(d)},
                   capture_output=True, text=True, timeout=20)
    assert _wait(Path(f"{sink}.json"))
    assert json.loads(Path(f"{sink}.json").read_text()) == {"cwd": str(d)}
