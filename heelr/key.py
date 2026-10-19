"""A pane's placement key (role, repo, task): config, the task chain, ticket
extraction and the optional classify script."""
from __future__ import annotations

import hashlib
import json
import re
import os
import select
import signal
import string
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import core, facts, herdr
from .assign import PaneInfo, classify
from .layouts import config_dir

UNSORTED = "unsorted"
CLASSIFY_MAX = 64 * 1024
REPORT_CWD_FRESH = 600.0     # a reported folder older than this no longer beats the live one


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
    """Defaults (placement off) for a missing, unparsable or wrongly typed file."""
    p = path or (config_dir() / "placement.toml")
    try:
        with open(p, "rb") as f:
            d = tomllib.load(f)
        pl = d.get("placement", {})
        shape = pl.get("shape", {})
        by_tab = shape.get("by_tab", {})
        if not isinstance(by_tab, dict) or not all(isinstance(v, str) for v in by_tab.values()):
            return PlacementConfig()
        return PlacementConfig(
            enabled=pl.get("enabled") is True,
            workspace=_template(pl.get("workspace"), "{repo}"), tab=_template(pl.get("tab"), "{task}"),
            ticket=_str(pl["ticket"]) if pl.get("ticket") else None,
            create_workspaces=pl.get("create_workspaces", True) is not False,
            classify=_str(pl["classify"]) if pl.get("classify") else None,
            shape_default=_str(shape.get("default", "agents-logs")), shape_by_tab=dict(by_tab),
            burst_window=_window(_num(pl.get("burst_window", 0.75))))
    except Exception:                # a hostile file (deep nesting: RecursionError) is just a bad file
        return PlacementConfig()


def _template(v, default: str) -> str:
    """A workspace/tab template may use only {repo} and {task}; anything else (an empty one too)
    is the default."""
    if v is None:
        return default
    if not _str(v).strip():                      # wrong type: the whole config falls back
        return default
    try:
        parsed = list(string.Formatter().parse(v))
        if any(f not in ("repo", "task") or spec or conv for _, f, spec, conv in parsed if f is not None):
            return default
        v.format(repo="x", task="y")
        return v
    except (ValueError, KeyError, IndexError):
        return default


def _window(v: float) -> float:
    """The hook sleeps this long on every new pane: only 0..5 s is believable (nan/inf/negative: default)."""
    return v if 0.0 <= v <= 5.0 else 0.75


def _str(v) -> str:
    if not isinstance(v, str):
        raise TypeError("expected a string")
    return v


