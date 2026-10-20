#!/usr/bin/env python3
"""Regenerate the README screenshots in docs/images/.

    python3 tools/screenshots/shots.py [--out DIR] [--keep-full] [scene ...]

Needs Xvfb, xwd, kitty (with kitten) and herdr on PATH, and Python 3.11+ (stdlib only). Run it from
a checkout. It never touches your Herdr session or your display: it starts a private X server (:77),
a kitty window on it and a throwaway Herdr server with its own HOME and socket, stages made-up repos
(acme-api, web-ui, docs-site) with stand-in agents (a script named `claude` that only draws a prompt
box; no real agent runs), drives heelr against it, captures only that display with xwd, crops and
labels the captures, and tears everything down. kitty reads your kitty.conf, so the shots use your
terminal font and colours. stage.py has the isolation rules. Scenes: placement, layouts, carousel
(hero.png and carousel.png), current, marks (sidebar.png).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pnglib  # noqa: E402
import stage  # noqa: E402
from stage import Stage, sleep  # noqa: E402

REPO = stage.REPO
sys.path.insert(0, str(REPO / "plugins" / "tab-colours"))
import tab_colours  # noqa: E402

CONFIG = """onboarding = false

[update]
manifest_check = false
version_check = false

[ui]
status_indicators = "symbols"

[ui.sound]
enabled = false

[ui.toast]
delivery = "herdr"

{sidebar}
[theme]
name = "gruvbox"
auto_switch = false

[[keys.command]]
key = "prefix+m"
type = "popup"
command = "heelr pick"
width = 96
height = 16
"""
PLACEMENT = "[placement]\nenabled = true\n"


# ------------------------------------------------------------------ demo content

def make_repo(home: Path, name: str, branch: str = "main", worktrees=()) -> dict[str, Path]:
    """A fake git checkout (just the files heelr reads): src/<name> on `branch`, plus one linked
    worktree src/<name>-<task> per entry of `worktrees`. Returns {task or 'main': folder}."""
    root = home / "src" / name
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "main").write_text("0" * 40 + "\n")
    (git / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    out = {branch: root}
    for f in ("README.md", "pyproject.toml", "src", "tests"):
        (root / f).mkdir() if f in ("src", "tests") else (root / f).write_text("")
    for task in worktrees:
        gd = git / "worktrees" / task
        gd.mkdir(parents=True)
        (gd / "HEAD").write_text(f"ref: refs/heads/{task}\n")
        (gd / "commondir").write_text("../..\n")
        wt = home / "wt" / task
        wt.mkdir(parents=True)
        for f in ("README.md", "pyproject.toml"):
            (wt / f).write_text("")
        (wt / "src").mkdir()
        (wt / "tests").mkdir()
        (wt / ".git").write_text(f"gitdir: {gd}\n")
        out[task] = wt
    return out


def demo_repos(home: Path) -> dict[str, Path]:
    """Folders keyed by 'repo/task'."""
    folders = {}
    for repo, branch, wts in (("acme-api", "main", ("auth-refresh", "billing-export")),
                              ("web-ui", "dark-mode", ()), ("docs-site", "main", ())):
        for task, path in make_repo(home, repo, branch, wts).items():
            folders[f"{repo}/{task}"] = path
    return folders


class Scene:
    """A staged Herdr session: helpers to build workspaces, tabs and panes."""

    def __init__(self, st: Stage, size=(1400, 800)):
        self.st = st
        self.folders = demo_repos(st.home)
        (st.home / ".sudo_as_admin_successful").write_text("")

    def jr(self, *args) -> dict:
        return json.loads(self.st.herdr(*args).stdout)["result"]

    def workspace(self, label: str, folder: Path) -> tuple[str, str, str]:
        r = self.jr("workspace", "create", "--label", label, "--cwd", str(folder), "--no-focus")
        return r["workspace"]["workspace_id"], r["tab"]["tab_id"], r["root_pane"]["pane_id"]

    def tab(self, ws: str, label: str, folder: Path) -> tuple[str, str]:
        r = self.jr("tab", "create", "--workspace", ws, "--label", label, "--cwd", str(folder), "--no-focus")
        return r["tab"]["tab_id"], r["root_pane"]["pane_id"]

    def split(self, pane: str, direction: str, folder: Path | None = None, focus: bool = False) -> str:
        args = ["pane", "split", pane, "--direction", direction, "--focus" if focus else "--no-focus"]
        if folder:
            args += ["--cwd", str(folder)]
        return self.jr(*args)["pane"]["pane_id"]

    def run(self, pane: str, cmd: str):
        self.st.herdr("pane", "run", pane, cmd)

    def agent(self, pane: str, title: str):
        self.run(pane, f"clear; claude '{title}'")

    def ws_by_label(self, label: str) -> str:
        return next(w["workspace_id"] for w in self.st.snap()["workspaces"] if w["label"] == label)

    def settle(self, s: float = 1.5):
        sleep(s)

    def tab_colours(self):
        """Run the heelr tab-colours plugin's reconcile once, as its hooks do."""
        import subprocess
        subprocess.run([sys.executable, str(REPO / "plugins" / "tab-colours" / "tab_colours.py")],
                       env=self.st.env, check=True, capture_output=True, timeout=60)

    def mark(self):
        self.st.heelr("drift-mark")


