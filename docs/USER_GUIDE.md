# heelr user guide

heelr rearranges the panes you already have in Herdr into a named layout and
lets you switch between layouts whenever you like. Nothing restarts: your
Claude sessions, shells and log tails keep running and only change where they
sit on screen.

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Keys](#keys)
- [Commands](#commands)
- [Layouts](#layouts)
  - [Rule layouts (presets)](#rule-layouts-presets)
  - [Fixed layouts](#fixed-layouts)
  - [Multi-tab layouts](#multi-tab-layouts)
- [Roles](#roles)
- [Placement](#placement)
- [Saving and undoing](#saving-and-undoing)
- [Configuration and files](#configuration-and-files)
- [Troubleshooting](#troubleshooting)
- [After a Herdr upgrade](#after-a-herdr-upgrade)

## Quick start

From the tab you want to rearrange:

1. Press `prefix+m` (`ctrl+b`, then `m`). A menu opens with a row of layout pictures.
2. Use ←/→ to highlight one and press Enter, or press its key (`1` is the first). The
   tab rearranges and the menu closes.
3. Didn't like it? Press `prefix+u` to undo.
4. Want a different pane in the big slot? Focus it and press `prefix+Enter`. Want the
   big slot bigger? `prefix+=` (and `prefix+-` to shrink it).

The same thing from a shell:

```bash
heelr apply grid -n   # preview the moves, change nothing
heelr apply grid      # do it
heelr back            # undo
```

## How it works

| Term | Meaning |
|---|---|
| **Layout** | A named shape for a tab (or for a whole workspace). Stored as a small TOML file. |
| **Slot** | One position in a layout. Each slot holds one pane. |
| **Main slot** | The slot meant for the pane you're working in. It gets the pane you have focused, if that pane's role fits. |
| **Role** | What a pane is: `agent`, `logs` or `shell`, detected automatically. Layouts can ask for a role in a slot. |
| **Ratio** | How a split divides its space: `0.6` gives the **first** side (left or top) 60%. Sizes are proportions, so a layout works at any window size. |

Applying a layout:

1. Reads the tab from Herdr in one snapshot.
2. Decides which pane goes in which slot (see [Roles](#roles)).
3. Moves panes into place. Herdr can't move a pane to a new spot within the
   same tab, so heelr briefly moves panes to a temporary tab called
   `heelr-park` and then back. That tab closes itself when it's empty, so
   you may see a tab flicker in the tab bar.
4. Re-reads the tab and checks it matches. If it doesn't, it tells you what differs.

**Safety:**
- heelr never closes a pane or stops a process you started. It moves panes,
  and creates new shells only when a layout says `add_missing = true` (or
  `on_mismatch = "fill"`) and has slots with no pane.
- The one thing it closes: `back` closes panes that the undone apply created,
  if the shell is at its prompt or still running only `new_pane_command`. A
  created pane with something else running in it is kept and moved to the
  `overflow` tab. A shell at its prompt counts as unused even if you ran
  commands in it, so see [Saving and undoing](#saving-and-undoing) before
  doing real work in one.
- If a layout already matches, nothing moves.
- Only one apply or `back` runs at a time. A second one started meanwhile
  (say, `prefix+u` pressed twice) is refused and leaves the undo point alone.
- If something fails partway, it stops, nothing is closed, and `heelr back`
  takes you to where you started. Until you do, the `heelr-park` tab stays
  open and every apply in that workspace refuses, so the half-done state
  can't become the undo point.

## Keys

| Key | What it does |
|---|---|
| `prefix+m` | Layout menu: one row of layout thumbnails (shaded = main pane), opening on the tab's current layout. ←/→ (or `h`/`l`) move the highlight and Enter applies it; a layout's key (`1`–`5` are your favourites, then every other layout in order) applies it at once; `b` undoes; `q` or `Esc` cancels. |
| `prefix+u` | Undo the last layout change in this workspace. |
| `prefix+Enter` | Promote: swap the focused pane with the tab's largest pane (ties: the top-left one). Nothing else moves. Already the largest: a notification says so. `prefix+u` swaps them back. |
| `prefix+a` | Auto-tile off / on for this tab (see [Auto-tile](#auto-tile)). A notification says which. |
| `prefix+shift+z` | Sleep / wake: end this tab's idle Claude agents to free their memory, or bring them back (see [Sleep and wake](#sleep-and-wake)). |
| `prefix+=` / `prefix+-` | Nudge: make the main area 10% bigger / smaller (between 20% and 80%) by moving dividers; no pane moves. heelr remembers the size for this tab, so applying the same layout here later keeps it. Works on the layout heelr last applied to the tab, if it has a main area (not `grid` or `even-*`). If the tab has changed shape since, the layout is applied again at the new size. Not undoable with `prefix+u`: nudge the other way. |

`b` means undo only **inside** the menu. Outside it, undo is `prefix+u`;
`prefix+b` is Herdr's own key for collapsing the sidebar.

If a pick changes nothing (the tab is already in that layout) or fails, the
menu stays open with the reason until you press a key. Otherwise it closes as
soon as the layout is applied.

Errors from `prefix+u`, `prefix+Enter`, `prefix+=`, `prefix+-`, `prefix+a` and `prefix+shift+z` appear as a Herdr
notification, because they run in the background.

The menu applies to the tab you're looking at. Multi-tab layouts appear in the
menu too and act on the whole workspace.

## Commands

| Command | What it does |
|---|---|
| `heelr list` | Lists every layout: built-in presets and your own. |
| `heelr show <name> [-n N]` | Draws a layout. For presets, `-n` sets how many panes to draw (default 4). |
| `heelr current` | Draws the tab as it is now, with each pane's id and detected role. |
| `heelr apply <name> [-n]` | Rearranges into a layout. `-n` prints the planned moves and changes nothing. `<name>` can also be a path to a `.toml` file. |
| `heelr back [-n]` | Undoes the last layout change in the workspace. Running it again flips back. |
| `heelr save <name> [--force]` | Saves the tab as an editable layout file. |
| `heelr save <name> --tabs [--force]` | Saves the whole workspace as a multi-tab layout. |
| `heelr promote` | Swaps the focused pane with the tab's largest pane (what `prefix+Enter` runs). |
| `heelr nudge + \| -` | Grows or shrinks the main area of the tab's layout by 10% and remembers it for the tab (what `prefix+=` / `prefix+-` run). |
| `heelr auto-toggle` | Switches auto-tile off (or back on) for the tab (what `prefix+a` runs). |
| `heelr sleep` / `heelr wake` | Ends the tab's idle, named Claude agents / resumes them in the same panes (see [Sleep and wake](#sleep-and-wake)). |
| `heelr sleep-toggle` | Wakes the tab if it has slept agents, else sleeps it (what `prefix+shift+z` runs). |
| `heelr auto` | Re-tiles every laid-out tab whose panes changed. The plugin runs it on pane events; does nothing unless `auto_tile = true`. |
| `heelr pick` | The layout menu that `prefix+m` opens. |
| `heelr selftest [--bin PATH] [--record]` | Runs the live tests on a throwaway Herdr server (see [After a Herdr upgrade](#after-a-herdr-upgrade)). |
| `heelr check-update [--bin PATH]` | Says whether a newer Herdr is out, and whether the running server needs a restart to pick up the installed binary. |

Options:
- `--tab <id>` points `apply`, `back`, `save`, `current`, `pick`, `promote`, `nudge`, `auto-toggle`, `sleep`, `wake` or `sleep-toggle` at a tab
  other than the focused one (multi-tab layouts use that tab's workspace). Tab
  ids look like `wA:t1`; `herdr tab list` shows them.
- `heelr --allow-untested <command>` runs on a Herdr version heelr hasn't
  been tested with (see [After a Herdr upgrade](#after-a-herdr-upgrade)).

## Layouts

There are three kinds. Your own layouts go in `~/.config/heelr/layouts/`,
one file each, named `<name>.toml`. A file there with the same name as a
preset replaces the preset.

### Rule layouts (presets)

Rule layouts work with any number of panes. Eleven ship with heelr. All but
`grid` open new shells when the tab has too few panes to show the shape
(see **Creating missing panes** below):

| Name | Shape |
|---|---|
| `main-left` | One large pane on the left (60%), the rest stacked on the right. |
| `main-top` | One large pane on top (60%), the rest side by side below. |
| `grid` | As close to square as possible: 4 panes → 2×2, 5 → 3 columns of 2, 2, 1. |
| `even-columns` | Equal columns. |
| `even-rows` | Equal rows. |
| `main-right` | Mirror of `main-left`: large pane on the right. |
| `main-bottom` | Mirror of `main-top`: large pane along the bottom. |
| `main-left-grid` | Large pane on the left (50%), the rest as a grid on the right. Better than `main-left` with 5+ panes. |
| `main-center` | Large pane in the middle (50%), the rest in a stack on each side (25% each). The first half of the other panes go left; with an odd count the left gets the extra one. With 2 panes it looks like `main-right`. |
| `two-mains` | Two large panes side by side on top (65%), the rest in a row along the bottom. The focused pane takes the left one. |
| `agents-logs` | Sorts by role: every agent pane in a grid on top (70%), everything else (logs, shells) in a row along the bottom. If every pane is an agent, it opens a new shell for the bottom row. With no agent at all it's a plain grid. |

Make your own variant by writing a file:

```toml
# ~/.config/heelr/layouts/wide-main.toml
name = "wide-main"
kind = "rule"
rule = "main-left"      # any rule name from the table above
main_ratio = 0.7        # share of the main area (ignored by grid / even-*)
main_role = "agent"     # optional: prefer an agent for the main pane
```

The main pane is the one you have focused. With `main_role` set, it's the
focused pane if it has that role, otherwise the first pane that does. Every
other pane keeps its current order.

For `agents-logs`, `main_role` instead picks which role goes in the top band
(default `agent`), and `main_ratio` is the top band's height.

**Creating missing panes.** Two more settings work in any rule or fixed layout:

```toml
add_missing = true                         # open new shells for slots with no pane
new_pane_command = "kubectl logs -f ..."   # optional: run this in each new pane
```

What counts as missing depends on the layout:

| Layout | With `add_missing = true` |
|---|---|
| `agents-logs` | If every pane is an agent, one new shell is added along the bottom. With no agent, nothing is added (a new shell couldn't fill the agent band) and the tab is a plain grid. With `main_role` set, nothing is added if the new pane would itself have that role (`main_role = "shell"`, or a `new_pane_command` that matches it), since it couldn't fill the bottom row. |
| `main-*` (including `main-center`), `main-left-grid` | A tab with one pane gets a second, so the main pane has something beside it. |
| `two-mains` | Topped up to 3 panes: two on top, one below. |
| `even-columns`, `even-rows` | A tab with one pane gets a second, so there are two columns (rows). |
| `grid` | Nothing is ever missing: any number of panes is a grid. |
| Fixed layouts | Same as `on_mismatch = "fill"`: every empty slot gets a new pane. |

**Every preset except `grid` has this on**, so picking, say, `main-left` on a
one-pane tab splits it instead of doing nothing. New panes are plain shells
(no `new_pane_command`) and open in the tab's working folder. Once the tab
has enough panes, these layouts only rearrange. `back` closes the new panes
again (see [Saving and undoing](#saving-and-undoing)).

A multi-tab layout that names one of these presets for a tab (`layout =
"main-left"`) fills that tab the same way; inline `[tabs.layout]` tables
have `add_missing` off unless they set it.

To turn it off for a preset, override it with a file of the same name:

```toml
# ~/.config/heelr/layouts/main-left.toml
name = "main-left"
kind = "rule"
rule = "main-left"
main_ratio = 0.6
add_missing = false
```

### Fixed layouts

A fixed layout spells out an exact shape with a set number of slots. The
easiest way to get one is to arrange a tab by hand (Herdr's own split and
resize keys) and run `heelr save <name>`. Then edit the file.

The shape is a tree of splits. Each split has a direction, a ratio, and two
children `a` (left or top) and `b` (right or bottom). Each child is either
another split or a slot.

```toml
# Agent on the left (60%); on the right, another agent above a log tail.
name = "agents-2x1-logs"
kind = "fixed"
on_mismatch = "fill"     # refuse | fill

[root]
dir = "right"            # right = side by side, down = stacked
ratio = 0.6              # share of the FIRST child (a)
  [root.a]
  role = "agent"
  main = true            # this slot gets your focused pane
  [root.b]
  dir = "down"
  ratio = 0.5
    [root.b.a]
    role = "agent"
    [root.b.b]
    role = "logs"
```

Slot options, all optional:
- `role = "agent"` prefers a pane with that role.
- `main = true` marks the slot for your focused pane.

When the tab's pane count doesn't match the slot count:

| `on_mismatch` | Fewer panes than slots | More panes than slots |
|---|---|---|
| `refuse` (default) | Nothing changes; it tells you why. | Nothing changes; it tells you why. |
| `fill` (same as `add_missing = true`) | Empty slots get new shells. | Extra panes move to a tab called `overflow`. |

### Multi-tab layouts

A multi-tab layout arranges a whole workspace. Each `[[tabs]]` entry is a tab
with a label. heelr gathers matching panes into it from any tab in the
workspace, then lays that tab out.

```toml
# ~/.config/heelr/layouts/fleet.toml  (also in examples/fleet.toml)
name = "fleet"
kind = "multi"
park_tab = "overflow"          # where panes no tab claims go

[[tabs]]
label = "agents"
gather = ["agent"]             # roles to pull in; "*" = any role
layout = "grid"                # a layout name ...

[[tabs]]
label = "ops"
gather = ["logs", "shell"]
max = 3                        # take at most 3 panes
  [tabs.layout]                # ... or a layout written inline
  kind = "rule"
  rule = "main-top"
  main_ratio = 0.5
```

How panes are placed:
1. If a tab with the entry's label already exists, the matching panes already
   in it stay there. That's why re-applying the same layout moves nothing.
2. Other matching panes are pulled in, earliest tab first, up to `max`.
3. A pane goes to the first entry that matches it, so put more specific entries first.
4. Every pane no entry claims moves to `park_tab`. Nothing is closed.
5. Tabs that don't exist are created. Tabs that end up empty close (Herdr's own behaviour).
6. Each tab is then laid out with its `layout`. Leave `layout` out to keep
   whatever shape the panes land in. If one tab's layout fails (for example a
   fixed layout that refuses), the other tabs are still done.

Labels must be unique and different from `park_tab`. Tabs created this way keep
their label; the tab-autoname plugin only renames tabs with numbered or
auto-set labels.

## Roles

Each pane gets one role. The first rule that matches wins:

| Role | Matches |
|---|---|
| `agent` | Herdr has detected an agent in the pane (Claude, opencode, Codex, …). |
| `logs` | The pane's foreground command matches `kubectl logs`, `tail -f` or `journalctl`. |
| `shell` | Everything else. |

Run `heelr current` to see what each pane was detected as.

Roles are preferences in fixed layouts: role-matching panes are placed first,
and if none match, any leftover pane fills the slot. In multi-tab layouts,
`gather` is strict: only panes with those roles go to that tab.

To change the rules, copy `roles.example.toml` from the repo to
`~/.config/heelr/roles.toml` and edit it. A rule is one of:

```toml
[[roles]]
name = "agent"
agent = true                    # Herdr detected an agent

[[roles]]
name = "k9s"
cmd = 'k9s'                     # regex on the foreground command line

[[roles]]
name = "shell"
fallback = true                 # always matches; keep it last
```

## Placement

Placement sends panes to the workspace and tab their repo and task belong to: one workspace per
repo, one tab per task. **It is off by default.** Turn it on with `enabled = true` in
`~/.config/heelr/placement.toml` (copy `examples/placement.toml`).

```toml
[placement]
enabled = false
workspace = "{repo}"        # one workspace per repo
tab = "{task}"              # one tab per task; tab = "{repo}" for one tab per repo
# ticket = 'GH-\d+'         # pull ticket ids out of worktree / branch names
create_workspaces = true
# classify = "~/.config/heelr/classify.sh"   # stdin: pane facts JSON; stdout: {"role","repo","task"}
# burst_window = 0.75       # seconds, 0 to 5 (anything else: 0.75); new panes closer together than this are a burst

[placement.shape]
default = "agents-logs"     # layout for each tab placement creates
# by_tab = { main = "main-left" }   # by the tab's task name
```

- Templates may use only `{repo}` and `{task}`. Anything else, or an empty template, makes heelr fall back to the defaults.
- A wrongly typed value (for example `enabled = "yes"` or `create_workspaces = 1`) is treated as unset: placement stays
  off and workspaces are created, as by default. Only that field falls back; a wrongly typed `shape.by_tab`, template or
  number makes the whole file fall back to the defaults.
  A pane's role never decides where it goes, so a role change is never drift.
- Per repo, put a `.heelr.toml` in the repo root with `name = "..."` (the workspace name) and/or
  `ticket = '...'` (the ticket regex for that repo).
- Trust: a repo's `.heelr.toml` is read from whatever folder a pane is in, so it can set the repo name and
  ticket pattern for that pane. Ticket patterns (here and in `placement.toml`) are accepted only if they are at most 200 characters, have at most two quantifiers (`?`, `*`, `+` and `{m,n}`), at most one `|` (not under a quantifier), no quantifier inside another, and no backreference, lookaround or conditional; anything else is ignored, because it can hang the hook on a crafted branch name, and logged once to `placement.log`. Each side of `A|B` counts its own quantifiers, so `[A-Z]+-\d+|GH-\d+` (three quantifiers) is ignored: write `[A-Z]+-\d+` instead. Only the first 120 characters of a name are searched.

### Where a pane belongs

The task comes from the first of these that has one: `heelr tag` (your own override); the adapter's
report (it may supply a task, a worktree or a branch); the git worktree name; the git branch, unless
it is the repo's default branch. A checkout inside a repo with none of those gets the task `main`.
`unsorted` is only for panes outside any git repo or with no known folder. If a ticket regex is set
(in `.heelr.toml` or `placement.toml`), the ticket id is pulled out of the name.

If another repo's task already uses a tab label in the workspace, heelr creates `<task> · <repo>`
instead, and a pane in a tab with that qualified label counts as in place.

### New panes

- A new pane is placed after a 0.75 s window (`burst_window`), so a restore that opens many panes
  is handled once.
- A burst (`pane.created` events chained within 2 s of each other) moves panes without moving focus,
  and never moves the focused pane. A single new pane moves, and focus follows it only if it is the focused pane; a pane a script opened in the
  background is placed without taking focus.
- A tab that placement creates gets its shape (`[placement.shape]`: `by_tab` for its task name, else
  `default`) recorded as its auto-tile layout, so with `auto_tile = true` it is laid out in that shape as panes
  arrive. A tab that already exists keeps the layout it has. The shape is applied by auto-tile, so it needs
  `auto_tile = true`.
- The Herdr version check applies to background placement like any other command: on an untested Herdr it
  places nothing and logs once (run `heelr selftest --record` to mark the version tested).
- If another heelr change holds the lock for over 5 s, the new pane is skipped quietly (a line in
  `placement.log`) and placed on the next event or by `heelr sort`.
- If the last hook of a burst fails, the burst stays unplaced. Run `heelr sort`.
- Panes in sleeping tabs, and panes in tabs with auto-tile switched off (`prefix+a`), are never
  placed, sorted, pulled or marked. If the only tab with the wanted label is asleep, the pane stays
  where it is.
- Failures of background placement are written to `placement.log` in heelr's state folder and
  notified once. A failing `classify` script (cannot start, times out, too much output, bad output, non-zero
  exit) is logged there too, and notified at most once a minute, by the hook, `sort` and `pull`;
  refreshing the ↗ marks stays silent.
- Moving a pane to another workspace gives it a new id. heelr keeps its stored facts and tag with it, also
  when you move or close panes by hand, and `heelr tag` / `heelr report` run from inside that pane (which
  still says its old id) follow it.
- A repo is named after its main checkout's folder. A worktree of a bare repo is named after the bare
  folder (`project.git`), and a worktree of a submodule after the submodule; `.heelr.toml` is read from that
  folder.

### Commands

```
heelr sort [--workspace | --all] [-n]   # move drifted panes (default: the focused tab)
heelr pull TASK [-n]                    # gather one task's panes from every workspace into this one
heelr tag TASK [--pane ID]              # set a pane's task by hand; --clear removes it
heelr report [--pane ID]                # store facts about a pane (JSON on stdin; adapters call this)
heelr back [-n]                         # undo the last sort / pull
heelr drift-mark                        # refresh the ↗ marks now
```

- `sort` and `pull` work even when placement is off. `-n` previews.
- `back` after `sort` or `pull` works only in the workspace where you ran it. There is a single
  place-undo record: a later sort in another workspace replaces it. A partial sort or pull that
  stopped on an error can still be undone with `back`. If a source tab closed after being emptied,
  `back` recreates it. `back -n` lists what it would restore.
- A bare `heelr tag` is an error (it does not clear); use `--clear`.

### The ↗ marks

With placement on, each tab that holds panes belonging somewhere else shows how many after its name:
`herdr/heelr ↗2`. The mark goes once the tab is clean. The focused pane counts too, so a tab shows the
same number wherever you are. `sort` never moves the focused pane, so after a plain `sort` the tab you
are in can keep `↗1` for the pane you're in; `sort --all` from another tab moves it. A pane you `pull`
into a tab counts too: `sort` would send it back to its repo's workspace.

- The marks update when a pane opens, closes or moves, after `sort`, `pull`, `back` and `tag`, and
  when a shell reports a new folder. A branch switch without a `cd` shows at the next of these, or
  run `heelr drift-mark`.
- Every heelr label comparison ignores the mark: placement, sleep, layouts and the tab colours see
  `herdr/heelr`. A label you type that ends in ` ↗` and a number is taken for a mark: heelr reads the name
  without it and replaces the number with its own count. tab-autoname
  leaves a marked tab alone, as it does a sleeping one, and names it again once the mark goes.
- Refreshing is best effort and silent: it never notifies, never waits on a layout change, has a 5 s
  budget (classify scripts included; past it nothing is renamed) and renames a tab only when its count
  changes. A tab renamed while a refresh runs is left for the next one.
- Turning placement off takes heelr's marks away at the next pane event (or run `heelr drift-mark`).
  It takes away only marks it remembers writing: a mark on a tab that came back with a new id (a Herdr
  restore while placement is off) stays. Rename that tab by hand, or turn placement on for a moment.
- The park tab (`heelr-park`) is never marked: its panes are only there during an apply.

### Adapters

Adapters tell heelr what a pane is working on (`heelr report`).

- Shell: `heelr shell-hook bash|zsh|fish` prints a snippet that reports folder changes. In bash,
  put `command -v heelr >/dev/null && eval "$(heelr shell-hook bash)"` after any line that sets
  `PROMPT_COMMAND` (in `~/.bashrc`). In zsh, add `command -v heelr >/dev/null && eval "$(heelr shell-hook zsh)"`
  to `~/.zshrc`. In fish: `command -v heelr >/dev/null; and heelr shell-hook fish | source`. The `command -v`
  guard keeps a shell that starts without heelr on its path quiet. The zsh and fish snippets are not yet
  tested on a real shell.
- `examples/adapters/`: `claude-hooks.json` (Claude Code `CwdChanged`; merge its `hooks` block into `settings.json`), `claude-statusline.md`
  (Claude Code status line), `codex-hooks.json` (Codex), `opencode-heelr.js` (opencode).

## Saving and undoing

**Saving:**
- `heelr save <name>` writes the focused tab's shape to
  `~/.config/heelr/layouts/<name>.toml` as a fixed layout. Each slot is
  marked with the role of the pane in it, and your focused pane's slot is
  `main`. Edit ratios and roles freely.
- `heelr save <name> --tabs` writes the whole workspace as a multi-tab
  layout: one entry per tab, gathering that tab's roles, capped at its current
  pane count, with its current shape inline (`on_mismatch = "fill"`). An
  existing `overflow` tab is left out; unclaimed panes land there anyway.
- Neither overwrites an existing file unless you add `--force`.
- `<name>` must be a plain file name: not empty, no `/` or `\`, not starting
  with `.`.

**Undoing:**
- `back` undoes the **last** layout change in the workspace, whether it was a
  single-tab or a multi-tab one. It goes one step back only; running it again
  redoes the change.
- An apply that changed nothing doesn't replace the undo point.
- Single-tab undo is kept per tab. It refuses if the tab has gained panes since,
  because it doesn't know where they go; apply a layout instead. A refused
  `back` changes nothing: it checks before closing any pane.
- Panes the apply created (`add_missing`) are closed if the shell is at its
  prompt or still running only `new_pane_command`. If something else is
  running in the foreground, the pane is kept and moved to the `overflow` tab.
- heelr can only see what runs in the foreground. A created shell back at its
  prompt is closed even if you've used it: its scrollback goes, and so do jobs
  you stopped with `ctrl+z` or started with `&`. To keep a created pane you've
  used, don't undo that apply: apply another layout instead.
- Multi-tab undo puts recorded panes back in their original tabs, recreating
  tabs that closed (they get new tab ids). A pane opened since in a tab being
  restored (including a created pane you've used) moves to the `overflow` tab;
  panes opened since elsewhere are left where they are.
- Panes closed since are simply left out.

**A safe way to experiment with a whole workspace:**

```bash
heelr save before --tabs     # a snapshot you can always return to
heelr apply fleet -n         # preview
heelr apply fleet
heelr apply before           # back to the snapshot, any time later
```

## Auto-tile

With auto-tile on, a tab you've laid out with heelr keeps its layout as panes open and
close, so a new pane slots into the stack instead of splitting whatever had focus.

- **A new pane** joins the end of the stack. Main stays where it is.
- **A closed pane** leaves no gap. If main closes, the top of the stack becomes main.
- The tab keeps its nudged size, and focus stays where Herdr put it.
- It never opens a pane, and it saves no undo point: `prefix+u` still undoes your last
  deliberate change.

It acts on a tab only when all of these hold:

1. `auto_tile = true` in `~/.config/heelr/config.toml` (off by default).
2. You applied a rule layout (a preset, or your own `kind = "rule"` file) to the tab, and
   haven't since undone it (`back`) or applied a multi-tab layout. Fixed layouts are left
   alone.
3. You haven't switched it off for the tab with `prefix+a`.
4. A pane opened in the tab, closed, or moved in or out. Moving panes around *within* the
   tab doesn't count.
5. No `heelr-park` tab is open in the workspace (it waits for `heelr back`).

`agents-logs` keeps its bands: a new pane goes wherever its role puts it, as `apply` would.

**Hand-made swaps.** heelr remembers the order it laid the panes out in. If you swap panes
by hand (not with `prefix+Enter`), the next pane that opens or closes puts them back in
heelr's order. Use `prefix+Enter`, or apply the layout again, to make a new order stick.

**Tabs laid out before auto-tile existed** have no remembered order: apply a layout once.

It runs from the heelr Herdr plugin (see [The heelr plugin](#the-heelr-plugin-tab-colours-and-auto-tile)).
Problems go to `~/.local/state/heelr/auto.log`. A new error also shows a notification; the same error again doesn't, so a lasting problem doesn't notify on every pane event. Once auto-tile works again it logs `ok: auto-tile working again`, and the next error notifies again.

## Sleep and wake

A tab you won't need for a while can give back its agents' memory (each Claude agent with its
tool servers holds roughly 250–400 MiB). `prefix+shift+z` sleeps the tab you're on; pressing it
again wakes it.

- **Sleep** ends each Claude agent in the tab, with its tool servers. The panes stay where
  they are, now showing their shell, so the layout doesn't change. A notification says how
  much memory was freed.
- **Wake** types `cd -- <its folder> && claude --resume <session id>` into each pane, the way
  Herdr's restore does after a reboot, so each agent comes back with its whole conversation.

Sleep is all or nothing. It refuses the whole tab, naming every pane that blocks it, unless
every agent in the tab:

1. **is a named session** (`/rename` it). Even if heelr's record were lost, you can find a
   named session with `claude --resume`.
2. **is idle**, not working or waiting for you.
3. **has no running task**: a shell command or watcher it started is still running
   (stopping it would lose that work). Stop the task, or wait for it.

Panes without an agent are left alone; one running something (a dev server, `tail -f`) is
named in the notification as "left running". A background *subagent* inside an agent can't be
seen, so it is stopped with the agent.

Wake skips a pane that is gone, or whose session is already running somewhere (resumed by
hand, say), and says so with the session's name; a pane busy with something else is left
asleep ("retry wake later"). A Herdr restart or reboot doesn't wake a slept tab: Herdr
forgets an agent once it has exited.

A sleeping tab's name gets `💤 ` in front (`💤 end-game`); wake puts the old name back,
unless you renamed the tab meanwhile. tab-autoname leaves a 💤 name alone and picks the tab
up again once it's restored, and tab colours ignore the 💤, so the tab keeps its colour.

The record of what's asleep is in `~/.local/state/heelr/sleep/`.

## Configuration and files

| Path | What it is |
|---|---|
| `~/.config/heelr/layouts/*.toml` | Your layouts. |
| `~/.config/heelr/roles.toml` | Optional role rules. |
| `~/.config/heelr/config.toml` | Optional. `favourites = ["main-left", "grid", ...]` sets menu keys `1`–`5` (and on). `auto_tile = true` turns on [auto-tile](#auto-tile). |
| `~/.config/heelr/tested-versions` | Herdr versions you've tested with `heelr selftest --record`, one per line. Shipped ones are in `heelr/tested_versions.txt`. |
| `~/.local/state/heelr/` | Undo records, the apply lock, `tabs/` (each tab's last layout, nudged sizes, pane order and auto-tile switch), `sleep/` (each sleeping tab's agents: pane, session id, name, folder), `pick.log` (one line per menu choice) and `auto.log` (auto-tile problems, kept under 64 KB). Safe to delete; you lose undo, nudged sizes and auto-tile's memory of each tab (apply again). |
| `~/.config/herdr/config.toml` | The `prefix+m`, `prefix+u`, `prefix+Enter`, `prefix+=`, `prefix+-`, `prefix+a` and `prefix+shift+z` bindings (search for `heelr`). |
| Your heelr checkout | The code (a git clone). `~/.local/bin/heelr` is a symlink to its `bin/heelr`. |

The menu lists favourites first (marked ★) in the order you give, then every
other layout alphabetically. Default favourites: `main-left`, `grid`,
`main-top`, `even-columns`, `even-rows`.

Each layout shows as a thumbnail with its key underneath, in one row that
scrolls sideways (◀ / ▶ mark more off screen); the highlighted one is
underlined and named in full at the top right. Rule layouts are drawn with 3–5
sample panes; multi-tab layouts show their tab count. The menu uses the
largest thumbnails that fit, and falls back to a plain list (with a `>` on the
highlighted line) if not even two fit. The popup size is the `width` /
`height` of the `prefix+m` binding; 96 × 16 shows four or five cards.

Layout keys are `1`–`9`, then letters, skipping `b`, `h`, `l` and `q` (the menu's own
keys).

The bindings, for reference:

```toml
[[keys.command]]
key = "prefix+m"
type = "popup"
command = "heelr pick"
width = 96
height = 16

[[keys.command]]
key = "prefix+u"
type = "shell"
command = "heelr back"

[[keys.command]]
key = "prefix+enter"
type = "shell"
command = "heelr promote"

[[keys.command]]
key = "prefix+="
type = "shell"
command = "heelr nudge +"

[[keys.command]]
key = "prefix+-"
type = "shell"
command = "heelr nudge -"

[[keys.command]]
key = "prefix+a"
type = "shell"
command = "heelr auto-toggle"

[[keys.command]]
key = "prefix+shift+z"
type = "shell"
command = "heelr sleep-toggle"
```

## Troubleshooting

| You see | What to do |
|---|---|
| `refused: layout has N slots but the tab has M panes` | A fixed layout with `on_mismatch = "refuse"`. Use a rule layout, or set `on_mismatch = "fill"`. |
| `tab … has a zoomed pane; unzoom first` | Unzoom the pane (Herdr's zoom key), then retry. |
| `Herdr X is untested` | See [After a Herdr upgrade](#after-a-herdr-upgrade). |
| `warning: Herdr X is an untested patch release; proceeding` | heelr ran anyway (same major.minor as a tested version). `heelr selftest --record` silences it. |
| `another layout change is running` | Another heelr change, auto-tile or layout plugin held on for over 2 seconds. Retry. |
| `no heelr layout recorded for this tab; apply a layout first` | Nudge works on the layout heelr last applied to the tab. After `back` or a multi-tab layout there isn't one. Apply one from the menu, then nudge. |
| `… has no main area to resize` | Nudge on `grid` or `even-*`: there's no main area. Apply a `main-*` layout instead. |
| `main area already at the limit` | Nudge stops at 20% and 80%. |
| Auto-tile does nothing | Check, in order: `auto_tile = true` in `~/.config/heelr/config.toml`; the heelr plugin is linked and up to date (`herdr plugin list`; re-link after updating heelr); the tab has a layout you applied since auto-tile existed; `prefix+a` hasn't switched it off (it says which); no `heelr-park` tab is open. `~/.local/state/heelr/auto.log` says why it skipped or failed. |
| Auto-tile put back panes I'd swapped by hand | It keeps heelr's order. Swap with `prefix+Enter`, or apply the layout again, and the new order sticks. |
| `busy: another heelr change ran for over 5s` in `auto.log` | A pane opened during a long apply. Open or close a pane, or apply again, to re-tile. |
| `no layout named …` | Check `heelr list`. The file must be `~/.config/heelr/layouts/<name>.toml`. |
| `INVALID: …` next to a layout in `heelr list` | The file has a mistake; the message says what. |
| `layout differs from target after apply` | The panes moved but some sizes or splits didn't come out as asked; the message lists which. Run `heelr current` to see the result, and try less extreme ratios. |
| A `heelr-park` tab stays open, and apply says "Run `heelr back` first" | An apply stopped partway. Run `heelr back`. If back can't restore it (it says so), move the panes in `heelr-park` back by hand, or close the `heelr-park` tab if it's empty, then apply again. |
| `stopped after k/n steps` | Something changed underneath (e.g. a pane closed mid-apply). Nothing was closed; `heelr back` restores. |
| The menu doesn't open | `herdr config check`, then `herdr server reload-config`. |
| A pick in the menu seems to do nothing | Usually the tab already has that layout; the menu says so. To see what each key press did, read `~/.local/state/heelr/pick.log`. |
| `note: all N panes are 'agent', so … is a plain grid here` | A role layout (`agents-logs`) without `add_missing`: one band is empty. Turn on `add_missing = true`, or add a log or shell pane. |
| `note: none of the N panes is 'agent', so … is a plain grid here` | `agents-logs` on a tab with no agent. Start an agent in one of the panes, then apply again. |
| The sidebar collapsed to a column of numbers | That's Herdr's `prefix+b` (sidebar toggle), easy to hit when reaching for undo. Press `prefix+b` again. Undo is `prefix+u`. |

## After a Herdr upgrade

heelr depends on details of Herdr's behaviour, so it checks the server's
version before changing anything:

- a **tested** version runs;
- any untested **patch release** of a tested major.minor, older or newer
  (0.9.2 or 0.9.4 when 0.9.3 is tested), runs, with a one-line warning;
- a new **minor or major** version (0.10.0, 1.0.0), or a version string heelr
  can't read (e.g. `0.10.0-rc1`), is refused until you test it.
  `heelr --allow-untested …` runs anyway.

To test a version, run:

```bash
heelr selftest --record
```

This starts a throwaway Herdr server (its own `HOME` under `/run/user/$UID`
if that directory exists, otherwise under `$TMPDIR`, or `/tmp`; the path must
be short enough for a unix socket,
none of your Herdr environment), checks that the server answering is that
fresh one (its socket inside the throwaway `HOME`, no workspaces or panes)
before any test runs, runs the live tests there, then stops it, kills anything
still running from its `HOME`, and deletes the `HOME`, also on Ctrl-C. Your running Herdr and its panes are never
touched, so it is safe to run from inside Herdr, and before restarting into a
new binary. The live tests apply every preset, a multi-tab gather and `back`,
and check that no pane or process changed.

- `--bin PATH` tests that binary. Default: `$HERDR_BIN_PATH`, else the
  `herdr.bin` next to the `herdr` on your `PATH`, else that `herdr`.
- `--record` adds the version to `~/.config/heelr/tested-versions` when the
  tests pass. A failing or skipped run records nothing.
- Exit code: 0 pass, 1 fail or error, 130 interrupted.

A version listed in either file is accepted exactly as written, even a
pre-release such as `0.10.0-rc1`: listing it is your explicit statement that
you tested it.

The tested list is plain text, one version per line: the versions shipped with
heelr are in `heelr/tested_versions.txt`, yours in
`~/.config/heelr/tested-versions`. To ship a version you've recorded, move it
into `heelr/tested_versions.txt` and commit.

To run the offline tests: `python3 -m pytest` from your heelr checkout.

Internals: selftest lets its own child processes run on the version under test
by setting `_HEELR_SELFTEST_ALLOW_VERSION` in their environment. It is not a
user setting and may change; to run on an untested version, use
`heelr --allow-untested`.

### Checking for a new Herdr

```bash
heelr check-update
```

Reads `https://herdr.dev/latest.json` and compares it with the installed
binary (`herdr.bin --version`) and the running server (`herdr.bin status
--json`, which still answers after an upgrade that changed Herdr's protocol;
`herdr api snapshot` if it can't; both read-only). It changes nothing: it doesn't install, restart, or turn on
Herdr's own `version_check`.

| Exit | Meaning |
|---|---|
| 0 | Up to date. |
| 10 | A newer Herdr is out. The output says whether heelr already accepts it, would warn, or would refuse until `heelr selftest --record` passes. |
| 11 | The server is running an older version than the installed binary, or Herdr itself reports the server's binary changed since it started: restart Herdr to pick up the update. |
| 1 | Error, e.g. no network, or the binary or server doesn't answer within 10 seconds. |

If an update is out and a restart is pending too, the exit is 10 and both are
printed. `--bin PATH` checks a different binary.

## The heelr plugin: tab colours and auto-tile

`plugins/tab-colours/` is heelr's Herdr plugin (its id is `heelr.tab-colours`, its name
"heelr"). It does two things, each from its own hooks, so a problem in one can't hold up the
other:

- **Auto-tile:** on `pane.created`, `pane.closed` and `pane.moved` it runs `heelr auto` from
  the same checkout (`../../bin/heelr`, so the plugin and heelr are always the same version).
  That does nothing unless `auto_tile = true`; see [Auto-tile](#auto-tile).
- **Tab colours**, below.

Herdr copies a plugin's hooks when you link it, so after updating heelr, link it again:
`herdr plugin link <checkout>/plugins/tab-colours` updates the existing plugin in place
(same id, so your colours and pins are kept).

### Tab colours

The plugin gives every tab a stable, distinct
colour in the sidebar's agent list, with no per-tab config. Herdr can't colour a
row or compute a colour, so the plugin gives each tab a colour slot and reports
one pane token per pane, `$tabc<slot>`, whose value is the tab's name. The
sidebar lists all slot tokens, each with a fixed colour; only the reported one
shows, so each agent row shows its tab name once, in its tab's colour.

- **Stable:** a tab keeps its slot for as long as it exists (renames,
  restarts and live handoffs included); the slot table is in the plugin's state
  dir. A tab that comes back under a new id gets its old slot back by name.
- **Distinct:** a new tab takes the least-used slot, lowest first, so the first
  seven tabs get gruvbox's seven bright accents. Ten slots; past ten, colours
  repeat.
- **Pins:** `[pins]` in the plugin's `config.toml` gives tabs whose name starts
  with a prefix a fixed colour. Automatic slots avoid pinned colours while
  others are free.
- **Updates:** runs on Herdr start and live handoff (pane tokens don't survive
  a handoff; the plugin re-reports them) and on `tab.created`, `tab.renamed`,
  `tab.closed`, `pane.created` and `pane.moved`, so heelr's pane moves recolour
  too.

Install (Herdr 0.9.3+):

```bash
cd ~/src/heelr/plugins/tab-colours
herdr plugin link "$PWD"
cp config.example.toml "$(herdr plugin config-dir heelr.tab-colours)/config.toml"   # optional pins
python3 tab_colours.py sidebar     # prints the [ui.sidebar.agents] rows for your pins
```

Replace the `[ui.sidebar.agents]` block in `~/.config/herdr/config.toml` with
that output (`sidebar.example.toml` is the output for the example pins), then
`herdr server reload-config`. Colours appear on the next tab or pane event; to
apply them at once, run `python3 tab_colours.py` (it uses the same state and
config dirs as the plugin). Change pins → re-run `sidebar` and replace the rows
again. Remove: `herdr plugin unlink heelr.tab-colours` and restore your rows.