def _num(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError("expected a number")
    return float(v)


def repo_config(root: str | None) -> dict:
    if not root:
        return {}
    try:
        with open(Path(root) / ".heelr.toml", "rb") as f:
            d = tomllib.load(f)
        return {k: _str(d[k]) for k in ("name", "ticket") if k in d}
    except Exception:
        return {}


TICKET_TEXT_MAX = 120
TICKET_PATTERN_MAX = 200      # characters
TICKET_MAX_QUANTIFIERS = 2    # `?` `*` `+` `{m,n}` (min < max), counted anywhere in the pattern
TICKET_MAX_COUNT = 100        # a finite repeat count above this is refused


def _children(av):
    for x in av if isinstance(av, (tuple, list)) else [av]:
        if isinstance(x, (list, tuple)):
            yield from _children(x)
        elif hasattr(x, "data"):               # a sub-pattern
            yield x


_REPEATS = ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")
_FORBIDDEN = ("GROUPREF", "GROUPREF_EXISTS", "ASSERT", "ASSERT_NOT", "ATOMIC_GROUP")


def _ticket_problem(sub, st: dict, in_rep: bool = False) -> str | None:
    """Allowlist walk of a parsed ticket pattern (a repo's .heelr.toml is untrusted): at most
    TICKET_MAX_QUANTIFIERS quantifiers, at most one `|` and not under a quantifier, no quantifier
    inside another, no backreference, lookaround or conditional. Returns why it is refused."""
    from re._constants import MAXREPEAT
    for op, av in sub:
        name = str(op)
        if name in _FORBIDDEN:
            return f"uses {name.lower()}"
        if name in _REPEATS:
            mn, mx, body = av
            if in_rep:
                return "a quantifier inside a quantifier"
            if mx != MAXREPEAT and mx > TICKET_MAX_COUNT or mn > TICKET_MAX_COUNT:
                return "a repeat count over 100"
            if mn < mx:
                st["q"] += 1
                if st["q"] > TICKET_MAX_QUANTIFIERS:
                    return f"more than {TICKET_MAX_QUANTIFIERS} quantifiers"
            why = _ticket_problem(body, st, True)
        elif name == "BRANCH":
            st["b"] += 1
            if st["b"] > 1:
                return "more than one alternation"
            if in_rep:
                return "an alternation under a quantifier"
            why = next(filter(None, (_ticket_problem(br, st, in_rep) for br in av[1])), None)
        else:
            why = next(filter(None, (_ticket_problem(c, st, in_rep) for c in _children(av))), None)
        if why:
            return why
    return None


_ticket_ok: dict[str, bool] = {}


def _safe_ticket(regex: str) -> bool:
    if regex not in _ticket_ok:
        why = None
        try:
            if len(regex) > TICKET_PATTERN_MAX:
                why = f"longer than {TICKET_PATTERN_MAX} characters"
            else:
                import re._parser as sre
                why = _ticket_problem(sre.parse(regex), {"q": 0, "b": 0})
                if why is None:
                    re.compile(regex)
        except BaseException as e:                 # re.error, OverflowError, RecursionError, anything
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            why = f"cannot be parsed ({type(e).__name__})"
        _ticket_ok[regex] = why is None
        if why:
            FAILURES.append(f"ticket pattern {regex[:60]!r} ignored: {why}")
    return _ticket_ok[regex]


def extract_ticket(text: str, regex: str | None) -> str:
    if not regex:
        return text
    text = text[:TICKET_TEXT_MAX]
    try:
        if not _safe_ticket(regex):
            return text
        m = re.search(regex, text)
    except Exception:
        return text
    if not m:
        return text
    return (m.group(1) if m.re.groups else None) or m.group(0)


FAILURES: list[str] = []      # why classify runs failed in this process; the hook, sort and pull report them


def take_failures() -> list[str]:
    out = list(dict.fromkeys(FAILURES))
    FAILURES.clear()
    return out


def _fail(script: str, why: str, cacheable: bool = False) -> tuple[dict, bool]:
    FAILURES.append(f"classify script {script}: {why}")
    return {}, cacheable


def _run_script(script: str, stdin: dict, timeout: float = 1.0) -> tuple[dict, bool]:
    """Run the classify script. Returns (result, cacheable): {} for any failure, and cacheable is
    False for transient ones (spawn error, deadline, output over the cap) so the next call retries.
    The script leads its own process group; on any early exit the whole group is killed.
    Every failure is also noted in FAILURES. Never raises."""
    try:
        payload = json.dumps(stdin).encode()
    except (TypeError, ValueError) as e:
        return _fail(script, f"input not serialisable ({e})")
    deadline = time.monotonic() + timeout
    try:
        p = subprocess.Popen([str(Path(script).expanduser())], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    except (OSError, ValueError) as e:
        return _fail(script, f"could not start ({e})")
    try:
        data, why = _talk(p, payload, deadline)
        if why:
            return _fail(script, why)
        try:
            rc = p.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return _fail(script, "timed out")
    finally:
        _reap(p)
    if rc != 0:
        return _fail(script, f"exited {rc}")         # transient, like a timeout: retried, not cached
    try:
        out = json.loads(data)
    except ValueError:
        return _fail(script, "output is not JSON", True)
    if not isinstance(out, dict):
        return _fail(script, "output is not a JSON object", True)
    return {k: v for k, v in out.items() if k in ("role", "repo", "task") and isinstance(v, str) and v}, True


def _talk(p, payload: bytes, deadline: float) -> tuple[bytes, str | None]:
    """Feed stdin and read stdout together (no pipe deadlock) up to the cap and the deadline.
    (output, None) on EOF; (b"", why) when it gave up."""
    try:
        return _talk_loop(p, payload, deadline)
    except (OSError, ValueError) as e:        # a closed fd, select/read/set_blocking failing
        return b"", f"I/O error ({e})"


def _talk_loop(p, payload: bytes, deadline: float) -> tuple[bytes, str | None]:
    rfd, wfd = p.stdout.fileno(), p.stdin.fileno()
    os.set_blocking(wfd, False)
    out, sent, writing = b"", 0, True
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return b"", "timed out"
        r, w, _ = select.select([rfd], [wfd] if writing else [], [], left)
        if w:
            try:
                sent += os.write(wfd, payload[sent:sent + 65536])
            except BlockingIOError:
                pass
            except OSError:          # the script closed its stdin: stop feeding it
                writing = False
            if writing and sent >= len(payload):
                writing = False
            if not writing:
                try:
                    p.stdin.close()
                except OSError:
                    pass
        if r:
            chunk = os.read(rfd, 65536)
            if not chunk:
                return out, None
            out += chunk
            if len(out) > CLASSIFY_MAX:
                return b"", "output over the size cap"


def _reap(p) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    for f in (p.stdin, p.stdout):
        try:
            f.close()
        except OSError:
            pass
    try:
        p.wait(timeout=1.0)
    except subprocess.SubprocessError:
        pass


def run_classify(script: str, stdin: dict, timeout: float = 1.0) -> dict:
    """The script's {"role","repo","task"} answer; {} on any failure."""
    return _run_script(script, stdin, timeout)[0]


def _cache_path() -> Path:
    return core.state_dir() / "classify-cache.json"


def _script_stamp(script: str) -> str:
    """The script's identity for the cache: an edited script must not keep answering from the old run."""
    try:
        st = Path(script).expanduser().stat()
        return f"{Path(script).expanduser()}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return f"{script}:missing"


_deadline: float | None = None      # monotonic; set by drift-status so classify never outruns its budget


def set_deadline(t: float | None) -> None:
    global _deadline
    _deadline = t


def _classify_cached(script: str, pane_id: str, stdin: dict) -> dict:
    try:
        h = hashlib.sha256(json.dumps([_script_stamp(script), stdin], sort_keys=True).encode()).hexdigest()
    except (TypeError, ValueError):
        return _run_script(script, stdin)[0]
    hit = facts._classify_cache().get(pane_id)
    if isinstance(hit, dict) and hit.get("hash") == h and isinstance(hit.get("out"), dict):
        return hit["out"]
    timeout = 1.0
    if _deadline is not None:
        timeout = min(timeout, _deadline - time.monotonic())
        if timeout <= 0.05:
            return {}                           # out of budget: say nothing, run nothing
    out, cacheable = _run_script(script, stdin, timeout)
    if cacheable:
        try:
            with facts.store_lock(min(facts.STORE_WAIT, 0.3)):     # drift-status calls this too: never wait long
                cache = facts._classify_cache()
                cache[pane_id] = {"hash": h, "out": out}
                core.write_atomic(_cache_path(), json.dumps(cache))
        except (herdr.HerdrError, OSError):
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
    rep_cwd = store.get("report", {}).get("cwd")
    try:
        fresh = time.time() - float(store.get("report_at", 0)) < REPORT_CWD_FRESH
    except (TypeError, ValueError):
        fresh = False
    folder = (rep_cwd if fresh else None) or snap_pane.get("foreground_cwd") or snap_pane.get("cwd") or rep_cwd or "/"
    gf = facts.git_facts(folder)
    rc = repo_config(gf.root)
    out = {}
    hint = classify(PaneInfo(pid, agent=snap_pane.get("agent")), roles)      # the role the rules would give
    if cfg.classify:
        out = _classify_cached(cfg.classify, pid, {
            "pane_id": pid, "role_hint": hint, "agent": snap_pane.get("agent"), "agent_status": snap_pane.get("agent_status"),
            "cwd": snap_pane.get("cwd"), "foreground_cwd": snap_pane.get("foreground_cwd"),
            "git": {"root": gf.root, "worktree": gf.worktree, "branch": gf.branch},
            "report": store.get("report", {}), "tag": store.get("tag"), "repo_config": rc})
    role = out.get("role") or hint
    repo = out.get("repo") or rc.get("name") or gf.repo or (Path(folder).name or "/")
    task = out.get("task") or _task(gf, store)
    if task != UNSORTED and "task" not in out:
        task = extract_ticket(task, rc.get("ticket") or cfg.ticket)
    return Key(role, repo, task)