# ------------------------------------------------------------------ scenes

def scene_placement(out: Path, keep_full: bool):
    with Stage(900, 480, 12.0) as st:
        st.start_xvfb()
        st.start_server(CONFIG.format(sidebar=tab_colours.sidebar_toml([])))
        sc = Scene(st)
        (st.home / ".config" / "heelr").mkdir(parents=True, exist_ok=True)
        (st.home / ".config" / "heelr" / "placement.toml").write_text(PLACEMENT)
        f = sc.folders
        ws, tab, shell = sc.workspace("docs-site", f["docs-site/main"])
        st.herdr("tab", "rename", tab, "main")
        a1 = sc.split(shell, "right", f["acme-api/auth-refresh"])
        a2 = sc.split(a1, "down", f["acme-api/billing-export"])
        a3 = sc.split(shell, "down", f["web-ui/dark-mode"])
        st.herdr("workspace", "focus", ws)
        st.start_kitty()
        sleep(2.5)
        sc.agent(a1, "refresh auth tokens")
        sc.agent(a2, "export billing csv")
        sc.agent(a3, "dark mode palette")
        sleep(2)
        sc.tab_colours()
        sc.mark()
        sleep(1.5)
        before = st.grab("before")
        st.heelr("sort", "--all")
        sleep(2)
        st.herdr("workspace", "focus", sc.ws_by_label("acme-api"))
        sc.tab_colours()
        sc.mark()
        sleep(1.5)
        after = st.grab("after")
        save(out, "placement.png", before_after(before, after))
        full(out, keep_full, before=before, after=after)


def before_after(before, after, crop=None, stack="v"):
    """One image from two captures (cropped to crop=(x, y, w, h)), each with a small label."""
    parts = []
    for img, text in ((before, "before"), (after, "after")):
        if crop:
            img = pnglib.crop(img, *crop)
        img = pnglib.label(img, text, x=img[0] - 8 * 6 * 2 - 24, y=img[1] - 7 * 2 - 24 - 6)
        parts.append(img)
    return (pnglib.vstack if stack == "v" else pnglib.hstack)(*parts, gap=10, colour=(40, 38, 36))


def full(out: Path, keep: bool, **imgs):
    """With --keep-full, also write the uncropped captures to <out>/full/ (not for committing)."""
    if keep:
        (out / "full").mkdir(exist_ok=True)
        for name, img in imgs.items():
            pnglib.write_png(img, str(out / "full" / f"{name}.png"))


def save(out: Path, name: str, img, full=None):
    pnglib.write_png(img, str(out / name))
    print(f"  {name}: {img[0]}x{img[1]}, {(out / name).stat().st_size // 1024} KB")


