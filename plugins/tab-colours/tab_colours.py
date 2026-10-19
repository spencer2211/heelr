#!/usr/bin/env python3
"""Give every Herdr tab a stable, distinct sidebar colour.

Herdr sidebar rules cannot compute a colour, and a rule only matches its own token's value.
So each tab gets a colour *slot* 1..N, and every pane in the tab reports exactly one custom
token, ``$tabc<slot>``, whose value is the tab's label. The sidebar config lists all N slot
tokens, each with a fixed ``fg``; unreported tokens disappear without a separator, so the
agent row shows the tab name once, in its tab's colour.

    python3 tab_colours.py                          # reconcile now (what the plugin hooks run)
    python3 tab_colours.py sidebar [--pin P=#hex]   # print the matching [ui.sidebar.agents] TOML

Slots are keyed by tab id (stable across live handoff and label changes, e.g. tab-autoname),
with a label memory so a tab that comes back under a new id keeps its colour. A new tab takes
the least-used slot, lowest first, so a handful of tabs get the seven bright accents. Stdlib only.
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# gruvbox dark: the seven bright accents, then three faded ones that stay distinct from them
PALETTE = [
    "#fb4934",  # red
    "#b8bb26",  # green
    "#fabd2f",  # yellow
    "#83a598",  # blue
    "#d3869b",  # purple
    "#8ec07c",  # aqua
    "#fe8019",  # orange
    "#458588",  # faded blue
    "#b16286",  # faded purple
    "#98971a",  # faded green
]
N = len(PALETTE)
PREFIX = "tabc"
TOKEN = re.compile(rf"^{PREFIX}(\d+)$")
PLUGIN_ID = SOURCE = "heelr.tab-colours"
MAX_LABELS = 256
PARK_LABEL = "heelr-park"  # heelr's temporary parking tab: gone again when the apply ends
MAX_VALUE = 80  # Herdr caps token values at 80 characters
HEX = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")


def herdr(*args):
    return subprocess.run([os.environ.get("HERDR_BIN_PATH", "herdr"), *args],
                          capture_output=True, text=True, timeout=10)


def _home():
    # Outside Herdr (run by hand) fall back to the dirs Herdr gives the plugin, so both share state.
    return Path(os.environ.get("HOME", "~")).expanduser()


def state_path():
    d = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if d:
        return Path(d) / "slots.json"
    return _home() / ".local/state/herdr/plugins" / PLUGIN_ID / "slots.json"


def config_path():
    d = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if d:
        return Path(d) / "config.toml"
    return _home() / ".config/herdr/plugins/config" / PLUGIN_ID / "config.toml"


def load_pins(path):
    """[(label prefix, #hex)] from the [pins] table of a TOML file; [] if it does not exist."""
    try:
        import tomllib
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return []
    table = data.get("pins") or {}
    if not isinstance(table, dict):
        raise ValueError(f"{path}: [pins] must be a table of prefix = \"#hex\"")
    pins = []
    for prefix, fg in table.items():
        if not isinstance(fg, str) or not HEX.match(fg):
            raise ValueError(f"{path}: pin {prefix!r} needs a #RGB or #RRGGBB colour, got {fg!r}")
        if prefix:  # "" would match every tab
            pins.append((prefix, fg))
    return pins


def normalise(text):
    """Mirror Herdr's token normalisation so a re-read value compares equal."""
    text = "".join(c for c in text if unicodedata.category(c) != "Cc")
    return text.strip()[:MAX_VALUE].strip()


SLEEP_MARK = "💤 "   # heelr sleep's prefix on a sleeping tab's label: the same tab underneath
DRIFT_MARK = re.compile(r" ↗\d+$")   # heelr placement's suffix: panes here that belong elsewhere


def base_label(tab):
    """The tab's label without heelr's sleep and drift marks."""
    label = tab.get("label")
    label = label.removeprefix(SLEEP_MARK) if isinstance(label, str) else ""
    return DRIFT_MARK.sub("", label) or label


def display_label(tab):
    return normalise(base_label(tab)) or str(tab.get("number", ""))


def usable_tabs(snapshot):
    """The snapshot's tabs that can be coloured: dicts with a string id, except heelr's park tab."""
    return [t for t in snapshot.get("tabs") or []
            if isinstance(t, dict) and isinstance(t.get("tab_id"), str)
            and base_label(t) != PARK_LABEL]


def _valid(s):
    return isinstance(s, int) and not isinstance(s, bool) and 1 <= s <= N


def assign(tabs, state, pins=()):
    """Return ({tab_id: slot}, new_state). Pure.

    A tab whose label starts with a pinned prefix gets slot 0 (coloured by the pin's sidebar
    rule) and takes no colour slot. Auto slots avoid the pinned colours while others are free.
    """
    state = state if isinstance(state, dict) else {}
    old = state.get("tabs") if isinstance(state.get("tabs"), dict) else {}
    labels = dict(state["labels"]) if isinstance(state.get("labels"), dict) else {}
    ids = state.get("ids") if isinstance(state.get("ids"), dict) else {}
    pins = [(p, fg) for p, fg in pins if p]
    pinned_fg = {fg.lower() for _, fg in pins}
    avoid = {i for i, fg in enumerate(PALETTE, 1) if fg.lower() in pinned_fg}
    slots, used = {}, Counter()
    for t in tabs:
        if any(display_label(t).startswith(p) for p, _ in pins):
            slots[t["tab_id"]] = 0
    auto = [t for t in tabs if t["tab_id"] not in slots]

    def take(tid, s):
        slots[tid] = s
        used[s] += 1

    now = {display_label(t): t["tab_id"] for t in auto}

    def reused(t):
        """A cold restart can hand this id to another tab: its old label is now on a different tab,
        and its own label was some other id's. Then the labels, not the id, say which slot is whose."""
        was, lab = ids.get(t["tab_id"]), display_label(t)
        return (isinstance(was, str) and was != lab and now.get(was, t["tab_id"]) != t["tab_id"]
                and any(i != t["tab_id"] and v == lab for i, v in ids.items()))

    for t in auto:  # 1. a tab keeps the slot it had
        s = old.get(t["tab_id"])
        if _valid(s) and not used[s] and not reused(t):
            take(t["tab_id"], s)
    for t in auto:  # 2. a returning label gets its old slot back
        s = labels.get(display_label(t))
        if t["tab_id"] not in slots and _valid(s) and not used[s]:
            take(t["tab_id"], s)
    for t in auto:  # 3. least-used slot, pinned colours last, then lowest (brightest) first
        if t["tab_id"] not in slots:
            take(t["tab_id"], min(range(1, N + 1), key=lambda s: (used[s], s in avoid, s)))
    for t in auto:
        lab = display_label(t)
        labels.pop(lab, None)
        labels[lab] = slots[t["tab_id"]]
    labels = dict(list(labels.items())[-MAX_LABELS:])
    return slots, {"tabs": {t["tab_id"]: slots[t["tab_id"]] for t in auto}, "labels": labels,
                   "ids": {t["tab_id"]: display_label(t) for t in auto}}


def patches(snapshot, slots):
    """[(pane_id, {token: value}, [tokens to clear])] needed to make panes match their tab."""
    labels = {t["tab_id"]: display_label(t) for t in usable_tabs(snapshot)}
    out = []
    for p in snapshot.get("panes", []):
        tid = p.get("tab_id")
        if tid not in slots or tid not in labels:
            continue
        name, value = f"{PREFIX}{slots[tid]}", labels[tid]
        mine = {k: v for k, v in (p.get("tokens") or {}).items() if TOKEN.match(k)}
        put = {} if mine.get(name) == value else {name: value}
        clear = sorted((k for k in mine if k != name), key=lambda k: int(TOKEN.match(k)[1]))
        if put or clear:
            out.append((p["pane_id"], put, clear))
    return out


def _load_state(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def run(runner, path, config=None):
    out = runner("api", "snapshot")
    if out.returncode:
        return
    snapshot = json.loads(out.stdout)["result"]["snapshot"]
    try:
        pins = load_pins(config) if config else []
    except (OSError, ValueError) as e:  # a bad config must not stall every event: colour without pins
        print(f"tab-colours: ignoring pins, bad config: {e}", file=sys.stderr)
        pins = []
    slots, state = assign(usable_tabs(snapshot), _load_state(path), pins)
    for pid, put, clear in patches(snapshot, slots):
        args = ["pane", "report-metadata", pid, "--source", SOURCE]
        for k, v in put.items():
            args += ["--token", f"{k}={v}"]
        for k in clear:
            args += ["--clear-token", k]
        runner(*args)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def coalesced(path, work):
    """Run work() under the lock, collapsing a burst of event hooks.

    Each run first leaves a mark. One that finds the lock busy returns at once: the holder sees the
    mark and reconciles again, from a fresh snapshot. So a burst costs at most two runs, and no hook
    waits. Returns whether this call ran work().
    """
    lock_path, mark = path.with_suffix(".lock"), path.with_suffix(".dirty")
    mark.touch()
    with open(lock_path, "w") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            try:
                while mark.exists():
                    mark.unlink()
                    work()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
            if not mark.exists():  # a mark left between the last check and the unlock
                return True


def sidebar_toml(pins=()):
    lines = [
        "# heelr tab-colours: one $tabc<slot> token per colour slot. The plugin reports exactly one",
        "# of them per pane (value = tab label), so each agent row shows its tab name once, coloured.",
        "# Generated by `python3 tab_colours.py sidebar` (pins from the plugin's config.toml).",
        "[ui.sidebar.agents]",
        "rows = [",
        '  ["state_icon", "terminal_title_stripped"],',
        "  [",
    ]
    if pins:
        rules = ", ".join(f'{{ starts_with = {json.dumps(p)}, fg = "{fg}" }}' for p, fg in pins)
        lines.append(f'    {{ token = "${PREFIX}0", bold = true, rules = [{rules}] }},')
    for i, fg in enumerate(PALETTE, 1):
        lines.append(f'    {{ token = "${PREFIX}{i}", fg = "{fg}", bold = true }},')
    lines += ['    { token = "state_text", dim = true },', "  ],", "]", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tab_colours.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    sb = sub.add_parser("sidebar", help="print the [ui.sidebar.agents] TOML for these slots")
    sb.add_argument("--config", type=Path, default=None,
                    help="plugin config with a [pins] table (default: the plugin's config.toml)")
    args = ap.parse_args(argv)
    if args.cmd == "sidebar":
        sys.stdout.write(sidebar_toml(load_pins(args.config or config_path())))
        return 0
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    coalesced(path, lambda: run(herdr, path, config_path()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
