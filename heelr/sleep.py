"""Tab sleep and wake: end a tab's idle, named Claude agents to free their memory, and
resume them in the same panes later.

Process facts come from /proc and `pane process-info`, never from Herdr's agent flag
alone: Claude's Herdr hook reports only SessionStart, so after an exit the flag is stale.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import core, herdr

DEADLINE = 10.0          # seconds every killed group gets to exit (spike 1: 0.6-5.2 s)
MARK = core.SLEEP_MARK
NO_TRANSCRIPT = object()


# ------------------------------------------------------------------ processes

@dataclass
class Proc:
    pid: int
    ppid: int
    pgid: int
    rss: int                 # bytes
    argv: list[str]
    start: float             # seconds since boot


def _clk() -> int:
    return os.sysconf("SC_CLK_TCK")


def read_procs() -> dict[int, Proc]:
    """Every process this user can see, from /proc."""
    page, clk, out = os.sysconf("SC_PAGE_SIZE"), _clk(), {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = Path(f"/proc/{d}/stat").read_text()
            argv = Path(f"/proc/{d}/cmdline").read_bytes().split(b"\0")
            rss = int(Path(f"/proc/{d}/statm").read_text().split()[1]) * page
        except (OSError, ValueError, IndexError):
            continue  # gone, or not ours to read
        st = parse_stat(stat)
        if st is None:
            continue
        out[int(d)] = Proc(pid=int(d), ppid=st[0], pgid=st[1], rss=rss,
                           argv=[a.decode(errors="replace") for a in argv if a], start=st[2] / clk)
    return out


def parse_stat(stat: str) -> tuple[int, int, int] | None:
    """(ppid, pgid, start ticks) from /proc/<pid>/stat; None for a zombie (exited, not yet
    reaped: not running anything)."""
    rest = stat[stat.rindex(")") + 2:].split()  # the name may hold spaces and parens
    if rest[0] == "Z":
        return None
    return int(rest[1]), int(rest[2]), int(rest[19])


def uptime() -> float:
    return float(Path("/proc/uptime").read_text().split()[0])


def is_claude(argv: list[str]) -> bool:
    return bool(argv) and os.path.basename(argv[0]) == "claude"


def descendants(procs: dict[int, Proc], pid: int) -> list[Proc]:
    kids: dict[int, list[Proc]] = {}
    for p in procs.values():
        kids.setdefault(p.ppid, []).append(p)
    out, todo = [], [pid]
    while todo:
        for k in kids.get(todo.pop(), []):
            out.append(k)
            todo.append(k.pid)
    return out


def tasks(procs: dict[int, Proc], leader: int) -> list[Proc]:
    """Work a Claude is still running: its descendants outside its own process group,
    one per group. MCP servers share Claude's group; each Bash-tool shell (foreground or
    run_in_background) gets its own."""
    seen, out = set(), []
    for p in descendants(procs, leader):
        if p.pgid != leader and p.pgid not in seen:
            seen.add(p.pgid)
            out.append(p)
    return out


def group(procs: dict[int, Proc], pgid: int) -> list[Proc]:
    return [p for p in procs.values() if p.pgid == pgid]


def resume_arg(argv: list[str]) -> str | None:
    """The token after --resume / -r (or --resume=<x>), if any."""
    for i, a in enumerate(argv):
        if a in ("--resume", "-r") and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--resume="):
            return a.split("=", 1)[1]
    return None


def _age(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def _mib(n: int) -> str:
    return f"{n / 2**30:.1f} GiB" if n >= 2**30 else f"{n / 2**20:.0f} MiB"


# ------------------------------------------------------------------ session names

def projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return Path(base) / "projects"


def session_name(sid: str):
    """The user-set name (the last `custom-title` record) of a session; None if it has
    none, NO_TRANSCRIPT if no transcript was found."""
    files = sorted(projects_dir().glob(f"*/{sid}.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        return NO_TRANSCRIPT
    name = None
    with open(files[-1], encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"custom-title"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "custom-title" and isinstance(rec.get("customTitle"), str):
                name = rec["customTitle"].strip() or name
    return name


# ------------------------------------------------------------------ panes

@dataclass
class Pane:
    id: str
    snap: dict
    info: dict               # pane process-info
    kind: str = "shell"      # agent | stale | busy | shell
    leader: int | None = None

    @property
    def short(self) -> str:
        return self.id.split(":")[-1]

    @property
    def title(self) -> str:
        return self.snap.get("terminal_title_stripped") or ""

    @property
    def session(self) -> str | None:
        s = self.snap.get("agent_session") or {}
        return s.get("value") if isinstance(s, dict) else None

    @property
    def shell(self) -> int | None:
        return self.info.get("shell_pid")

    def fg_desc(self) -> str:
        procs = self.info.get("foreground_processes") or []
        lead = [p for p in procs if p.get("pid") == self.info.get("foreground_process_group_id")]
        p = (lead or procs or [{}])[0]
        return p.get("cmdline") or p.get("name") or "?"


def classify(snap_pane: dict, info: dict) -> Pane:
    p = Pane(snap_pane["pane_id"], snap_pane, info)
    fg = info.get("foreground_process_group_id")
    lead = [x for x in info.get("foreground_processes") or [] if x.get("pid") == fg]
    if fg is not None and fg != p.shell and lead and is_claude(lead[0].get("argv") or [lead[0].get("name", "")]):
        p.kind, p.leader = "agent", fg
    elif fg is not None and fg != p.shell:
        p.kind = "busy"      # something else in the foreground, whatever Herdr's flag says
    elif snap_pane.get("agent") == "claude":
        p.kind = "stale"
    return p


def process_info(pane_id: str) -> dict:
    return herdr.call("pane", "process-info", "--pane", pane_id)["process_info"]


def read_tab_panes(snap: dict, tab: str) -> list[Pane]:
    return [classify(sp, process_info(sp["pane_id"])) for sp in snap["panes"] if sp["tab_id"] == tab]


def send(*args: str) -> None:
    """A herdr request that prints no JSON (send-text / send-keys)."""
    proc = subprocess.run([herdr.herdr_bin(), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise herdr.HerdrError(f"herdr {' '.join(args[:3])} -> rc={proc.returncode}: "
                               f"{(proc.stderr or proc.stdout).strip()[:300]}")


def label_tab(rec: dict) -> None:
    """💤 before the tab's label while anything in it sleeps; the old label back once nothing
    does, unless it was renamed meanwhile. Best effort: a failed rename changes nothing else.
    (tab-autoname skips a label it didn't set, and takes the tab back once it's restored.)"""
    try:
        tabs = herdr.snapshot().get("tabs") or []
        cur = next((t.get("label") for t in tabs if t.get("tab_id") == rec["tab"]), None)
        if cur is None:
            return
        if any(e.get("status") in ("slept", "pending") for e in rec["panes"]):
            if not cur.startswith(MARK):
                rec["label"] = core.strip_drift(cur)          # the name, without a drift mark
                herdr.call("tab", "rename", rec["tab"], MARK + cur)
        elif isinstance(rec.get("label"), str) and core.strip_drift(cur) == MARK + rec["label"]:
            herdr.call("tab", "rename", rec["tab"], rec["label"] + cur[len(core.strip_drift(cur)):])
    except herdr.HerdrError:
        pass


# ------------------------------------------------------------------ the record

def record_path(tab: str) -> Path:
    return core.state_dir() / "sleep" / (re.sub(r"[^A-Za-z0-9_-]", "_", tab) + ".json")


def load_record(tab: str) -> dict:
    try:
        rec = json.loads(record_path(tab).read_text())
    except (OSError, ValueError):
        return {"tab": tab, "panes": []}
    if not isinstance(rec, dict) or not isinstance(rec.get("panes"), list):
        return {"tab": tab, "panes": []}
    rec["panes"] = [e for e in rec["panes"] if isinstance(e, dict) and isinstance(e.get("pane"), str)
                    and isinstance(e.get("session"), str)]
    return rec


def save_record(rec: dict) -> None:
    """Rewritten after every change; deleted once nothing in it is slept."""
    p = record_path(rec["tab"])
    if not any(e.get("status") in ("slept", "pending") for e in rec["panes"]):
        p.unlink(missing_ok=True)
        return
    core.write_atomic(p, json.dumps(rec, indent=1, ensure_ascii=False))


def slept(rec: dict) -> list[dict]:
    return [e for e in rec["panes"] if e.get("status") == "slept"]


def sleeping_panes(snap: dict) -> set[str]:
    """Panes placement must leave alone: in a sleeping tab, or slept in any record."""
    asleep = {t["tab_id"] for t in snap["tabs"] if (t.get("label") or "").startswith(core.SLEEP_MARK)}
    out = {p["pane_id"] for p in snap["panes"] if p["tab_id"] in asleep}
    try:
        files = list((core.state_dir() / "sleep").glob("*.json"))
    except OSError:
        files = []
    for f in files:
        try:
            rec = json.loads(f.read_text())
            out |= {e["pane"] for e in slept({"panes": [x for x in rec["panes"] if isinstance(x, dict)]})
                    if isinstance(e.get("pane"), str)}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


# ------------------------------------------------------------------ sleep

@dataclass
class Plan:
    agents: list[Pane] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # stale / left running


def check(panes: list[Pane], procs: dict[int, Proc], now: float, names: dict) -> Plan:
    plan = Plan()
    for p in panes:
        label = f"{p.short} '{p.title}'" if p.title else p.short
        if p.kind == "stale":
            plan.notes.append(f"{p.short}: already asleep or stale")
            continue
        if p.kind == "busy":
            plan.notes.append(f"left running: {p.short} ({p.fg_desc()[:60]})")
            continue
        if p.kind != "agent":
            continue
        plan.agents.append(p)
        sid = p.session
        if not sid:
            plan.blocks.append(f"{label} has no session id Herdr knows (nothing to resume)")
            continue
        name = names.setdefault(sid, session_name(sid))
        if name is NO_TRANSCRIPT:
            plan.blocks.append(f"{label} is unnamed (no transcript found)")
        elif not name:
            plan.blocks.append(f"{label} is not a named session (/rename it, then retry)")
        if p.snap.get("agent_status") != "idle":
            plan.blocks.append(f"{label} is {p.snap.get('agent_status') or 'not idle'}")
        for t in tasks(procs, p.leader):
            what = os.path.basename(t.argv[0]) if t.argv else "?"
            plan.blocks.append(f"{label} has a running task: {what} (running {_age(now - t.start)})")
    return plan


def do_sleep(tab: str, *, read_procs=None, killpg=None, clock=time.monotonic, pause=time.sleep,
             now=None) -> tuple[bool, str]:
    """(ok, message). Holds the apply lock throughout."""
    read_procs = read_procs or globals()["read_procs"]
    killpg, now = killpg or os.killpg, now or uptime
    with core.apply_lock(layout=False):
        snap = herdr.snapshot()
        panes = read_tab_panes(snap, tab)
        procs = read_procs()
        names: dict = {}
        plan = check(panes, procs, now(), names)
        if plan.blocks:
            return False, "can't sleep this tab:\n  " + "\n  ".join(plan.blocks)
        if not plan.agents:
            msg = "no agents to sleep in this tab"
            return True, "; ".join([msg, *plan.notes])

        rec = load_record(tab)
        resolve_pending(rec, procs)
        rec["panes"] = [e for e in rec["panes"] if e.get("status") == "slept"]  # failed: re-checked
        rec.setdefault("slept_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        # re-read every pane right before the kill: same claude, still idle, still no task
        snap2 = {sp["pane_id"]: sp for sp in herdr.snapshot()["panes"]}
        procs = read_procs()
        go, skipped = [], []
        for p in plan.agents:
            now_p = classify(snap2.get(p.id, {"pane_id": p.id}), process_info(p.id)) if p.id in snap2 else None
            if (now_p is None or now_p.kind != "agent" or now_p.leader != p.leader
                    or now_p.snap.get("agent_status") != "idle" or tasks(procs, p.leader)):
                skipped.append(f"{p.short} changed; left awake")
                continue
            shell_pgid = procs[p.shell].pgid if p.shell in procs else p.shell
            if p.leader == shell_pgid or p.leader not in procs or not is_claude(procs[p.leader].argv):
                skipped.append(f"{p.short}: not a claude process group; left awake")
                continue
            lead = [x for x in p.info.get("foreground_processes") or [] if x.get("pid") == p.leader]
            entry = {"pane": p.id, "session": p.session, "name": names.get(p.session),
                     "cwd": (lead[0].get("cwd") if lead else None) or p.snap.get("foreground_cwd")
                     or p.snap.get("cwd") or "",
                     "status": "pending", "pgid": p.leader,
                     "rss": sum(x.rss for x in group(procs, p.leader))}
            # a newer sleep of the same pane or session replaces the old entry
            rec["panes"] = [e for e in rec["panes"] if e["pane"] != p.id and e["session"] != p.session]
            rec["panes"].append(entry)
            go.append((p, entry))
        save_record(rec)

        freed, n, failed, errors = 0, 0, [], {}
        waiting = {p.leader for p, _ in go}
        try:
            for p, entry in go:
                try:
                    killpg(p.leader, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except OSError as e:
                    errors[p.leader] = e
            waiting -= set(errors)
            deadline = clock() + DEADLINE
            while waiting:
                waiting &= {x.pgid for x in read_procs().values()}
                if not waiting or clock() >= deadline:
                    break
                pause(0.1)
        finally:
            # every pending entry ends slept (group gone) or failed, even on an exception
            try:
                alive = {x.pgid for x in read_procs().values()}
            except Exception:
                alive = {p.leader for p, _ in go}
            for p, entry in go:
                if p.leader in errors:
                    entry["status"] = "failed"
                    failed.append(f"{p.short}: can't signal it ({errors[p.leader].strerror}); left as is")
                elif p.leader in alive:
                    entry["status"] = "failed"
                    failed.append(f"{p.short} still running after {DEADLINE:g}s; left as is")
                else:
                    entry["status"] = "slept"
                    freed += entry["rss"]
                    n += 1
            label_tab(rec)
            save_record(rec)

    parts = [f"slept {n} agent{'s' if n != 1 else ''}, freed {_mib(freed)}"]
    if n:
        parts.append("background subagents, if any, were stopped")
    return not failed and not skipped, "; ".join(parts + failed + skipped + plan.notes)


# ------------------------------------------------------------------ wake

def running_sessions(snap: dict, procs: dict[int, Proc]) -> tuple[set[str], set[str]]:
    """(session ids, resume tokens) that are live: a Herdr pane whose session is the id and
    whose foreground holds a live claude, or any claude's --resume/-r token."""
    ids, tokens = set(), set()
    for sp in snap["panes"]:
        s = sp.get("agent_session") or {}
        sid = s.get("value") if isinstance(s, dict) else None
        if sid and sp.get("agent") == "claude":
            try:
                if classify(sp, process_info(sp["pane_id"])).kind == "agent":
                    ids.add(sid)
            except herdr.HerdrError:
                pass
    for p in procs.values():
        if is_claude(p.argv) and (tok := resume_arg(p.argv)):
            tokens.add(tok)
    return ids, tokens