def work_tab(sc: Scene, root: str, folder: Path, last: str = "agent1") -> dict[str, str]:
    """Four panes in a 2x2 grid in the tab whose first pane is `root`: a shell, two agents and a log
    tail, deliberately not in the shape the layout scenes want. Returns the panes by name."""
    st = sc.st
    log = st.home / "logs" / "api.log"
    log.parent.mkdir(exist_ok=True)
    log.write_text("".join(f"{m}\n" for m in (
        "GET /session 200", "POST /refresh 200", "GET /invoices 200", "GET /session 200",
        "POST /refresh 401", "GET /healthz 200", "GET /invoices 200", "POST /refresh 200")))
    other = "shell" if last == "agent1" else "agent1"
    p = {other: root}
    p["agent2"] = sc.split(root, "right", folder)
    p["logs"] = sc.split(p["agent2"], "down", folder)
    p[last] = sc.split(root, "down", folder, focus=True)    # the focused pane is the layouts' main
    return p


def start_work(sc: Scene, p: dict[str, str]):
    sc.run(p["shell"], "clear; ls")
    sc.agent(p["agent1"], "refresh auth tokens")
    sc.agent(p["agent2"], "add token tests")
    sc.run(p["logs"], f"clear; tail -f ~/logs/api.log")
    sleep(2)


def scene_layouts(out: Path, keep_full: bool):
    """Before / after `heelr apply main-left` on one tab; cropped to the panes."""
    with Stage(900, 480, 12.0) as st:
        st.start_xvfb()
        st.start_server(CONFIG.format(sidebar=tab_colours.sidebar_toml([])))
        sc = Scene(st)
        ws, tab, root = sc.workspace("acme-api", sc.folders["acme-api/auth-refresh"])
        st.herdr("tab", "rename", tab, "auth-refresh")
        p = work_tab(sc, root, sc.folders["acme-api/auth-refresh"])
        st.herdr("workspace", "focus", ws)
        st.start_kitty()
        sleep(2.5)
        start_work(sc, p)
        sc.tab_colours()
        sleep(1)
        before = st.grab("before")
        st.heelr("apply", "main-left", "--tab", tab)
        st.refresh()
        after = st.grab("after")
        save(out, "layouts.png", before_after(before, after, crop=(256, 0, 644, 480)))
        full(out, keep_full, before=before, after=after)


def scene_carousel(out: Path, keep_full: bool):
    """The hero (popup on its first card) and the carousel on a saved layout."""
    with Stage(1100, 480, 12.0) as st:
        st.start_xvfb()
        st.start_server(CONFIG.format(sidebar=tab_colours.sidebar_toml([])))
        sc = Scene(st)
        cfg = st.home / ".config" / "heelr"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "config.toml").write_text('favourites = ["main-left", "grid", "main-top", "review-desk"]\n')
        ws, tab, root = sc.workspace("acme-api", sc.folders["acme-api/auth-refresh"])
        st.herdr("tab", "rename", tab, "auth-refresh")
        p = work_tab(sc, root, sc.folders["acme-api/auth-refresh"])
        st.herdr("workspace", "focus", ws)
        st.start_kitty()
        sleep(2.5)
        start_work(sc, p)
        # a saved layout for the carousel: arrange a scratch workspace, `heelr save`, close it
        sws, stab, sroot = sc.workspace("scratch", st.home)
        sp = work_tab(sc, sroot, st.home)
        st.heelr("apply", "agents-logs", "--tab", stab)
        st.heelr("save", "review-desk", "--tab", stab)
        st.herdr("workspace", "close", sws)
        sc.tab_colours()
        sleep(1)
        st.keys("ctrl+b", "m")
        sleep(2)
        hero = st.grab("hero")
        save(out, "hero.png", hero)
        for _ in range(3):
            st.kitten("send-text", "l")
            sleep(0.5)
        sleep(1)
        card = st.grab("card")
        save(out, "carousel.png", pnglib.crop(card, 258, 72, 842, 350))
        full(out, keep_full, hero=hero, card=card)


