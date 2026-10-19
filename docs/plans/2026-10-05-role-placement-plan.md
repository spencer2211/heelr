# Role Placement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Place every Herdr pane in the workspace and tab its (repo, task) belongs to, shape each tab by role, and mark (not move) panes that drift, as specified in the role placement design.

**Architecture:** Five small stdlib modules: `facts` (git facts from files, per-pane reports and tags), `key` (config, task chain, ticket regex, classify script), `place` (planner, executor, undo), `burst` (shared pane.created window), and CLI commands in `cli.py`. Placement runs inside `heelr auto` under `core.apply_lock`, which also takes the shared `<socket>.layout.lock`. Drift is computed on demand by a read-only `heelr drift-status` for Herdr's `tab_bar_right`.

**Tech Stack:** Python 3.11+ stdlib only (`tomllib`, `fcntl`, `subprocess`, `hashlib`), pytest, the in-memory `tests/fakeherdr.py`, `heelr selftest` on a throwaway Herdr 0.9.3.

**Spec:** `docs/plans/2026-10-05-role-placement-design.md` (approved at f755698) and `docs/spikes/2026-10-05-role-placement-spikes.md`.

## Global Constraints

- Stdlib only; no new dependencies.
- Herdr 0.9.3 API; the existing version gate applies (`herdr.check_version`).
- Placement is **off** unless `~/.config/heelr/placement.toml` has `enabled = true`; with it off, `heelr auto` behaves exactly as before.
- Panes are never closed or killed by placement, sort or pull. (The one exception: the initial shell of a workspace heelr itself just created, if still idle.)
- The focused pane never moves on its own; every heelr move passes `--no-focus`, except a single just-created pane, which moves with `--focus`.
- Tab labels are never changed after creation; heelr renames, removes or closes only tabs it created and recorded.
- No dependence on hook order across plugins.
- Sleeping tabs (`💤 ` label) and panes in a sleep record are never placed, sorted, pulled or marked; nothing is placed into a sleeping tab.
- Drift = the pane's (repo, task) maps to a different workspace/tab label than the one it is in. Role never causes drift.
- `heelr drift-status` is read-only: no `apply_lock`, no state writes except the classify cache, never fails (prints nothing on error).
- Terms: server → workspace → tab → pane; never "session".
- No personal names or private paths in tracked files (tests/test_privacy.py enforces).
- Version: this work ships as `0.2.0`; tags `vX.Y.Z`, version strings without `v`.
- Tests never reach a real process (tests/conftest.py guard); `git` facts are read from files, not by running git.
- Tests never touch the live layout lock: an autouse conftest fixture points `HERDR_SOCKET_PATH` into `tmp_path` (Task 3).
- Background placement (from `heelr auto`) never writes the undo record and never calls `set_last`; only `sort` and `pull` (commands you run) do, so `heelr back` always undoes the last thing *you* asked for.
- Hook budget: a `pane.created` hook spends `burst_window` (0.75 s) + placement (about 0.9 s total with the window, measured) + auto-tile. Herdr's plugin hook timeout is above 70 s or absent; see "Hook budget (measured)" in docs/spikes/2026-10-05-role-placement-spikes.md.

## Review Focus

1. **Submodules look like worktrees.** A submodule's `.git` file points into `<super>/.git/modules/<name>`, not `.git/worktrees/<name>`; it must be read as its own repo, not as a worktree of the superproject. Test in Task 3.
2. **A user-made workspace or tab already has the label heelr wants.** heelr must use it as the destination (exact label match), never rename it, and never create a second one with the same label. Test in Task 5.
3. **A cross-workspace move renumbers the pane mid-plan.** Later steps in the same plan (moves, layout, undo record, created-panes list, reports, tags) must use the new id. Test in Task 5.
4. **The classify script hangs, prints megabytes, or prints a JSON list.** Placement must fall back to rules within ~1 s and not crash the hook. Test in Task 4.
5. **A detached HEAD or a pane folder that was deleted.** Task becomes the worktree name or `unsorted`; no exception. Test in Task 3.

## Rulings against the spec (made while planning; reviewer-checked)

- Git facts are read from `.git` files (no git process), so the spec's 10 s git cache is dropped.
- Drift is computed on demand by `heelr drift-status`, not stored.
- A workspace heelr creates starts with a shell; heelr closes it if it is still idle.
- Burst rule: a new pane is "single" only if no other `pane.created` happened in the **previous 2 s** (`burst_lookback`) and none in the next `burst_window` (0.75 s). Anything else is part of a burst and moves without focus. This replaces the spec's ">3 events in 2 s", which let the first panes of a restore jump focus.
- Bare repositories with worktrees: `root` is the bare repo's folder, so `repo` is its name (e.g. `heelr.git`); accepted for 0.2.0, `.heelr.toml` `name` overrides it.
- The spec's "ignore events raised while heelr holds `apply_lock`" is implemented as the `own_panes()` re-check under `apply_lock`. It is race-free because every heelr pane creator notes the pane while holding the lock (`core.run`'s `note()`, `place.execute`'s new-workspace shell), and the hook's re-check can only run after the creator releases. A time-window rule was rejected: it skipped user panes created just after any auto-tile run, and stuck on if a lock holder was killed.
- Placement keys (and classify) are computed before taking the lock, so a slow classify never blocks other layout plugins.
- `sleep` and `wake` keep heelr's `apply.lock` but do **not** take the shared layout lock: they don't reshape layouts, and sleep waits up to 10 s for agents to exit, which would block other layout plugins.

---

## File structure

| File | Responsibility |
|---|---|
| `heelr/__init__.py` | `__version__` |
| `heelr/facts.py` (new) | `GitFacts` from the filesystem; per-pane store for reports and tags; `rekey()` across all heelr state |
| `heelr/key.py` (new) | `PlacementConfig`, repo config, ticket extraction, task chain, classify script runner + cache, `Key` |
| `heelr/place.py` (new) | `Dest` per pane, label qualifiers, planner, executor (moves, tab/workspace creation, re-keying), place undo record, back |
| `heelr/burst.py` (new) | shared record of recent `pane.created` events under a lock; single vs burst decision |
| `heelr/core.py` | `apply_lock` also takes `<HERDR_SOCKET_PATH>.layout.lock` |
| `heelr/cli.py` | `--version`, `report`, `tag`, `sort`, `pull`, `drift-status`, `shell-hook`; `auto` runs placement first; `back` handles `place` |
| `heelr/sleep.py` | `sleeping_panes(snap)` helper |
| `tests/fakeherdr.py` | workspaces, pane cwd, `tab create`, `workspace create`, cross-workspace id change |
| `tests/test_placement_*.py` (new) | per-module tests |
| `examples/placement.toml`, `examples/adapters/*` (new) | config and adapter examples |
| `docs/USER_GUIDE.md`, `README.md`, `CHANGELOG.md` | docs |

---

### Task 1: Version 0.1.0 in code, `--version`, CHANGELOG

**Files:**
- Modify: `heelr/__init__.py`, `heelr/cli.py` (in `main`)
- Create: `CHANGELOG.md`, `tests/test_version.py`

**Interfaces:**
- Produces: `heelr.__version__: str`; `heelr --version` prints `heelr <version>`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_version.py
import re
import tomllib
from pathlib import Path

import heelr
from heelr import cli

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_semver_without_v():
    assert re.fullmatch(r"\d+\.\d+\.\d+", heelr.__version__)


def test_cli_prints_version(capsys):
    try:
        cli.main(["--version"])
    except SystemExit as e:
        assert e.code == 0
    assert capsys.readouterr().out.strip() == f"heelr {heelr.__version__}"


def test_plugin_manifest_matches():
    m = tomllib.loads((ROOT / "plugins/tab-colours/herdr-plugin.toml").read_text())
    assert m["version"] == heelr.__version__


def test_changelog_has_this_version():
    assert f"## {heelr.__version__}" in (ROOT / "CHANGELOG.md").read_text()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 60 python -m pytest tests/test_version.py -q`
Expected: FAIL (`AttributeError: module 'heelr' has no attribute '__version__'`).

- [ ] **Step 3: Implement**

`heelr/__init__.py` (replace its one line, keeping the existing docstring if any):

```python
__version__ = "0.1.0"
```

In `cli.main`, after creating `ap`:

```python
    from . import __version__
    ap.add_argument("--version", action="version", version=f"heelr {__version__}")
```

`CHANGELOG.md`:

```markdown
# Changelog

