"""What heelr knows about a pane beyond Herdr's snapshot: git facts read from
the filesystem (no git process), and per-pane reports and tags."""
from __future__ import annotations

import contextlib
import fcntl
import json
import re
import threading
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


READ_MAX = 64 * 1024


def _read(p: Path, limit: int = READ_MAX) -> str | None:
    """A small git file's text. A repo's own files are untrusted: never read more than `limit`."""
    try:
        with open(p, "rb") as f:
            return f.read(limit).decode("utf-8", "replace").strip()
    except OSError:
        return None


def _branch(gitdir: Path) -> str | None:
    head = _read(gitdir / "HEAD") or ""
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


def _default_branch(common: Path) -> str | None:
    head = _read(common / "refs/remotes/origin/HEAD") or ""
    if head.startswith("ref: refs/remotes/origin/"):
        return head[len("ref: refs/remotes/origin/"):]
    packed = (_read(common / "packed-refs", 8 * READ_MAX) or "").splitlines()
    for b in ("main", "master"):
        if (common / "refs/heads" / b).exists() or any(l.endswith(f" refs/heads/{b}") for l in packed):
            return b
    return None


def git_facts(folder: str) -> GitFacts:
    try:
        d = Path(folder).resolve()
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
                    # a normal repo's common dir is <root>/.git; a bare repo or a submodule's
                    # <super>/.git/modules/<name> is itself the root (named after its folder)
                    root = common.parent if common.name == ".git" else common
                    return GitFacts(str(root), root.name, gitdir.name, _branch(gitdir),
                                    _default_branch(common))
                # a submodule (or any other gitdir file): its own repo
                return GitFacts(str(top), top.name, None, _branch(gitdir), _default_branch(gitdir))
    except (OSError, RuntimeError, ValueError):
        return NONE
    return NONE


# ------------------------------------------------------------ per-pane store

STORE_WAIT = 2.0
_store_held = threading.local()


@contextlib.contextmanager
def store_lock(wait: float | None = None):
    """One writer at a time for the pane store, its alias map and the classify cache: a small
    flock of its own, so a prompt-time `heelr report` never waits on a layout change.
    Raises core.LockBusy after `wait` seconds. Re-entrant within a thread."""
    if getattr(_store_held, "depth", 0):
        _store_held.depth += 1
        try:
            yield
        finally:
            _store_held.depth -= 1
        return
    deadline = time.monotonic() + (STORE_WAIT if wait is None else wait)
    with open(core.state_dir() / "panes.lock", "w") as f:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise core.LockBusy("another heelr command is writing pane facts") from None
                time.sleep(0.02)
        _store_held.depth = 1
        try:
            yield
        finally:
            _store_held.depth = 0


def _safe(pane_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", pane_id)


def _pane_path(pane_id: str) -> Path:
    return core.state_dir() / "panes" / (_safe(pane_id) + ".json")


ALIAS_MAX = 300      # a cross-workspace move leaves old id -> new id; the oldest go first
ALIAS_HOPS = 50


def _alias_path() -> Path:
    return core.state_dir() / "pane-alias.json"


def _aliases() -> dict:
    try:
        a = json.loads(_alias_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in a.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(a, dict) else {}


def _save_aliases(a: dict) -> None:
    core.write_atomic(_alias_path(), json.dumps(a, indent=1))


def resolve(pane_id: str) -> str:
    """The pane's current id: a cross-workspace move renumbers it, but the shell inside
    keeps reporting the id it started with. Follows old -> new, never loops."""
    a, seen = _aliases(), {pane_id}
    for _ in range(ALIAS_HOPS):
        nxt = a.get(pane_id)
        if nxt is None or nxt in seen:
            break
        pane_id = nxt
        seen.add(pane_id)
    return pane_id


def drop_alias(pane_id: str) -> None:
    """A new pane took this id: it is no longer anyone's old id."""
    with store_lock():
        a = _aliases()
        if a.pop(pane_id, None) is not None:
            _save_aliases(a)


def load_pane(pane_id: str) -> dict:
    pane_id = resolve(pane_id)
    try:
        rec = json.loads(_pane_path(pane_id).read_text())
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _save_pane(pane_id: str, rec: dict) -> None:
    core.write_atomic(_pane_path(pane_id), json.dumps(rec))


REPORT_FIELDS = ("cwd", "worktree", "branch", "task", "agent", "session_id")


def save_report(pane_id: str, data: dict) -> bool:
    """Store the report; True when it says something different from the last one."""
    with store_lock():
        pane_id = resolve(pane_id)
        rec = load_pane(pane_id)
        new = {k: str(v) for k, v in data.items() if k in REPORT_FIELDS and v is not None}
        changed = rec.get("report") != new
        rec["report"] = new
        rec["report_at"] = time.time()
        _save_pane(pane_id, rec)
        return changed


def set_tag(pane_id: str, task: str | None) -> None:
    with store_lock():
        pane_id = resolve(pane_id)
        rec = load_pane(pane_id)
        if task:
            rec["tag"] = task
        else:
            rec.pop("tag", None)
        _save_pane(pane_id, rec)


def forget(pane_id: str) -> None:
    """The pane is gone: its record, its classify answers, and every old id that pointed at it."""
    with store_lock():
        _pane_path(pane_id).unlink(missing_ok=True)
        cache = _classify_cache()
        if pane_id in cache:
            del cache[pane_id]
            core.write_atomic(core.state_dir() / "classify-cache.json", json.dumps(cache))
        a = before = _aliases()
        a = dict(a)
        dead, changed = {pane_id}, True
        while changed:
            changed = False
            for k, v in list(a.items()):
                if v in dead:
                    del a[k]
                    dead.add(k)
                    changed = True
        if a != before:
            _save_aliases(a)


def _classify_cache() -> dict:
    try:
        c = json.loads((core.state_dir() / "classify-cache.json").read_text())
    except (OSError, ValueError):
        return {}
    return c if isinstance(c, dict) else {}


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
    file (undo records, created lists, tab records, burst events, sleep records, pane store,
    classify cache). Call under core.apply_lock(), so no other heelr change reads or writes
    those files mid-rewrite; the pane store and burst files take their own locks too."""
    from . import burst
    n = 0
    base = core.state_dir()
    with store_lock():
        src = _pane_path(old)
        if src.exists():
            src.replace(_pane_path(new))
            n += 1
        for p in base.rglob("*.json"):
            guard = burst._locked() if p.name == "burst.json" else contextlib.nullcontext()
            with guard:
                try:
                    data = json.loads(p.read_text())
                except (OSError, ValueError):
                    continue
                swapped = _swap(data, old, new)
                if swapped != data:
                    core.write_atomic(p, json.dumps(swapped, indent=1))
                    n += 1
        a = _aliases()
        a.pop(new, None)               # `new` is a live pane id now (also breaks a move-back cycle)
        a.pop(old, None)               # re-added last, so it is the newest
        a[old] = new
        _save_aliases(dict(list(a.items())[-ALIAS_MAX:]))
    return n