def scene_current(out: Path, keep_full: bool):
    """`heelr current` in the big main pane of a four-pane tab (main-bottom)."""
    with Stage(1100, 800, 12.0) as st:
        st.start_xvfb()
        st.start_server(CONFIG.format(sidebar=tab_colours.sidebar_toml([])))
        sc = Scene(st)
        ws, tab, root = sc.workspace("acme-api", sc.folders["acme-api/auth-refresh"])
        st.herdr("tab", "rename", tab, "auth-refresh")
        p = work_tab(sc, root, sc.folders["acme-api/auth-refresh"], last="shell")
        st.herdr("workspace", "focus", ws)
        st.start_kitty()
        sleep(2.5)
        start_work(sc, p)
        st.heelr("apply", "main-bottom", "--tab", tab)
        st.refresh()
        sc.run(p["shell"], "clear")
        sleep(0.5)
        sc.run(p["shell"], "heelr current")
        sleep(2.5)
        shot = st.grab("current")
        save(out, "current.png", pnglib.crop(shot, 256, 334, 844, 466))
        full(out, keep_full, current=shot)


def scene_marks(out: Path, keep_full: bool):
    """A 💤 tab, a tab with ↗N and coloured tab names in the sidebar."""
    with Stage(900, 520, 12.0) as st:
        st.start_xvfb()
        st.start_server(CONFIG.format(sidebar=tab_colours.sidebar_toml([])))
        sc = Scene(st)
        cfg = st.home / ".config" / "heelr"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "placement.toml").write_text(PLACEMENT)
        f = sc.folders
        sid = "00000000-0000-4000-8000-00000000d3a0"
        proj = st.home / ".claude" / "projects" / "-demo"
        proj.mkdir(parents=True)
        (proj / f"{sid}.jsonl").write_text('{"type":"custom-title","customTitle":"reindex-catalog","sessionId":"%s"}\n' % sid)
        ws, t1, p1 = sc.workspace("acme-api", f["acme-api/auth-refresh"])
        st.herdr("tab", "rename", t1, "auth-refresh")
        t2, p2 = sc.tab(ws, "billing-export", f["acme-api/billing-export"])
        p2b = sc.split(p2, "right", f["web-ui/dark-mode"])
        p2c = sc.split(p2b, "down", f["docs-site/main"])
        t3, p3 = sc.tab(ws, "search-index", f["acme-api/auth-refresh"])
        ws2, t4, p4 = sc.workspace("web-ui", f["web-ui/dark-mode"])
        st.herdr("tab", "rename", t4, "dark-mode")
        ws3, t5, p5 = sc.workspace("docs-site", f["docs-site/main"])
        st.herdr("tab", "rename", t5, "main")
        st.herdr("workspace", "focus", ws)
        st.herdr("tab", "focus", t2)
        st.start_kitty()
        sleep(2.5)
        sc.agent(p1, "refresh auth tokens")
        sc.agent(p2, "export billing csv")
        sc.agent(p4, "dark mode palette")
        sc.agent(p5, "rewrite quickstart")
        sc.run(p3, f"clear; STUB_TITLE='reindex catalog' claude --resume {sid}")
        for _ in range(40):
            if st.pane_status(p3) == "idle":
                break
            sleep(0.5)
        st.heelr("sleep", "--tab", t3)
        sleep(1)
        sc.tab_colours()
        sc.mark()
        sleep(12)                                   # let the "slept 1 agent" toast go
        shot = st.grab("marks")
        save(out, "sidebar.png", shot)
        full(out, keep_full, marks=shot)


SCENES = {"placement": scene_placement, "layouts": scene_layouts, "carousel": scene_carousel,
          "current": scene_current, "marks": scene_marks}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO / "docs" / "images"))
    ap.add_argument("--keep-full", action="store_true", help="also keep the uncropped captures")
    ap.add_argument("scenes", nargs="*", choices=list(SCENES))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in a.scenes or SCENES:
        print(f"scene {name}", flush=True)
        SCENES[name](out, a.keep_full)


if __name__ == "__main__":
    main()