Versions follow [semver](https://semver.org). Before 1.0, a breaking config or CLI change bumps the
minor version; features and fixes bump the patch. Tags are `vX.Y.Z`.

## 0.1.0

Baseline: layouts (apply, back, save, presets, picker), promote, nudge, multi-tab layouts, auto-tile,
tab sleep/wake, the tab-colours plugin, selftest, check-update.
```

- [ ] **Step 4: Run the tests**

Run: `timeout 300 python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add heelr/__init__.py heelr/cli.py CHANGELOG.md tests/test_version.py
git commit -m "version: __version__ 0.1.0, heelr --version, CHANGELOG"
```

---

### Task 2: Fake Herdr learns workspaces, cwd, tab/workspace create, cross-workspace ids

**Files:**
- Modify: `tests/fakeherdr.py`
- Create: `tests/test_fakeherdr_placement.py`

**Interfaces:**
- Produces (fake only): `FakeHerdr.workspaces: dict[str, str]` (ws_id → label); `FakeHerdr.cwd: dict[str, str]` (pane → folder); `add_workspace(label) -> ws_id`; `add_tab(..., cwd=None)`; requests `workspace create --label L --cwd D --no-focus` → `{"workspace": {"workspace_id", "label"}, "pane": {"pane_id"}}`; `tab create --workspace W --label L --cwd D --no-focus` → `{"tab": {"tab_id","label","workspace_id"}, "pane": {"pane_id"}}`; a `pane move` whose destination is in another workspace gives the pane a new id `<ws>:p<n>` and returns it in `move_result.pane.pane_id` (as Herdr 0.9.3 does); snapshot gains `workspaces` and per-pane `cwd`/`foreground_cwd`.

(The real reply shapes of `workspace create` and `tab create` are checked live in Task 10; if they differ, the ruling updates both the fake and `place.py`.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fakeherdr_placement.py
from fakeherdr import FakeHerdr


def test_snapshot_lists_workspaces_and_cwd():
    f = FakeHerdr()
    w = f.add_workspace("heelr")
    tab, (p,) = f.add_tab(w, "main", 1, cwd="/r/heelr")
    snap = f.snapshot()
    assert {"workspace_id": w, "label": "heelr"} in [
        {k: x[k] for k in ("workspace_id", "label")} for x in snap["workspaces"]]
    pane = next(x for x in snap["panes"] if x["pane_id"] == p)
    assert pane["cwd"] == pane["foreground_cwd"] == "/r/heelr"


def test_cross_workspace_move_renumbers_the_pane():
    f = FakeHerdr()
    a, b = f.add_workspace("a"), f.add_workspace("b")
    ta, (p1, p2) = f.add_tab(a, "x", 2)
    tb, (q,) = f.add_tab(b, "y", 1)
    r = f.call("pane", "move", p2, "--tab", tb, "--split", "right", "--target-pane", q, "--no-focus")
    new = r["move_result"]["pane"]["pane_id"]
    assert new != p2 and new.startswith(b + ":") and new in f.panes(tb)


def test_same_workspace_move_keeps_the_id():
    f = FakeHerdr()
    a = f.add_workspace("a")
    t1, (p1, p2) = f.add_tab(a, "x", 2)
    t2, (q,) = f.add_tab(a, "y", 1)
    r = f.call("pane", "move", p2, "--tab", t2, "--split", "right", "--target-pane", q, "--no-focus")
    assert r["move_result"]["pane"]["pane_id"] == p2


def test_tab_and_workspace_create():
    f = FakeHerdr()
    a = f.add_workspace("a")
    f.add_tab(a, "x", 1)
    r = f.call("tab", "create", "--workspace", a, "--label", "GH-1", "--cwd", "/r", "--no-focus")
    assert r["tab"]["label"] == "GH-1" and r["pane"]["pane_id"] in f.panes(r["tab"]["tab_id"])
    w = f.call("workspace", "create", "--label", "repo2", "--cwd", "/r2", "--no-focus")
    assert f.workspaces[w["workspace"]["workspace_id"]] == "repo2"
    assert f.cwd[w["pane"]["pane_id"]] == "/r2"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 60 python -m pytest tests/test_fakeherdr_placement.py -q`
Expected: FAIL (`AttributeError: 'FakeHerdr' object has no attribute 'add_workspace'`).

- [ ] **Step 3: Implement in `tests/fakeherdr.py`**

In `__init__` add:

```python
        self.workspaces: dict[str, str] = {}   # ws_id -> label
        self.cwd: dict[str, str] = {}          # pane -> folder
        self.n_ws = 0
```

Add methods:

```python
    def add_workspace(self, label: str) -> str:
        self.n_ws += 1
        ws = f"w{self.n_ws}"
        self.workspaces[ws] = label
        return ws
```

Change `add_tab` signature to `add_tab(self, ws, label, n_panes=1, d="right", agents=(), cwd=None)`; register `ws` if unknown (`self.workspaces.setdefault(ws, ws)`) and set `self.cwd[pid] = cwd or "/"` for each new pane.

In `snapshot()`, add to each pane dict `"cwd": self.cwd.get(p, "/"), "foreground_cwd": self.cwd.get(p, "/")`, and add a top-level `"workspaces": [{"workspace_id": w, "label": l, "number": i + 1} for i, (w, l) in enumerate(self.workspaces.items())]`. Register any `ws` seen in `self.tabs` that is not in `self.workspaces` first.

In `call`, before the final `raise AssertionError`:

```python
        if args[:2] == ("tab", "create"):
            ws = self._opt(args, "--workspace", self.tabs[self.focused_tab]["ws"])
            tid, (pid,) = self.add_tab(ws, self._opt(args, "--label", ""), 1,
                                       cwd=self._opt(args, "--cwd"))
            return {"tab": {"tab_id": tid, "label": self.tabs[tid]["label"], "workspace_id": ws},
                    "pane": {"pane_id": pid}}
        if args[:2] == ("workspace", "create"):
            ws = self.add_workspace(self._opt(args, "--label", ""))
            tid, (pid,) = self.add_tab(ws, "1", 1, cwd=self._opt(args, "--cwd"))
            return {"workspace": {"workspace_id": ws, "label": self.workspaces[ws]},
                    "pane": {"pane_id": pid}}
```

In `_move`, after computing the destination workspace (`ws` for `--new-tab`, `self.tabs[tab]["ws"]` otherwise) and before inserting: if it differs from `self.tabs[src]["ws"]`, rename the pane:

```python
    def _renumber(self, pane: str, ws: str) -> str:
        new = self._new_pane(ws)
        for d in (self.agents, self.cwd, self.procs):
            if pane in d:
                d[new] = d.pop(pane)
        if self.focused_pane == pane:
            self.focused_pane = new
        return new
```

Call it as `pane = self._renumber(pane, dest_ws)` after `self._take(pane)` and use `pane` in the insert and in the reply. `_new_pane` must not overwrite `self.procs[new]` when renumbering: move the `procs` default into `add_tab`.

- [ ] **Step 4: Run all tests** (the fake is shared; nothing else may break)

Run: `timeout 300 python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/fakeherdr.py tests/test_fakeherdr_placement.py
git commit -m "test fake: workspaces, pane cwd, tab/workspace create, cross-workspace pane ids"
```

---

### Task 3: Shared layout lock + git facts + pane store + rekey

**Files:**
- Modify: `heelr/core.py:36-63` (`apply_lock`)
- Create: `heelr/facts.py`, `tests/test_placement_facts.py`
- Modify: `tests/test_apply_safety.py` (lock tests), `tests/conftest.py` (isolate the layout lock), `heelr/sleep.py` (`apply_lock(layout=False)`)

**Interfaces:**
- Produces:
  - `core.layout_lock_path() -> Path | None` — `Path(os.environ["HERDR_SOCKET_PATH"] + ".layout.lock")`, else `~/.config/herdr/herdr.sock.layout.lock`; `None` if its folder does not exist.
  - `core.apply_lock(wait=None, layout=True)` takes `apply.lock`, then (if `layout`) the shared layout lock, both with one deadline; on timeout of either it raises `herdr.HerdrError("another layout change is running")`; both released on every exit path. Re-entrancy unchanged.
  - `sleep.do_sleep` / `sleep.do_wake` call `core.apply_lock(layout=False)`.
  - `facts.GitFacts(root: str | None, repo: str | None, worktree: str | None, branch: str | None, default_branch: str | None)` (frozen dataclass).
  - `facts.git_facts(folder: str) -> GitFacts`
  - `facts.load_pane(pane_id) -> dict` (keys `report`, `report_at`, `tag`); `facts.save_report(pane_id, data: dict)`; `facts.set_tag(pane_id, task: str | None)`; `facts.forget(pane_id)`.
  - `facts.rekey(old: str, new: str) -> int` — replaces the exact string `old` with `new` in every JSON file under `core.state_dir()` (recursively), and renames `panes/<old>.json`; returns files changed. Sleep records are left alone (sleeping panes are never moved).

This is carry-over 4(c): the layout lock now also applies to today's `apply`, `auto`, `back`, `promote`, `nudge`, `save --tabs` (each holds it for one snapshot-plan-execute pass, typically well under 1 s). `sleep` and `wake` take only `apply.lock` (see Rulings).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_placement_facts.py
import json
import os
from pathlib import Path

import pytest

from heelr import core, facts, herdr


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    return tmp_path


def make_repo(root: Path, branch="main", origin_head="main"):
    g = root / ".git"
    (g / "refs/heads").mkdir(parents=True)
    (g / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    if origin_head:
        (g / "refs/remotes/origin").mkdir(parents=True)
        (g / "refs/remotes/origin/HEAD").write_text(f"ref: refs/remotes/origin/{origin_head}\n")
    return root


def make_worktree(repo: Path, name: str, branch: str) -> Path:
    wt = repo / ".worktrees" / name
    wt.mkdir(parents=True)
    gd = repo / ".git/worktrees" / name
    gd.mkdir(parents=True)
    (gd / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    (gd / "commondir").write_text("../..\n")
    (wt / ".git").write_text(f"gitdir: {gd}\n")
    return wt


def test_main_checkout(state):
    r = make_repo(state / "heelr")
    (r / "sub").mkdir()
    f = facts.git_facts(str(r / "sub"))
    assert (f.repo, f.worktree, f.branch, f.default_branch) == ("heelr", None, "main", "main")
    assert f.root == str(r)


def test_worktree_maps_to_its_main_repo(state):
    r = make_repo(state / "heelr")
    wt = make_worktree(r, "GH-330", "fix/GH-330-crash")
    f = facts.git_facts(str(wt))
    assert (f.repo, f.worktree, f.branch) == ("heelr", "GH-330", "fix/GH-330-crash")
    assert f.root == str(r)


def test_submodule_is_its_own_repo_not_a_worktree(state):          # Review Focus 1
    sup = make_repo(state / "super")
    mod = sup / "lib"
    mod.mkdir()
    md = sup / ".git/modules/lib"
    md.mkdir(parents=True)
    (md / "HEAD").write_text("ref: refs/heads/main\n")
    (mod / ".git").write_text("gitdir: ../.git/modules/lib\n")
    f = facts.git_facts(str(mod))
    assert (f.repo, f.worktree, f.root) == ("lib", None, str(mod))


def test_detached_head_and_missing_folder(state):                  # Review Focus 5
    r = make_repo(state / "heelr")
    (r / ".git/HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
    assert facts.git_facts(str(r)).branch is None
    gone = facts.git_facts(str(state / "deleted/folder"))
    assert gone == facts.GitFacts(None, None, None, None, None)


def test_packed_refs_match_whole_branch(state):
    r = make_repo(state / "heelr", origin_head=None)
    (r / ".git/packed-refs").write_text("abc refs/heads/maintenance\n")
    assert facts.git_facts(str(r)).default_branch is None


def test_not_a_repo(state):
    (state / "plain").mkdir()
    assert facts.git_facts(str(state / "plain")).repo is None


def test_report_tag_and_rekey(state):
    facts.save_report("w1:p2", {"cwd": "/x", "junk": 1})
    facts.set_tag("w1:p2", "GH-9")
    core.write_atomic(core.state_dir() / "back" / "t.json", json.dumps({"created": [{"pane": "w1:p2"}]}))
    assert facts.rekey("w1:p2", "w2:p7") >= 2
    p = facts.load_pane("w2:p7")
    assert p["report"]["cwd"] == "/x" and p["tag"] == "GH-9"
    assert facts.load_pane("w1:p2") == {}
    assert "w2:p7" in (core.state_dir() / "back" / "t.json").read_text()


def test_rekey_does_not_touch_prefix_matches(state):
    core.write_atomic(core.state_dir() / "x.json", json.dumps({"a": "w1:p2", "b": "w1:p21"}))
    facts.rekey("w1:p2", "w2:p1")
    assert json.loads((core.state_dir() / "x.json").read_text()) == {"a": "w2:p1", "b": "w1:p21"}
```

Add to `tests/conftest.py` (autouse, so every existing test is covered):

```python
@pytest.fixture(autouse=True)
def isolated_layout_lock(tmp_path, monkeypatch):
    """apply_lock takes <HERDR_SOCKET_PATH>.layout.lock; never the live one."""
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
```

Append to `tests/test_apply_safety.py`:

```python
def test_layout_lock_is_never_the_live_one(tmp_path):
    p = core.layout_lock_path()
    assert p is not None and str(p).startswith(str(tmp_path))


def test_sleep_takes_no_layout_lock(tmp_path):
    import fcntl
    with core.apply_lock(layout=False):
        with open(core.layout_lock_path(), "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)        # free

```

Also append:

```python
def test_apply_lock_also_takes_the_shared_layout_lock(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    with core.apply_lock():
        with open(tmp_path / "herdr.sock.layout.lock", "a") as f:
            with pytest.raises(BlockingIOError):
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(tmp_path / "herdr.sock.layout.lock", "a") as f:   # released afterwards
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_apply_lock_times_out_on_a_held_layout_lock_and_releases_apply_lock(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    with open(tmp_path / "herdr.sock.layout.lock", "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)                        # another plugin holds it
        with pytest.raises(herdr.HerdrError, match="another layout change"):
            with core.apply_lock(wait=0.2):
                pass
        assert not lock_held()                                   # apply.lock was released


def test_apply_lock_releases_both_on_error(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    with pytest.raises(ValueError):
        with core.apply_lock():
            raise ValueError("boom")
    assert not lock_held()
    with open(tmp_path / "herdr.sock.layout.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 60 python -m pytest tests/test_placement_facts.py tests/test_apply_safety.py -q -k "facts or layout_lock or releases_both or worktree or submodule or detached or report or rekey or repo or checkout"`
Expected: FAIL (`ModuleNotFoundError: heelr.facts`; lock tests fail because no layout lock is taken).

- [ ] **Step 3: Implement the lock in `heelr/core.py`**

Replace `apply_lock` with:

```python
def layout_lock_path() -> Path | None:
    """The lock other Herdr layout plugins share: <socket>.layout.lock."""
    sock = os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")
    p = Path(sock + ".layout.lock")
    return p if p.parent.is_dir() else None


def _flock_until(f, deadline: float) -> None:
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise herdr.HerdrError("another layout change is running") from None
            time.sleep(0.05)


@contextlib.contextmanager
def apply_lock(wait: float | None = None, layout: bool = True):
    """One layout change at a time: heelr's own apply.lock, then (layout=True)
    the <socket>.layout.lock other layout plugins use. Re-entrant within a
    process. `wait`: seconds to wait for both (default LOCK_WAIT)."""
    global _lock_depth
    if _lock_depth:
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
        return
    deadline = time.monotonic() + (LOCK_WAIT if wait is None else wait)
    with contextlib.ExitStack() as stack:
        f = stack.enter_context(open(state_dir() / "apply.lock", "w"))
        _flock_until(f, deadline)
        if layout and (lp := layout_lock_path()) is not None:
            g = stack.enter_context(open(lp, "a"))
            _flock_until(g, deadline)
        _lock_depth = 1
        try:
            yield
        finally:
            _lock_depth = 0
```

In `heelr/sleep.py`, change both `with core.apply_lock():` (in `do_sleep` and `do_wake`) to `with core.apply_lock(layout=False):`.

Existing tests matching `"another heelr apply is running"` change to `"another layout change is running"`; grep: `grep -rn "another heelr apply" tests heelr` and update each.

- [ ] **Step 4: Implement `heelr/facts.py`**

```python
"""What heelr knows about a pane beyond Herdr's snapshot: git facts read from
the filesystem (no git process), and per-pane reports and tags."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import core


@dataclass(frozen=True)
class GitFacts:
    root: str | None            # the main checkout's root (a worktree maps to its repo)
    repo: str | None            # root's folder name
    worktree: str | None        # linked worktree name, None in the main checkout
    branch: str | None          # None when detached
    default_branch: str | None  # origin/HEAD's branch, else main/master if present


NONE = GitFacts(None, None, None, None, None)


def _read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def _branch(gitdir: Path) -> str | None:
    head = _read(gitdir / "HEAD") or ""
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


def _default_branch(common: Path) -> str | None:
    head = _read(common / "refs/remotes/origin/HEAD") or ""
    if head.startswith("ref: refs/remotes/origin/"):
        return head[len("ref: refs/remotes/origin/"):]
    for b in ("main", "master"):
        packed = (_read(common / "packed-refs") or "").splitlines()
        if (common / "refs/heads" / b).exists() or any(l.endswith(f" refs/heads/{b}") for l in packed):
            return b
    return None


def git_facts(folder: str) -> GitFacts:
    try:
        d = Path(folder).resolve()
    except OSError:
        return NONE
    if not d.exists():
        return NONE
    for top in (d, *d.parents):
        dot = top / ".git"
        if dot.is_dir():
            return GitFacts(str(top), top.name, None, _branch(dot), _default_branch(dot))
        if dot.is_file():
            ref = _read(dot) or ""
            if not ref.startswith("gitdir:"):
                return NONE
            gitdir = (top / ref[len("gitdir:"):].strip()).resolve()
            common_ref = _read(gitdir / "commondir")
            if gitdir.parent.name == "worktrees" and common_ref is not None:
                common = (gitdir / common_ref).resolve()
                root = common.parent
                return GitFacts(str(root), root.name, gitdir.name, _branch(gitdir), _default_branch(common))
            # a submodule (or any other gitdir file): its own repo
            return GitFacts(str(top), top.name, None, _branch(gitdir), _default_branch(gitdir))
    return NONE


# ------------------------------------------------------------ per-pane store

def _safe(pane_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", pane_id)


def _pane_path(pane_id: str) -> Path:
    return core.state_dir() / "panes" / (_safe(pane_id) + ".json")


def load_pane(pane_id: str) -> dict:
    try:
        return json.loads(_pane_path(pane_id).read_text())
    except (OSError, ValueError):
        return {}


def _save_pane(pane_id: str, rec: dict) -> None:
    core.write_atomic(_pane_path(pane_id), json.dumps(rec))


REPORT_FIELDS = ("cwd", "worktree", "branch", "task", "agent", "session_id")


def save_report(pane_id: str, data: dict) -> None:
    rec = load_pane(pane_id)
    rec["report"] = {k: str(v) for k, v in data.items() if k in REPORT_FIELDS and v is not None}
    rec["report_at"] = time.time()
    _save_pane(pane_id, rec)


def set_tag(pane_id: str, task: str | None) -> None:
    rec = load_pane(pane_id)
    if task:
        rec["tag"] = task
    else:
        rec.pop("tag", None)
    _save_pane(pane_id, rec)


def forget(pane_id: str) -> None:
    _pane_path(pane_id).unlink(missing_ok=True)


def _swap(v, old: str, new: str):
    if isinstance(v, str):
        return new if v == old else v
    if isinstance(v, list):
        return [_swap(x, old, new) for x in v]
    if isinstance(v, dict):
        return {(new if k == old else k): _swap(x, old, new) for k, x in v.items()}
    return v


def rekey(old: str, new: str) -> int:
    """A cross-workspace move renumbered a pane: follow it in every heelr state
    file (undo records, created lists, tab records, pane store). Sleep records
    are skipped: sleeping panes are never moved."""
    n = 0
    base = core.state_dir()
    src = _pane_path(old)
    if src.exists():
        src.replace(_pane_path(new))
        n += 1
    for p in base.rglob("*.json"):
        if "sleep" in p.relative_to(base).parts:
            continue
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        swapped = _swap(data, old, new)
        if swapped != data:
            core.write_atomic(p, json.dumps(swapped, indent=1))
            n += 1
    return n
```

Check the sleep record folder name first: `grep -n "def record_path" -A3 heelr/sleep.py`. If it is not under a `sleep/` subfolder, change the skip test to match the real path (ledger a ruling).

- [ ] **Step 5: Run all tests**

Run: `timeout 300 python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add heelr/core.py heelr/facts.py tests/test_placement_facts.py tests/test_apply_safety.py
git commit -m "placement: shared layout lock in apply_lock; git facts from files; pane store; rekey"
```

---

### Task 4: Keys: config, task chain, ticket regex, classify script

**Files:**
- Create: `heelr/key.py`, `tests/test_placement_key.py`, `examples/placement.toml`

**Interfaces:**
- Consumes: `facts.GitFacts`, `facts.git_facts`, `facts.load_pane`; `assign.classify`, `assign.PaneInfo`, `assign.load_roles`.
- Produces:
  - `key.PlacementConfig` dataclass: `enabled=False, workspace="{repo}", tab="{task}", ticket=None, create_workspaces=True, classify=None, shape_default="agents-logs", shape_by_tab: dict, burst_window=0.75`.
  - `key.load_config(path: Path | None = None) -> PlacementConfig` (default `config_dir()/"placement.toml"`; missing or invalid file → defaults, `enabled=False`).
  - `key.repo_config(root: str | None) -> dict` (reads `<root>/.heelr.toml`; keys `name`, `ticket`; bad file → `{}`).
  - `key.extract_ticket(text: str, regex: str | None) -> str`
  - `key.Key(role: str | None, repo: str, task: str)` frozen dataclass; `UNSORTED = "unsorted"`.
  - `key.pane_key(snap_pane: dict, cfg: PlacementConfig, roles: list[dict]) -> Key`
  - `key.run_classify(script: str, stdin: dict, timeout=1.0) -> dict` (`{}` on any failure; output capped at 64 KiB; result must be a dict of str values).
  - Classify cache: `state_dir()/classify-cache.json`, `{pane_id: {"hash": sha256(stdin json), "out": {...}}}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_placement_key.py
import json
import subprocess

import pytest

from heelr import facts, key
from heelr.facts import GitFacts
from test_placement_facts import make_repo, make_worktree, state  # noqa: F401

ROLES = [{"name": "agent", "agent": True}, {"name": "shell", "fallback": True}]


def sp(pane="w1:p1", cwd="/", agent=None):
    return {"pane_id": pane, "cwd": cwd, "foreground_cwd": cwd, "agent": agent,
            "agent_status": None, "terminal_title": ""}


def test_defaults_are_off(tmp_path):
    assert key.load_config(tmp_path / "missing.toml").enabled is False
    (tmp_path / "bad.toml").write_text("not = [toml")
    assert key.load_config(tmp_path / "bad.toml").enabled is False


def test_ticket_extraction():
    assert key.extract_ticket("fix/GH-330-crash", r"GH-\d+") == "GH-330"
    assert key.extract_ticket("feat-ABC-12", r"([A-Z]+-\d+)") == "ABC-12"   # group 1 when present
    assert key.extract_ticket("tab-sleep", r"GH-\d+") == "tab-sleep"         # no match keeps it whole
    assert key.extract_ticket("anything", None) == "anything"


def test_task_chain_tag_beats_report_beats_worktree(state):
    r = make_repo(state / "heelr")
    wt = make_worktree(r, "GH-330", "fix/GH-330")
    cfg = key.PlacementConfig(enabled=True, ticket=r"GH-\d+")
    assert key.pane_key(sp(cwd=str(wt)), cfg, ROLES) == key.Key("shell", "heelr", "GH-330")
    facts.save_report("w1:p1", {"worktree": "GH-9-x"})
    assert key.pane_key(sp(cwd=str(wt)), cfg, ROLES).task == "GH-9"
    facts.set_tag("w1:p1", "GH-1")
    assert key.pane_key(sp(cwd=str(wt)), cfg, ROLES).task == "GH-1"


def test_main_checkout_on_default_branch_is_main(state):
    r = make_repo(state / "heelr")
    assert key.pane_key(sp(cwd=str(r)), key.PlacementConfig(), ROLES) == key.Key("shell", "heelr", "main")


def test_feature_branch_in_main_checkout_uses_branch(state):
    r = make_repo(state / "heelr", branch="GH-5-thing")
    assert key.pane_key(sp(cwd=str(r)), key.PlacementConfig(ticket=r"GH-\d+"), ROLES).task == "GH-5"


def test_not_in_git_is_unsorted(state):
    (state / "notes").mkdir()
    k = key.pane_key(sp(cwd=str(state / "notes")), key.PlacementConfig(), ROLES)
    assert k == key.Key("shell", "notes", key.UNSORTED)


def test_repo_config_overrides_name_and_ticket(state):
    r = make_repo(state / "heelr", branch="FAD-12-x")
    (r / ".heelr.toml").write_text('name = "hl"\nticket = \'FAD-\\d+\'\n')
    k = key.pane_key(sp(cwd=str(r)), key.PlacementConfig(ticket=r"GH-\d+"), ROLES)
    assert (k.repo, k.task) == ("hl", "FAD-12")


def test_classify_fields_win_and_missing_fall_back(state, monkeypatch):
    r = make_repo(state / "heelr")
    monkeypatch.setattr(key, "run_classify", lambda s, d, timeout=1.0: {"task": "X"})
    k = key.pane_key(sp(cwd=str(r)), key.PlacementConfig(classify="c.sh"), ROLES)
    assert (k.repo, k.task) == ("heelr", "X")


@pytest.mark.parametrize("out,rc", [("not json", 0), ("[1,2]", 0), ('{"task": 3}', 0), ("{}", 1),
                                    ("x" * 200_000, 0)])
def test_classify_bad_output_is_empty(monkeypatch, out, rc):   # Review Focus 4
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, rc, stdout=out, stderr=""))
    assert key.run_classify("c.sh", {"pane_id": "p"}) == {}


def test_classify_timeout_is_empty(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("c.sh", 1.0)
    monkeypatch.setattr(subprocess, "run", slow)
    assert key.run_classify("c.sh", {"pane_id": "p"}) == {}


def test_classify_is_cached_by_input(state, monkeypatch):
    r = make_repo(state / "heelr")
    calls = []
    monkeypatch.setattr(key, "run_classify", lambda s, d, timeout=1.0: calls.append(1) or {"task": "T"})
    cfg = key.PlacementConfig(classify="c.sh")
    key.pane_key(sp(cwd=str(r)), cfg, ROLES)
    key.pane_key(sp(cwd=str(r)), cfg, ROLES)
    assert len(calls) == 1
    key.pane_key(sp(cwd=str(r), agent="claude"), cfg, ROLES)   # facts changed: run again
    assert len(calls) == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 60 python -m pytest tests/test_placement_key.py -q`
Expected: FAIL (`ModuleNotFoundError: heelr.key`).

- [ ] **Step 3: Implement `heelr/key.py`**

```python
"""A pane's placement key (role, repo, task): config, the task chain, ticket
extraction and the optional classify script."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import core, facts
from .assign import PaneInfo, classify
from .layouts import config_dir

UNSORTED = "unsorted"
CLASSIFY_MAX = 64 * 1024


@dataclass
class PlacementConfig:
    enabled: bool = False
    workspace: str = "{repo}"
    tab: str = "{task}"
    ticket: str | None = None
    create_workspaces: bool = True
    classify: str | None = None
    shape_default: str = "agents-logs"
    shape_by_tab: dict = field(default_factory=dict)
    burst_window: float = 0.75


@dataclass(frozen=True)
class Key:
    role: str | None
    repo: str
    task: str


def load_config(path: Path | None = None) -> PlacementConfig:
    p = path or (config_dir() / "placement.toml")
    try:
        with open(p, "rb") as f:
            d = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return PlacementConfig()
    pl, shape = d.get("placement", {}), d.get("placement", {}).get("shape", {})
    return PlacementConfig(
        enabled=pl.get("enabled") is True,
        workspace=str(pl.get("workspace", "{repo}")), tab=str(pl.get("tab", "{task}")),
        ticket=pl.get("ticket") or None, create_workspaces=pl.get("create_workspaces", True) is not False,
        classify=pl.get("classify") or None, shape_default=str(shape.get("default", "agents-logs")),
        shape_by_tab=dict(shape.get("by_tab", {})), burst_window=float(pl.get("burst_window", 0.75)))


def repo_config(root: str | None) -> dict:
    if not root:
        return {}
    try:
        with open(Path(root) / ".heelr.toml", "rb") as f:
            d = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {k: str(d[k]) for k in ("name", "ticket") if k in d}


def extract_ticket(text: str, regex: str | None) -> str:
    if not regex:
        return text
    try:
        m = re.search(regex, text)
    except re.error:
        return text
    if not m:
        return text
    return m.group(1) if m.re.groups else m.group(0)


def run_classify(script: str, stdin: dict, timeout: float = 1.0) -> dict:
    try:
        proc = subprocess.run([str(Path(script).expanduser())], input=json.dumps(stdin),
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0 or len(proc.stdout) > CLASSIFY_MAX:
        return {}
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return {}
    if not isinstance(out, dict):
        return {}
    return {k: v for k, v in out.items() if k in ("role", "repo", "task") and isinstance(v, str) and v}


def _cache_path() -> Path:
    return core.state_dir() / "classify-cache.json"


def _classify_cached(script: str, pane_id: str, stdin: dict) -> dict:
    h = hashlib.sha256(json.dumps(stdin, sort_keys=True).encode()).hexdigest()
    try:
        cache = json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(pane_id)
    if hit and hit.get("hash") == h:
        return hit.get("out", {})
    out = run_classify(script, stdin)
    cache[pane_id] = {"hash": h, "out": out}
    try:
        core.write_atomic(_cache_path(), json.dumps(cache))
    except OSError:
        pass
    return out


def _task(gf: facts.GitFacts, store: dict) -> str:
    if store.get("tag"):
        return store["tag"]
    rep = store.get("report", {})
    if rep.get("task"):
        return rep["task"]
    if rep.get("worktree") or rep.get("branch"):
        return rep.get("worktree") or rep["branch"]
    if gf.worktree:
        return gf.worktree
    if gf.branch and gf.branch != gf.default_branch:
        return gf.branch
    if gf.root:
        return "main"
    return UNSORTED


def pane_key(snap_pane: dict, cfg: PlacementConfig, roles: list[dict]) -> Key:
    pid = snap_pane["pane_id"]
    store = facts.load_pane(pid)
    folder = store.get("report", {}).get("cwd") or snap_pane.get("foreground_cwd") or snap_pane.get("cwd") or "/"
    gf = facts.git_facts(folder)
    rc = repo_config(gf.root)
    out = {}
    if cfg.classify:
        out = _classify_cached(cfg.classify, pid, {
            "pane_id": pid, "agent": snap_pane.get("agent"), "agent_status": snap_pane.get("agent_status"),
            "cwd": snap_pane.get("cwd"), "foreground_cwd": snap_pane.get("foreground_cwd"),
            "git": {"root": gf.root, "worktree": gf.worktree, "branch": gf.branch},
            "report": store.get("report", {}), "tag": store.get("tag"), "repo_config": rc})
    role = out.get("role") or classify(PaneInfo(pid, agent=snap_pane.get("agent")), roles)
    repo = out.get("repo") or rc.get("name") or gf.repo or (Path(folder).name or "/")
    task = out.get("task") or _task(gf, store)
    if task != UNSORTED and "task" not in out:
        task = extract_ticket(task, rc.get("ticket") or cfg.ticket)
    return Key(role, repo, task)
```

`examples/placement.toml`:

```toml
# Copy to ~/.config/heelr/placement.toml. Placement is off until enabled = true.
[placement]
enabled = false
workspace = "{repo}"        # one workspace per repo
tab = "{task}"              # one tab per task; tab = "{repo}" for one tab per repo
# ticket = 'GH-\d+'         # pull ticket ids out of worktree / branch names
create_workspaces = true
# classify = "~/.config/heelr/classify.sh"   # stdin: pane facts JSON; stdout: {"role","repo","task"}
# burst_window = 0.75       # seconds; new panes closer together than this are a burst

[placement.shape]
default = "agents-logs"     # layout for each placed tab
# by_tab = { main = "main-left" }
```

- [ ] **Step 4: Run all tests**

Run: `timeout 300 python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add heelr/key.py tests/test_placement_key.py examples/placement.toml
git commit -m "placement: keys (config, task chain, ticket regex, classify script with cache)"
```

---

### Task 5: Planner, executor, undo

**Files:**
- Create: `heelr/place.py`, `tests/test_placement_place.py`
- Modify: `heelr/sleep.py` (add `sleeping_panes`), `heelr/core.py` (`run` notes created panes as own)

**Interfaces:**
- Consumes: `key.Key`, `key.pane_key`, `key.PlacementConfig`, `key.UNSORTED`; `facts.rekey`; `core.apply_lock`, `core.write_atomic`, `core.state_dir`, `core.SLEEP_MARK`, `core.base_label`, `core._closable`; `herdr.call`, `herdr.move`; `sleep.sleeping_panes`.
- Produces:
  - `sleep.sleeping_panes(snap: dict) -> set[str]` — panes in any tab whose label starts with `core.SLEEP_MARK`, plus panes listed as slept in any sleep record.
  - `place.Dest(workspace: str, tab: str)` frozen dataclass (labels).
  - `place.dest_for(k: Key, cfg) -> Dest | None` (None when `k.task == UNSORTED`).
  - `place.drifted(snap, cfg, roles, panes: set[str] | None = None) -> dict[str, Dest]` — pane → where it belongs, for panes not in their destination and not sleeping/unsorted.
  - `place.Move(pane: str, dest: Dest, focus: bool)`.
  - `place.execute(snap, moves: list[Move], cfg, log=print) -> list[dict]` — performs moves under `apply_lock`; creates missing workspaces (if allowed) and tabs; returns undo entries `{"pane", "from_ws", "from_tab", "to_tab", "created_tab": bool, "created_ws": bool}` with **current** pane ids; re-keys state on id change; records heelr-created tab ids in `state_dir()/place-tabs.json`.
  - `place.save_undo(entries)`, `place.back(log=print) -> int` — moves each pane back to `from_tab` if it still exists and the pane is still where heelr put it; skips and reports otherwise; closes heelr-created tabs only if empty (Herdr closes emptied tabs itself; nothing to do).
  - `place.own_panes() -> set[str]` — panes heelr created or moved in the last 10 s (`state_dir()/place-own.json`), used to ignore heelr's own events. Fed by `place.execute` (moved panes, a new workspace's shell) **and** by `core.run`'s `note()` for every pane `apply`/`back`/auto-tile creates (`place._note_own` is called there; import it lazily to avoid a cycle). `wake` creates no panes (it types into existing ones), so it needs nothing.
  - `place.execute(..., exact: bool = False)` — `exact=True` (used by `pull`) ignores the per-key clash rule: every pane goes into the one tab with that label.

Label clash rule: the destination tab in workspace W is the tab whose `base_label` equals the wanted label **and** that heelr created for the same key, or any tab with that exact label not created by heelr (user tab wins, Review Focus 2). If a heelr-created tab has that label for a *different* key, the new tab is created as `"<task> · <repo>"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_placement_place.py
import json

import pytest

from heelr import core, facts, herdr, key, place, sleep
from fakeherdr import FakeHerdr
from test_placement_facts import make_repo, make_worktree  # noqa: F401

ROLES = [{"name": "agent", "agent": True}, {"name": "shell", "fallback": True}]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    f = FakeHerdr()
    f.version = "0.9.3"
    monkeypatch.setattr(herdr, "call", f.call)
    monkeypatch.setattr(herdr, "run_in", f.run_in)
    return f


def cfg(**kw):
    return key.PlacementConfig(enabled=True, ticket=r"GH-\d+", **kw)


def test_unsorted_has_no_destination():
    assert place.dest_for(key.Key("shell", "x", key.UNSORTED), cfg()) is None


def test_templates():
    k = key.Key("agent", "heelr", "GH-1")
    assert place.dest_for(k, cfg()) == place.Dest("heelr", "GH-1")
    assert place.dest_for(k, cfg(tab="{repo}", workspace="all")) == place.Dest("all", "heelr")


def test_drift_lists_only_misplaced(fake, tmp_path):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = fake.add_workspace("heelr")
    t1, (a,) = fake.add_tab(w, "GH-1", 1, cwd=str(wt))
    t2, (b,) = fake.add_tab(w, "main", 1, cwd=str(wt))           # wrong tab
    d = place.drifted(fake.snapshot(), cfg(), ROLES)
    assert d == {b: place.Dest("heelr", "GH-1")}


def test_sleeping_panes_never_drift(fake, tmp_path):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = fake.add_workspace("heelr")
    fake.add_tab(w, "GH-1", 1, cwd=str(wt))
    t2, (b,) = fake.add_tab(w, core.SLEEP_MARK + "main", 1, cwd=str(wt))
    assert place.drifted(fake.snapshot(), cfg(), ROLES) == {}


def test_move_into_existing_user_tab_keeps_its_label(fake, tmp_path):     # Review Focus 2
    w = fake.add_workspace("heelr")
    t1, (a,) = fake.add_tab(w, "GH-1", 1)
    t2, (b,) = fake.add_tab(w, "other", 2)
    place.execute(fake.snapshot(), [place.Move(fake.panes(t2)[1], place.Dest("heelr", "GH-1"), False)], cfg())
    assert fake.tabs[t1]["label"] == "GH-1" and len(fake.panes(t1)) == 2
    assert fake.labels().count("GH-1") == 1


def test_creates_missing_tab_with_final_label_without_focus(fake):
    w = fake.add_workspace("heelr")
    t1, (a, b) = fake.add_tab(w, "main", 2)
    before = (fake.focused_tab, fake.focused_pane)
    place.execute(fake.snapshot(), [place.Move(b, place.Dest("heelr", "GH-2"), False)], cfg())
    assert "GH-2" in fake.labels()
    assert (fake.focused_tab, fake.focused_pane) == before
    assert any("--no-focus" in c for c in map(" ".join, fake.calls) if "pane move" in c)


def test_cross_workspace_move_rekeys_everything(fake):                     # Review Focus 3
    a = fake.add_workspace("repo-a")
    b = fake.add_workspace("repo-b")
    ta, (p1, p2) = fake.add_tab(a, "main", 2)
    tb, (q,) = fake.add_tab(b, "GH-3", 1)
    facts.set_tag(p2, "GH-3")
    undo = place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-b", "GH-3"), False)], cfg())
    new = undo[0]["pane"]
    assert new != p2 and new in fake.panes(tb)
    assert facts.load_pane(new)["tag"] == "GH-3" and facts.load_pane(p2) == {}


def test_creates_missing_workspace_and_closes_its_idle_shell(fake):
    a = fake.add_workspace("repo-a")
    ta, (p1, p2) = fake.add_tab(a, "main", 2)
    undo = place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-new", "GH-4"), False)], cfg())
    ws = next(w for w, l in fake.workspaces.items() if l == "repo-new")
    tabs = [t for t, v in fake.tabs.items() if v["ws"] == ws]
    assert [fake.tabs[t]["label"] for t in tabs] == ["GH-4"]
    assert undo[0]["created_ws"] is True


def test_no_workspace_creation_when_disallowed(fake):
    a = fake.add_workspace("repo-a")
    ta, (p1, p2) = fake.add_tab(a, "main", 2)
    place.execute(fake.snapshot(), [place.Move(p2, place.Dest("repo-new", "GH-4"), False)],
                  cfg(create_workspaces=False))
    assert set(fake.workspaces.values()) == {"repo-a"}
    assert any(fake.tabs[t]["label"] == "GH-4" and fake.tabs[t]["ws"] == a for t in fake.tabs)


def test_clash_with_heelr_tab_of_another_key_is_qualified(fake):
    w = fake.add_workspace("all")
    t1, (a, b) = fake.add_tab(w, "main", 2)
    place.execute(fake.snapshot(), [place.Move(b, place.Dest("all", "GH-1"), False)], cfg(), key_of={b: key.Key("shell", "r1", "GH-1")})
    t3, (c,) = fake.add_tab(w, "x", 1)
    place.execute(fake.snapshot(), [place.Move(c, place.Dest("all", "GH-1"), False)], cfg(), key_of={c: key.Key("shell", "r2", "GH-1")})
    assert "GH-1 · r2" in fake.labels()


def test_back_restores_and_skips_closed(fake):
    w = fake.add_workspace("heelr")
    t1, (a, b, c) = fake.add_tab(w, "main", 3)
    undo = place.execute(fake.snapshot(), [place.Move(b, place.Dest("heelr", "GH-1"), False),
                                           place.Move(c, place.Dest("heelr", "GH-2"), False)], cfg())
    place.save_undo(undo)
    fake.call("pane", "close", c)
    lines = []
    assert place.back(log=lines.append) == 0
    assert b in fake.panes(t1)
    assert any("skipped" in l and "closed" in l for l in lines)


def test_pull_style_exact_puts_two_repos_in_one_new_tab(fake):        # reviewer item 4
    w = fake.add_workspace("ra")
    ta, (a, b) = fake.add_tab(w, "main", 2)
    keys = {a: key.Key("shell", "ra", "GH-5"), b: key.Key("shell", "rb", "GH-5")}
    place.execute(fake.snapshot(), [place.Move(a, place.Dest("ra", "GH-5"), False),
                                    place.Move(b, place.Dest("ra", "GH-5"), False)],
                  cfg(), key_of=keys, exact=True)
    assert fake.labels().count("GH-5") == 1 and "GH-5 · rb" not in fake.labels()


def test_apply_created_panes_are_own(fake):
    from test_apply_safety import heelr
    w = fake.add_workspace("x")
    t, (a,) = fake.add_tab(w, "main", 1)
    heelr("apply", "agents-logs", "--tab", t)                    # adds a shell for the bottom row
    created = [p for p in fake.panes(t) if p != a]
    assert created and set(created) <= place.own_panes()


def test_drift_qualifier_must_match_the_repo(fake, tmp_path):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = fake.add_workspace("heelr")
    t1, (a,) = fake.add_tab(w, "GH-1 · other", 1, cwd=str(wt))
    assert a in place.drifted(fake.snapshot(), cfg(), ROLES)


def test_own_panes_are_remembered(fake):
    w = fake.add_workspace("heelr")
    t1, (a, b) = fake.add_tab(w, "main", 2)
    undo = place.execute(fake.snapshot(), [place.Move(b, place.Dest("heelr", "GH-1"), False)], cfg())
    assert undo[0]["pane"] in place.own_panes()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 60 python -m pytest tests/test_placement_place.py -q`
Expected: FAIL (`ModuleNotFoundError: heelr.place`).

- [ ] **Step 3: Implement `sleep.sleeping_panes`**

Read `heelr/sleep.py:235-275` (record path, `slept(rec)` entries) first. Add:

```python
def sleeping_panes(snap: dict) -> set[str]:
    """Panes placement must leave alone: in a 💤 tab, or slept in a record."""
    from . import core
    out = {p["pane_id"] for p in snap["panes"]
           if any(t["tab_id"] == p["tab_id"] and (t.get("label") or "").startswith(core.SLEEP_MARK)
                  for t in snap["tabs"])}
    for t in snap["tabs"]:
        out |= {e["pane"] for e in slept(load_record(t["tab_id"])) if "pane" in e}
    return out
```

If a slept entry stores the pane under another key than `"pane"`, use that key (ledger a ruling).

- [ ] **Step 4: Implement `heelr/place.py`**

```python
"""Put panes where their key says: plan the moves, run them, undo them."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from . import core, facts, herdr, sleep
from .key import UNSORTED, Key, PlacementConfig, pane_key

OWN_TTL = 10.0


@dataclass(frozen=True)
class Dest:
    workspace: str
    tab: str


@dataclass(frozen=True)
class Move:
    pane: str
    dest: Dest
    focus: bool


def dest_for(k: Key, cfg: PlacementConfig) -> Dest | None:
    if k.task == UNSORTED:
        return None
    f = {"repo": k.repo, "task": k.task, "role": k.role or ""}
    return Dest(cfg.workspace.format(**f), cfg.tab.format(**f))


# ------------------------------------------------------------------ records

def _json(name: str, default):
    try:
        return json.loads((core.state_dir() / name).read_text())
    except (OSError, ValueError):
        return default


def _put(name: str, data) -> None:
    core.write_atomic(core.state_dir() / name, json.dumps(data, indent=1))


def _heelr_tabs() -> dict:
    return _json("place-tabs.json", {})        # tab_id -> {"repo","task"}


def own_panes() -> set[str]:
    now = time.time()
    return {p for p, t in _json("place-own.json", {}).items() if now - t < OWN_TTL}


def _note_own(pane: str) -> None:
    now = time.time()
    rec = {p: t for p, t in _json("place-own.json", {}).items() if now - t < OWN_TTL}
    rec[pane] = now
    _put("place-own.json", rec)


# ------------------------------------------------------------------- drift

def _labels(snap: dict) -> tuple[dict, dict]:
    ws_label = {w["workspace_id"]: w.get("label") or "" for w in snap.get("workspaces", [])}
    tab_of = {t["tab_id"]: t for t in snap["tabs"]}
    return ws_label, tab_of


def drifted(snap: dict, cfg: PlacementConfig, roles: list[dict], panes: set[str] | None = None) -> dict[str, Dest]:
    ws_label, tab_of = _labels(snap)
    skip = sleep.sleeping_panes(snap)
    out = {}
    for sp in snap["panes"]:
        pid = sp["pane_id"]
        if (panes is not None and pid not in panes) or pid in skip:
            continue
        k = pane_key(sp, cfg, roles)
        d = dest_for(k, cfg)
        if d is None:
            continue
        here = Dest(ws_label.get(sp["workspace_id"], ""), core.base_label(tab_of[sp["tab_id"]]))
        if here.workspace != d.workspace or here.tab not in (d.tab, f"{d.tab} · {k.repo}"):
            out[pid] = d
    return out


# ----------------------------------------------------------------- execute

def _find_tab(snap: dict, ws_id: str, label: str, k: Key | None) -> tuple[str | None, str]:
    """(tab id to use or None, label to create with)."""
    mine = _heelr_tabs()
    for t in snap["tabs"]:
        if t["workspace_id"] != ws_id or core.base_label(t) != label:
            continue
        rec = mine.get(t["tab_id"])
        if rec is None or k is None or rec == {"repo": k.repo, "task": k.task}:
            return t["tab_id"], label
        qualified = f"{label} · {k.repo}"
        q = next((x for x in snap["tabs"] if x["workspace_id"] == ws_id and core.base_label(x) == qualified), None)
        return (q["tab_id"] if q else None), qualified
    return None, label


def execute(snap: dict, moves: list[Move], cfg: PlacementConfig, log=print,
            key_of: dict[str, Key] | None = None, exact: bool = False) -> list[dict]:
    undo: list[dict] = []
    with core.apply_lock():
        for mv in moves:
            snap = herdr.snapshot()                       # each move can renumber panes and close tabs
            sp = next((p for p in snap["panes"] if p["pane_id"] == mv.pane), None)
            if sp is None:
                log(f"skipped {mv.pane}: closed")
                continue
            ws_label, _ = _labels(snap)
            ws_id = next((w for w, l in ws_label.items() if l == mv.dest.workspace), None)
            created_ws = False
            if ws_id is None:
                if cfg.create_workspaces:
                    r = herdr.call("workspace", "create", "--label", mv.dest.workspace,
                                   "--cwd", sp.get("cwd") or "/", "--no-focus")
                    ws_id, created_ws = r["workspace"]["workspace_id"], True
                    _note_own(r["pane"]["pane_id"])
                    snap = herdr.snapshot()
                else:
                    ws_id = sp["workspace_id"]
            k = (key_of or {}).get(mv.pane)
            tab_id, label = _find_tab(snap, ws_id, mv.dest.tab, None if exact else k)
            created_tab = tab_id is None
            new_pane = mv.pane
            if created_tab:
                res = herdr.move(mv.pane, new_tab=True, workspace=ws_id, label=label, focus=mv.focus)
                tab_id = res["pane"]["tab_id"]
                tabs = _heelr_tabs()
                tabs[tab_id] = {"repo": k.repo, "task": k.task} if k else {"repo": mv.dest.workspace, "task": mv.dest.tab}
                _put("place-tabs.json", tabs)
            else:
                target = next(p["pane_id"] for p in snap["panes"] if p["tab_id"] == tab_id)
                res = herdr.move(mv.pane, tab=tab_id, target=target, split="right", focus=mv.focus)
            new_pane = res["pane"]["pane_id"]
            if new_pane != mv.pane:
                facts.rekey(mv.pane, new_pane)
            _note_own(new_pane)
            undo.append({"pane": new_pane, "from_ws": sp["workspace_id"], "from_tab": sp["tab_id"],
                         "to_tab": tab_id, "created_tab": created_tab, "created_ws": created_ws})
            if created_ws:
                _close_initial_shell(ws_id, keep=new_pane, log=log)
    return undo


def _close_initial_shell(ws_id: str, keep: str, log=print) -> None:
    """A workspace heelr created starts with one shell; close it if still idle."""
    snap = herdr.snapshot()
    for p in snap["panes"]:
        if p["workspace_id"] == ws_id and p["pane_id"] != keep and p["pane_id"] in own_panes():
            if core._closable(p["pane_id"], None, log=lambda *a: None):
                herdr.call("pane", "close", p["pane_id"])


# --------------------------------------------------------------------- undo

def save_undo(entries: list[dict]) -> None:
    if entries:
        _put("place-back.json", {"moves": entries})


def back(log=print) -> int:
    rec = _json("place-back.json", {})
    moves = rec.get("moves", [])
    if not moves:
        log("nothing to undo")
        return 1
    with core.apply_lock():
        for e in reversed(moves):
            snap = herdr.snapshot()
            sp = next((p for p in snap["panes"] if p["pane_id"] == e["pane"]), None)
            if sp is None:
                log(f"skipped {e['pane']}: closed")
                continue
            if sp["tab_id"] != e["to_tab"]:
                log(f"skipped {e['pane']}: moved by hand since")
                continue
            if not any(t["tab_id"] == e["from_tab"] for t in snap["tabs"]):
                res = herdr.move(e["pane"], new_tab=True, workspace=e["from_ws"], focus=False)
            else:
                target = next(p["pane_id"] for p in snap["panes"] if p["tab_id"] == e["from_tab"])
                res = herdr.move(e["pane"], tab=e["from_tab"], target=target, split="right", focus=False)
            new = res["pane"]["pane_id"]
            if new != e["pane"]:
                facts.rekey(e["pane"], new)
            _note_own(new)
    (core.state_dir() / "place-back.json").unlink(missing_ok=True)
    return 0
```

In `heelr/core.py`, inside `run`'s `note(pid)` (core.py:249), add as its first line:

```python
            from . import place
            place._note_own(pid)
```

Check `core._closable`'s real signature first (`grep -n "def _closable" -A8 heelr/core.py`) and adapt the call; ledger any change.

- [ ] **Step 5: Run all tests**

Run: `timeout 300 python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add heelr/place.py heelr/sleep.py tests/test_placement_place.py
git commit -m "placement: planner, executor with pane re-keying, label clashes, undo"
```

---

### Task 6: Bursts and placement inside `heelr auto`

**Files:**
- Create: `heelr/burst.py`, `tests/test_placement_auto.py`
- Modify: `heelr/cli.py` (`cmd_auto`)

**Interfaces:**
- Consumes: `place.*`, `key.load_config`, `key.pane_key`, `core.apply_lock`.
- Produces:
  - `burst.note(pane_id: str, now: float) -> None` — appends to `state_dir()/burst.json` under its own `burst.lock` flock; keeps the last 10 s.
  - `burst.decide(pane_id: str, window: float, now: float, lookback: float = 2.0) -> str` — events are chained into one burst when each is within `lookback` (2 s) of the previous. `"single"`: the burst has only this pane. `"burst-last"`: this pane is the burst's newest event and `now - its time >= window`. Else `"burst-wait"`.
  - `burst.burst_panes(pane_id, lookback=2.0) -> list[str]` — the panes of the burst containing `pane_id`.

Known limit (ledgered): the **first** pane of a restore has nothing before it and nothing within 0.75 s after only if the next pane comes 0.75–2 s later; it is then placed as "single" with focus once. Every later pane in that restore sees the earlier one and moves without focus. Raising `burst_window` closes the gap at the cost of latency.
  - `cli._place_new_pane(event: dict, cfg) -> None` — the hook logic.

Behaviour (carry-over 1): a `pane.created` hook (env `HERDR_PLUGIN_EVENT == "pane.created"`, pane from `HERDR_PLUGIN_EVENT_JSON` `data.pane.pane_id`):
1. If placement is off, or the pane is in `place.own_panes()`: skip placement (auto-tile runs as before).
2. `burst.note(pane)`; sleep `cfg.burst_window` **outside** any lock; then, **under `apply_lock`**, re-check `place.own_panes()` (a workspace shell heelr created meanwhile) and run `burst.decide`.
3. `"single"`: place the pane with `focus=True` (if its destination differs from where it is).
4. `"burst-last"`: place every pane of the burst with `focus=False` (panes already placed by an earlier hook are no longer drifted and are skipped).
5. `"burst-wait"`: do nothing (the newest event's hook does it).
6. Never write the undo record or call `set_last` (Global Constraints).
7. Then run auto-tile as today.

Keys (and any classify script) are computed **before** taking `apply_lock`, so a slow classify never holds the shared layout lock; `drifted()` re-reads under the lock (cached classify).
Every pane.created waits `burst_window` (default 0.75 s) before it is placed: ledger this as the accepted cost of not having a daemon.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_placement_auto.py
import json

import pytest

from heelr import burst, cli, core, key, place
from test_placement_place import fake, cfg  # noqa: F401
from test_placement_facts import make_repo, make_worktree


def test_single_and_burst_decisions(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    burst.note("a", 100.0)
    assert burst.decide("a", 0.75, 100.8) == "single"
    burst.note("b", 200.0)
    burst.note("c", 200.2)
    burst.note("d", 200.4)
    assert burst.decide("b", 0.75, 201.2) == "burst-wait"
    assert burst.decide("d", 0.75, 201.2) == "burst-last"
    assert burst.burst_panes("d") == ["b", "c", "d"]


def test_restore_one_second_apart_is_a_burst(tmp_path, monkeypatch):   # reviewer item 3
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    burst.note("a", 300.0)
    burst.note("b", 301.0)
    burst.note("c", 302.0)
    assert burst.decide("b", 0.75, 302.5) == "burst-wait"
    assert burst.decide("c", 0.75, 302.8) == "burst-last"   # every pane after the first moves without focus


def event(pane):
    return {"HERDR_PLUGIN_EVENT": "pane.created",
            "HERDR_PLUGIN_EVENT_JSON": json.dumps({"event": "pane_created", "data": {"pane": {"pane_id": pane}}})}


@pytest.fixture
def on(fake, tmp_path, monkeypatch):
    (tmp_path / "cfg").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "cfg/heelr").mkdir()
    (tmp_path / "cfg/heelr/placement.toml").write_text('[placement]\nenabled = true\nticket = \'GH-\\d+\'\n')
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return fake


def test_off_by_default_moves_nothing(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nocfg"))
    r = make_repo(tmp_path / "heelr")
    w = fake.add_workspace("heelr")
    t1, (a, b) = fake.add_tab(w, "other", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    before = fake.snapshot()["panes"]
    cli.main(["--allow-untested", "auto"])
    assert fake.snapshot()["panes"] == before


def test_single_new_pane_moves_with_focus(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(wt))
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    tab = next(t for t in on.tabs if on.tabs[t]["label"] == "GH-1")
    assert on.focused_tab == tab


def test_burst_moves_without_focus(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b, c) = on.add_tab(w, "main", 3, cwd=str(wt))
    burst.note(b, 1000.0)
    monkeypatch.setattr(cli.time, "time", lambda: 1000.2)
    before = (on.focused_tab, on.focused_pane)
    for k, v in event(c).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert (on.focused_tab, on.focused_pane)[0] == before[0]
    assert "GH-1" in on.labels()


def test_auto_placement_leaves_the_undo_record_alone(on, tmp_path, monkeypatch):   # reviewer item 7
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert not (core.state_dir() / "place-back.json").exists()


def test_pane_created_by_heelr_under_the_lock_is_not_placed(on, tmp_path, monkeypatch):
    """core.run creates a pane while holding apply_lock; its pane.created hook runs
    concurrently and must not place it (the own_panes re-check under the lock)."""
    import threading
    from test_apply_safety import heelr
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a,) = on.add_tab(w, "main", 1, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    created = []
    hook = None

    def on_call(args):
        nonlocal hook
        if args[:2] == ("pane", "split") and hook is None:        # fire the hook mid-apply
            def run():
                new = max(on.all_panes(), key=lambda p: int(p.split("p")[-1]))
                created.append(new)
                for k, v in event(new).items():
                    monkeypatch.setenv(k, v)
                cli.main(["--allow-untested", "auto"])
            hook = threading.Thread(target=run)
    on.on_call = on_call
    heelr("apply", "agents-logs", "--tab", t1)
    hook.start()
    hook.join(10)
    assert "GH-1" not in on.labels()


def test_user_pane_right_after_an_auto_tile_run_is_placed(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    with core.apply_lock():
        pass                                     # an unrelated auto-tile run just finished
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert "GH-1" in on.labels()


def test_heelr_own_panes_are_ignored(on, tmp_path, monkeypatch):
    r = make_repo(tmp_path / "heelr")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(make_worktree(r, "GH-1", "GH-1")))
    place._note_own(b)
    for k, v in event(b).items():
        monkeypatch.setenv(k, v)
    cli.main(["--allow-untested", "auto"])
    assert "GH-1" not in on.labels()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 60 python -m pytest tests/test_placement_auto.py -q`
Expected: FAIL (`ModuleNotFoundError: heelr.burst`).

- [ ] **Step 3: Implement `heelr/burst.py`**

```python
"""pane.created events close together are a burst (a restore, a multi-pane
apply): placed together, without moving focus. Shared across hook processes
through a small state file under its own lock."""
from __future__ import annotations

import contextlib
import fcntl
import json

from . import core

KEEP = 10.0


@contextlib.contextmanager
def _locked():
    with open(core.state_dir() / "burst.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _load() -> list[list]:
    try:
        return json.loads((core.state_dir() / "burst.json").read_text())
    except (OSError, ValueError):
        return []


def note(pane_id: str, now: float) -> None:
    with _locked():
        ev = [e for e in _load() if now - e[1] < KEEP and e[0] != pane_id] + [[pane_id, now]]
        core.write_atomic(core.state_dir() / "burst.json", json.dumps(ev))


LOOKBACK = 2.0


def _cluster(pane_id: str, ev: list[list], lookback: float = LOOKBACK) -> list[list]:
    ev = sorted(ev, key=lambda e: e[1])
    groups, cur = [], []
    for e in ev:
        if cur and e[1] - cur[-1][1] > lookback:
            groups.append(cur)
            cur = []
        cur.append(e)
    if cur:
        groups.append(cur)
    return next((g for g in groups if any(e[0] == pane_id for e in g)), [])


def decide(pane_id: str, window: float, now: float, lookback: float = LOOKBACK) -> str:
    with _locked():
        g = _cluster(pane_id, _load(), lookback)
    if len(g) <= 1:
        return "single"
    newest = g[-1]
    if newest[0] == pane_id and now - newest[1] >= window:
        return "burst-last"
    return "burst-wait"


def burst_panes(pane_id: str, lookback: float = LOOKBACK) -> list[str]:
    with _locked():
        return [e[0] for e in _cluster(pane_id, _load(), lookback)]
```

- [ ] **Step 4: Wire into `cmd_auto` in `heelr/cli.py`**

Add `import json` and `from . import burst, key as keymod, place` at the top. Add:

```python
def _event_pane() -> str | None:
    if os.environ.get("HERDR_PLUGIN_EVENT") != "pane.created":
        return None
    try:
        ev = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
        return ev["data"]["pane"]["pane_id"]
    except (ValueError, KeyError, TypeError):
        return None


def _place_new_pane(pane: str, cfg) -> None:
    if pane in place.own_panes():
        return
    burst.note(pane, time.time())
    time.sleep(cfg.burst_window)
    verdict = burst.decide(pane, cfg.burst_window, time.time())
    if verdict == "burst-wait":
        return
    panes = [pane] if verdict == "single" else burst.burst_panes(pane)
    roles = _roles()
    pre = herdr.snapshot()
    keys = {sp["pane_id"]: keymod.pane_key(sp, cfg, roles) for sp in pre["panes"] if sp["pane_id"] in panes}
    # The lock is reached within burst_window + AUTO_WAIT (< 6 s) of the event, under
    # place.OWN_TTL (10 s); creators note their panes while holding this lock, so this
    # re-check cannot miss a pane heelr made.
    with core.apply_lock(wait=AUTO_WAIT):
        panes = [p for p in panes if p not in place.own_panes()]
        if not panes:
            return
        snap = herdr.snapshot()
        todo = place.drifted(snap, cfg, roles, set(panes))
        if todo:
            moves = [place.Move(p, d, focus=(verdict == "single")) for p, d in todo.items()]
            place.execute(snap, moves, cfg, log=lambda *a: None, key_of=keys)   # no undo, no set_last
```

At the start of `cmd_auto`, before `if not auto_tile_enabled(): return 0`, add:

```python
    pane = _event_pane()
    cfg = keymod.load_config()
    if pane and cfg.enabled:
        try:
            _place_new_pane(pane, cfg)
        except Exception as e:  # never fail the hook
            _auto_error(e)
```

(`place.execute` takes `apply_lock` itself; auto-tile then takes it again below, so the two never run at once and the newly placed pane's tabs are re-laid by the same `heelr auto` process.)

- [ ] **Step 5: Run all tests**

Run: `timeout 300 python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add heelr/burst.py heelr/cli.py tests/test_placement_auto.py
git commit -m "placement: new panes placed from heelr auto; bursts placed together without focus"
```

---

### Task 7: Commands: report, tag, sort, pull, drift-status, back

**Files:**
- Modify: `heelr/cli.py`
- Create: `tests/test_placement_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces CLI:
  - `heelr report [--pane ID]` — reads JSON from stdin, `facts.save_report`; never moves; exit 0 even on bad JSON (prints nothing).
  - `heelr tag [TASK] [--pane ID] [--clear]` — sets / clears the tag.
  - `heelr sort [--workspace | --all] [-n]` — moves drifted panes in the focused tab / workspace / server, `focus=False`, saves undo, `set_last(ws, "place")`.
  - `heelr pull TASK [-n]` — moves every non-sleeping pane whose key has that task into a tab labelled `TASK` in the focused workspace; saves undo.
  - `heelr drift-status` — read-only; prints `↗ N` for the focused tab's drifted panes **other than the focused pane** (which `sort` never moves), or nothing; exit 0 always; no `apply_lock`.
  - `heelr back` — if `core.get_last(ws) == "place"`, runs `place.back()`.
  - Pane default for `report` / `tag`: `$HERDR_PANE_ID`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_placement_cli.py
import io
import json
import sys

import pytest

from heelr import cli, core, facts
from test_placement_place import fake  # noqa: F401
from test_placement_facts import make_repo, make_worktree
from test_apply_safety import lock_held


@pytest.fixture
def on(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "cfg/heelr").mkdir(parents=True)
    (tmp_path / "cfg/heelr/placement.toml").write_text('[placement]\nenabled = true\nticket = \'GH-\\d+\'\n')
    return fake


def h(*a):
    return cli.main(["--allow-untested", *a])


def test_report_stores_and_never_moves(on, monkeypatch):
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main", 1)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": "/a", "worktree": "GH-2"})))
    assert h("report", "--pane", p) == 0
    assert facts.load_pane(p)["report"]["worktree"] == "GH-2"
    monkeypatch.setattr(sys, "stdin", io.StringIO("garbage"))
    assert h("report", "--pane", p) == 0


def test_tag_and_clear(on):
    w = on.add_workspace("x")
    t, (p,) = on.add_tab(w, "main", 1)
    h("tag", "GH-7", "--pane", p)
    assert facts.load_pane(p)["tag"] == "GH-7"
    h("tag", "--clear", "--pane", p)
    assert "tag" not in facts.load_pane(p)


def test_drift_status_is_read_only_and_counts_focused_tab(on, tmp_path, capsys):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(wt))
    on.add_tab(w, "GH-1", 1, cwd=str(wt))
    on.focused_tab, on.focused_pane = t1, a
    calls = len(on.calls)
    assert h("drift-status") == 0
    assert capsys.readouterr().out.strip() == "↗ 1"          # b; the focused pane a is not counted
    assert all(c[:2] in {("api", "snapshot")} for c in on.calls[calls:])
    assert not lock_held()


def test_drift_status_never_fails(on, monkeypatch, capsys):
    monkeypatch.setattr(cli.herdr, "snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert h("drift-status") == 0
    assert capsys.readouterr().out == ""


def test_sort_tab_then_back(on, tmp_path):
    r = make_repo(tmp_path / "heelr")
    wt = make_worktree(r, "GH-1", "GH-1")
    w = on.add_workspace("heelr")
    t1, (a, b) = on.add_tab(w, "main", 2, cwd=str(wt))
    on.focused_tab, on.focused_pane = t1, a
    assert h("sort") == 0
    assert "GH-1" in on.labels() and b not in on.panes(t1)
    assert a in on.panes(t1)                     # the focused pane never moves
    assert h("back") == 0
    assert b in on.panes(t1)


def test_pull_with_no_existing_tab_makes_one_tab(on, tmp_path):      # reviewer item 4
    ra, rb = make_repo(tmp_path / "ra"), make_repo(tmp_path / "rb")
    wa, wb = on.add_workspace("ra"), on.add_workspace("rb")
    ta, (a, x) = on.add_tab(wa, "main", 2, cwd=str(tmp_path))
    tb, (b,) = on.add_tab(wb, "x", 1, cwd=str(make_worktree(rb, "GH-6", "GH-6")))
    tc, (c,) = on.add_tab(wa, "y", 1, cwd=str(make_worktree(ra, "GH-6", "GH-6")))
    on.focused_tab, on.focused_pane = ta, a
    assert h("pull", "GH-6") == 0
    assert on.labels().count("GH-6") == 1
    assert not any(l.startswith("GH-6 · ") for l in on.labels())


def test_pull_gathers_task_across_workspaces_and_skips_sleeping(on, tmp_path):
    ra, rb = make_repo(tmp_path / "ra"), make_repo(tmp_path / "rb")
    wa, wb = on.add_workspace("ra"), on.add_workspace("rb")
    ta, (a,) = on.add_tab(wa, "GH-5", 1, cwd=str(make_worktree(ra, "GH-5", "GH-5")))
    tb, (b,) = on.add_tab(wb, "GH-5", 1, cwd=str(make_worktree(rb, "GH-5", "GH-5")))
    tz, (z,) = on.add_tab(wb, core.SLEEP_MARK + "GH-5", 1, cwd=str(tmp_path / "rb/.worktrees/GH-5"))
    on.focused_tab, on.focused_pane = ta, a
    assert h("pull", "GH-5") == 0
    assert len(on.panes(ta)) == 2                # b joined (renumbered), a stayed
    assert z in on.panes(tz)                     # sleeping pane untouched
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 60 python -m pytest tests/test_placement_cli.py -q`
Expected: FAIL (`argument cmd: invalid choice: 'report'`).

- [ ] **Step 3: Implement in `heelr/cli.py`**

```python
def cmd_report(args) -> int:
    pane = args.pane or os.environ.get("HERDR_PANE_ID")
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if pane and isinstance(data, dict):
        flat = dict(data)
        ws = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
        wt = data.get("worktree") if isinstance(data.get("worktree"), dict) else {}
        flat.setdefault("cwd", ws.get("current_dir"))          # Claude status line shape
        if wt:
            flat["worktree"] = wt.get("name")
            flat.setdefault("branch", wt.get("branch"))
        facts.save_report(pane, flat)
    return 0


def cmd_tag(args) -> int:
    pane = args.pane or os.environ.get("HERDR_PANE_ID")
    if not pane:
        raise herdr.HerdrError("no pane: pass --pane or run inside Herdr")
    facts.set_tag(pane, None if args.clear else args.task)
    return 0


def _scope_panes(snap: dict, scope: str) -> set[str]:
    if scope == "all":
        return {p["pane_id"] for p in snap["panes"]}
    if scope == "workspace":
        return {p["pane_id"] for p in snap["panes"] if p["workspace_id"] == snap["focused_workspace_id"]}
    return {p["pane_id"] for p in snap["panes"] if p["tab_id"] == snap["focused_tab_id"]}


def _run_moves(snap, moves, cfg, keys, dry_run, exact=False) -> int:
    if dry_run:
        for m in moves:
            print(f"would move {m.pane} -> {m.dest.workspace} / {m.dest.tab}")
        return 0
    if not moves:
        print("nothing to sort")
        return 0
    undo = place.execute(snap, moves, cfg, key_of=keys, exact=exact)
    place.save_undo(undo)
    core.set_last(snap["focused_workspace_id"], "place")
    print(f"moved {len(undo)} pane(s)")
    return 0


def cmd_sort(args) -> int:
    cfg = keymod.load_config()
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    scope = "all" if args.all else "workspace" if args.workspace else "tab"
    roles = _roles()
    panes = _scope_panes(snap, scope) - {snap.get("focused_pane_id")}
    todo = place.drifted(snap, cfg, roles, panes)
    keys = {sp["pane_id"]: keymod.pane_key(sp, cfg, roles) for sp in snap["panes"] if sp["pane_id"] in todo}
    return _run_moves(snap, [place.Move(p, d, False) for p, d in todo.items()], cfg, keys, args.dry_run)


def cmd_pull(args) -> int:
    cfg = keymod.load_config()
    snap = herdr.snapshot()
    herdr.check_version(snap, args.allow_untested)
    roles = _roles()
    ws_label = {w["workspace_id"]: w.get("label") or "" for w in snap.get("workspaces", [])}
    here = ws_label.get(snap["focused_workspace_id"], "")
    dest = place.Dest(here, args.task)
    skip = sleep_mod.sleeping_panes(snap) | {snap.get("focused_pane_id")}
    keys, moves = {}, []
    for sp in snap["panes"]:
        if sp["pane_id"] in skip:
            continue
        k = keymod.pane_key(sp, cfg, roles)
        tab = next(t for t in snap["tabs"] if t["tab_id"] == sp["tab_id"])
        if k.task == args.task and not (sp["workspace_id"] == snap["focused_workspace_id"]
                                        and core.base_label(tab) == args.task):
            keys[sp["pane_id"]] = k
            moves.append(place.Move(sp["pane_id"], dest, False))
    return _run_moves(snap, moves, cfg, keys, args.dry_run, exact=True)


def cmd_drift_status(args) -> int:
    try:
        cfg = keymod.load_config()
        snap = herdr.snapshot(timeout=1.5)
        n = len(place.drifted(snap, cfg, _roles(), _scope_panes(snap, "tab") - {snap.get("focused_pane_id")}))
        if n:
            print(f"↗ {n}")
    except Exception:
        pass
    return 0
```

Import `from . import facts, sleep as sleep_mod`. In `_cmd_back`, at the top (after reading the workspace id the way it does today), add:

```python
    if core.get_last(ws_id) == "place":
        return place.back()
```

Find the exact spot with `sed -n 151,200p heelr/cli.py`; `ws_id` is whatever `_cmd_back` already computes for the focused workspace.

Register the parsers in `main`:

```python
    p = sub.add_parser("report", help="store facts about a pane (adapters; JSON on stdin)")
    p.add_argument("--pane")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("tag", help="set the pane's task by hand (overrides everything)")
    p.add_argument("task", nargs="?")
    p.add_argument("--pane")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(fn=cmd_tag)
    p = sub.add_parser("sort", help="move drifted panes to their tabs (focused tab by default)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--workspace", action="store_true", help="the whole focused workspace")
    g.add_argument("--all", action="store_true", help="every workspace on the Herdr server")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.set_defaults(fn=cmd_sort)
    p = sub.add_parser("pull", help="gather one task's panes from every workspace into this one")
    p.add_argument("task")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.set_defaults(fn=cmd_pull)
    p = sub.add_parser("drift-status", help="↗ count for the focused tab (for tab_bar_right)")
    p.set_defaults(fn=cmd_drift_status)
```

- [ ] **Step 4: Run all tests**

Run: `timeout 300 python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add heelr/cli.py tests/test_placement_cli.py
git commit -m "placement: report, tag, sort, pull, drift-status, back"
```

---

### Task 8: Adapters: shell hook and agent examples

**Files:**
- Modify: `heelr/cli.py` (`shell-hook` command)
- Create: `examples/adapters/claude-statusline.md`, `examples/adapters/claude-hooks.json`, `examples/adapters/codex-hooks.json`, `examples/adapters/opencode-heelr.js`, `tests/test_placement_adapters.py`

**Interfaces:**
- Produces: `heelr shell-hook {bash,zsh,fish}` prints a snippet for `eval`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_placement_adapters.py
import json
from pathlib import Path

import pytest

from heelr import cli

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("sh", ["bash", "zsh", "fish"])
def test_shell_hook_prints_snippet(sh, capsys):
    assert cli.main(["shell-hook", sh]) == 0
    out = capsys.readouterr().out
    assert "heelr report" in out and "HERDR_PANE_ID" in out


def test_bash_hook_appends_never_replaces(capsys):
    cli.main(["shell-hook", "bash"])
    out = capsys.readouterr().out
    assert 'PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND;}__heelr_hook"' in out
    assert "PROMPT_COMMAND=__heelr_hook" not in out


def test_example_json_parses():
    for f in ("claude-hooks.json", "codex-hooks.json"):
        json.loads((ROOT / "examples/adapters" / f).read_text())
```

Also add to `tests/test_live.py`'s existing bash-safe section, if one exists, nothing; the real-shell coexistence check was done in the spike (docs/spikes/2026-10-05-role-placement-spikes.md item 4).

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 60 python -m pytest tests/test_placement_adapters.py -q`
Expected: FAIL (`invalid choice: 'shell-hook'`).

- [ ] **Step 3: Implement**

In `cli.py`:

```python
SHELL_HOOKS = {
    "bash": r'''__heelr_hook() {
  [ -n "${HERDR_PANE_ID:-}" ] || return
  [ "$PWD" = "${__heelr_last:-}" ] && return
  __heelr_last=$PWD
  (printf '{"cwd":"%s"}' "$PWD" | heelr report --pane "$HERDR_PANE_ID" >/dev/null 2>&1 &)
}
case ";${PROMPT_COMMAND:-};" in *";__heelr_hook;"*) ;; *) PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND;}__heelr_hook";; esac
''',
    "zsh": r'''__heelr_hook() {
  [[ -n "${HERDR_PANE_ID:-}" ]] || return
  (printf '{"cwd":"%s"}' "$PWD" | heelr report --pane "$HERDR_PANE_ID" >/dev/null 2>&1 &)
}
autoload -Uz add-zsh-hook && add-zsh-hook chpwd __heelr_hook
''',
    "fish": r'''function __heelr_hook --on-variable PWD
  set -q HERDR_PANE_ID; or return
  printf '{"cwd":"%s"}' "$PWD" | heelr report --pane "$HERDR_PANE_ID" >/dev/null 2>&1 &
end
''',
}


def cmd_shell_hook(args) -> int:
    print("# heelr: tell heelr when this pane's folder changes. Add AFTER any line that sets PROMPT_COMMAND.")
    print(SHELL_HOOKS[args.shell], end="")
    return 0
```

Parser:

```python
    p = sub.add_parser("shell-hook", help="print a shell snippet that reports folder changes")
    p.add_argument("shell", choices=sorted(SHELL_HOOKS))
    p.set_defaults(fn=cmd_shell_hook)
```

Folders containing `"` or `\` would break the hand-built JSON; build it with `python3 -c` instead if a test with such a folder is added later (Minor, ledger only).

`examples/adapters/claude-statusline.md`:

````markdown
# Claude Code adapter (status line)

Claude Code passes JSON to your status line script after every message. Add one line to your
own script (heelr never takes the `statusLine` setting); `$input` is the JSON your script read:

```sh
[ -n "$HERDR_PANE_ID" ] && (printf '%s' "$input" | heelr report >/dev/null 2>&1 &)
```

heelr reads `workspace.current_dir` and `worktree.name` / `worktree.branch` from it.
````

`examples/adapters/claude-hooks.json` (merge into `~/.claude/settings.json` `hooks`):

```json
{
  "CwdChanged": [{"hooks": [{"type": "command", "command": "[ -n \"$HERDR_PANE_ID\" ] && heelr report || true"}]}]
}
```

No `WorktreeCreate` hook: in Claude Code a `WorktreeCreate` hook replaces the default worktree creation (it must create the worktree and print its path), so a reporting hook there would break `--worktree` and EnterWorktree. `CwdChanged` already fires when Claude enters a worktree. Add a test that the example has no `WorktreeCreate` key:

```python
def test_claude_example_has_no_worktree_create():
    assert "WorktreeCreate" not in json.loads((ROOT / "examples/adapters/claude-hooks.json").read_text())
```

`examples/adapters/codex-hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "[ -n \"$HERDR_PANE_ID\" ] && heelr report || true"}]}]
  }
}
```

`examples/adapters/opencode-heelr.js`:

```js
// opencode plugin: report the session's folder to heelr. Copy to ~/.config/opencode/plugins/.
export const HeelrReport = async ({ $ }) => ({
  event: async ({ event }) => {
    if (!process.env.HERDR_PANE_ID) return
    if (event.type !== "session.created" && event.type !== "session.updated") return
    const dir = event.properties?.info?.directory
    if (!dir) return
    await $`printf '%s' ${JSON.stringify({ cwd: dir, agent: "opencode" })} | heelr report`.quiet().nothrow()
  },
})
```

- [ ] **Step 4: Run all tests**

Run: `timeout 300 python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add heelr/cli.py examples/adapters tests/test_placement_adapters.py
git commit -m "placement: shell hook (bash, zsh, fish) and agent adapter examples"
```

---

### Task 9: Live selftest cases

**Files:**
- Modify: `heelr/selftest.py`, `tests/test_live.py`

**Interfaces:**
- Consumes: the throwaway-server harness in `heelr/selftest.py` (`make_home`, `child_env`, `assert_isolated`); read its existing cases first (`sed -n 140,296p heelr/selftest.py`) and follow their pattern exactly.
- Produces: four live cases run by `heelr selftest` (HEELR_LIVE=1 in `tests/test_live.py`):
  1. `workspace create` and `tab create` reply shapes match what `place.py` reads (`["workspace"]["workspace_id"]`, `["pane"]["pane_id"]`, `["tab"]["tab_id"]`). If not, fix `place.py` and the fake (ledger a ruling).
  2. A new pane in a git worktree folder lands in its `<task>` tab via `heelr auto` with placement enabled (config written into the throwaway HOME).
  3. `heelr pull` across two workspaces, then `heelr back`, restores every pane to its tab (by checking tab membership via `terminal_id`-independent pane counts and labels, since ids change).
  4. The focused pane never moves during `sort --all`.
  5. **Hook budget:** find Herdr's plugin hook timeout (`herdr plugin --help`, the config reference, or a test plugin that sleeps 2/5/10/30 s and logs whether it was killed) and time a real `heelr auto` placement on `pane.created`. If budget > timeout, stop and report before Task 10.

- [ ] **Step 1: Write the live cases** in `tests/test_live.py`, following the existing `@pytest.mark.real_processes` cases (each sets up its own folders under the throwaway HOME: a repo with `.git/HEAD`, `.git/worktrees/GH-1/{HEAD,commondir}`, and `.worktrees/GH-1/.git`).

- [ ] **Step 2: Run them**

Run: `HEELR_LIVE=1 timeout 600 python -m pytest tests/test_live.py -q -k placement`
Expected: PASS. Any failure is a finding about the real API: debug with superpowers:systematic-debugging, never by loosening the assertion.

- [ ] **Step 3: Run the full selftest**

Run: `timeout 600 bin/heelr selftest`
Expected: all cases pass, including the four new ones.

- [ ] **Step 4: Commit**

```bash
git add heelr/selftest.py tests/test_live.py
git commit -m "selftest: placement live cases (create replies, new pane, pull/back, focused pane)"
```

---

### Task 10: Docs, CHANGELOG, version 0.2.0

**Files:**
- Modify: `docs/USER_GUIDE.md`, `README.md`, `CHANGELOG.md`, `heelr/__init__.py`, `plugins/tab-colours/herdr-plugin.toml`

- [ ] **Step 1:** Add a "Placement" section to `docs/USER_GUIDE.md` (after "Roles"): what placement does, `placement.toml` (copy `examples/placement.toml`), `.heelr.toml` per repo, the key and task chain, `sort` / `--workspace` / `--all`, `pull`, `tag`, `back`, the `↗` count (with the `tab_bar_right` config entry below), adapters, and "off by default". Regenerate `docs/user-guide.html` the way the repo does today (`grep -rn "user-guide.html" docs README.md bin` to find how).

`tab_bar_right` entry to document (not added to anyone's live config by this plan; the user decides):

```toml
[[ui.tab_bar_right]]
type = "command"
command = "heelr drift-status"
interval_seconds = 30
```

Check the exact shape against the backup `config.toml.bak-sleeplabel-*` the reviewer cited before writing it.

- [ ] **Step 2:** README: one paragraph under the feature list: "Placement: panes go to the workspace and tab their repo and task belong to."

- [ ] **Step 3:** Bump to `0.2.0` in `heelr/__init__.py` and the tab-colours manifest; add to `CHANGELOG.md` above 0.1.0:

```markdown
## 0.2.0

- Placement (off by default): new panes go to the workspace and tab of their (repo, task); `sort`,
  `pull`, `tag`, `report`, `drift-status`, `shell-hook`; adapter examples for Claude Code, Codex,
  opencode.
- `apply`, `auto`, `back`, `promote`, `nudge` and `save --tabs` now also take the shared
  `<socket>.layout.lock` that other Herdr layout plugins use (`sleep` and `wake` don't: they don't
  reshape layouts).
- `heelr --version`.
```

- [ ] **Step 4: Run all tests**

Run: `timeout 300 python -m pytest -q`
Expected: all pass (test_version checks manifest and CHANGELOG match 0.2.0).

- [ ] **Step 5: Commit**

```bash
git add docs README.md CHANGELOG.md heelr/__init__.py plugins/tab-colours/herdr-plugin.toml
git commit -m "docs: placement guide; 0.2.0"
```

---

## After the last task

- Whole-branch review (heelr-reviewer, per the repo's review rule), then the user decides on landing.
- Tag `v0.2.0` on main only after landing, with the user's OK.
- Live rollout is separate and needs the user's OK: creating `~/.config/heelr/placement.toml`, the `tab_bar_right` entry, and the shell hook line in `~/.bashrc`.