def do_wake(tab: str, *, read_procs=None, typer=None) -> tuple[bool, str]:
    read_procs, typer = read_procs or globals()["read_procs"], typer or _type
    with core.apply_lock(layout=False):
        procs = read_procs()
        rec = load_record(tab)
        resolve_pending(rec, procs)
        todo = slept(rec)
        if not todo:
            label_tab(rec)
            save_record(rec)
            return True, "nothing asleep in this tab"
        snap = herdr.snapshot()
        here = {sp["pane_id"]: sp for sp in snap["panes"]}
        ids, tokens = running_sessions(snap, procs)
        woke, notes, keep, typed_panes = 0, [], False, set()
        for e in todo:
            name = e.get("name") or e["session"]
            sp = here.get(e["pane"])
            short = e["pane"].split(":")[-1]
            if sp is None:
                notes.append(f"{short} is gone; resume '{name}' with `claude --resume {e['session']}`")
                e["status"] = "gone"
            elif e["session"] in ids or e["session"] in tokens:
                notes.append(f"{short} '{name}' is already running")
                e["status"] = "running"
            elif e.get("name") and e["name"] in tokens:
                notes.append(f"{short}: a session named '{name}' is running; if it isn't this one, "
                             f"resume it by hand with `claude --resume {e['session']}`")
                e["status"] = "running"
            elif e["pane"] in typed_panes:
                notes.append(f"{short}: already resuming another session there; "
                             f"resume '{name}' by hand with `claude --resume {e['session']}`")
                e["status"] = "running"
            else:
                p = classify(sp, process_info(e["pane"]))
                if p.kind in ("agent", "busy"):
                    notes.append(f"{short} busy; retry wake later")
                    keep = True
                    continue
                typer(e["pane"], f"cd -- {shlex.quote(e.get('cwd') or '.')} && "
                                 f"claude --resume {shlex.quote(e['session'])}")
                e["status"] = "woke"
                woke += 1
                ids.add(e["session"])
                typed_panes.add(e["pane"])
            save_record(rec)
        rec["panes"] = [x for x in rec["panes"] if x.get("status") == "slept"]
        label_tab(rec)
        save_record(rec)
    msg = f"woke {woke} agent{'s' if woke != 1 else ''}"
    return not keep, "; ".join([msg, *notes])


def _type(pane: str, command: str) -> None:
    send("pane", "send-keys", pane, "ctrl+u")
    send("pane", "send-text", pane, command)
    send("pane", "send-keys", pane, "enter")


def asleep(tab: str) -> bool:
    """Has slept (or unresolved pending) panes. Read outside the lock by the toggle; the
    command it picks re-reads under the lock."""
    return any(e.get("status") in ("slept", "pending") for e in load_record(tab)["panes"])


def resolve_pending(rec: dict, procs: dict[int, Proc]) -> None:
    """A `pending` entry left by an interrupted sleep: slept if its group is gone, else failed."""
    # a pgid reused since the crash would make a slept entry look failed: acceptable, since
    # failed entries are re-checked on the next sleep and never typed into by wake
    alive = {p.pgid for p in procs.values()}
    for e in rec["panes"]:
        if e.get("status") == "pending":
            e["status"] = "failed" if e.get("pgid") in alive else "slept"
