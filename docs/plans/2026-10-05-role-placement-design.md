# Role placement: design (2026-10-05)

Put every pane in the right workspace, tab and slot from what it is (its role) and what
it's for (its repo and task), so a new user writes a few rules and stops hand-arranging.
Agreed in a brainstorm on 2026-10-05.

Why this feature: a survey of the Herdr plugin ecosystem, agent managers and terminal
layout tools (2026-10-05) found presets, promote, nudge, auto-tile, sleep and tab naming
all have close equivalents. Placing **already-running** panes by role, and gathering them
into labelled tabs, had none. This is heelr's differentiator, so it gets the depth.

## Decisions

| Decision | Chosen | By |
|---|---|---|
| What placement is for | Stacked layers: organise by work (A) and keep a standard shape (C) matter most; triage by agent state is optional and out of scope here | user |
| Unit of work | Key is **(repo, task)**. Default repo-first: workspace = repo, tab = task. Repo = tab (the current habit) stays possible via templates | user |
| Multi-repo tickets | Repo-first by default, plus `heelr pull <task>` to gather one task across repos, undone by `back` | user |
| When panes move | New panes are placed automatically; existing panes that drift are **marked**, not moved; a key moves them | user |
| Architecture | Rules + event hooks (no daemon) | user, on recommendation |
| Extension points | `heelr report` (facts in) and an optional classify script (key out), both JSON on stdin, modelled on Claude Code's status line | user |
| Agents | Agent-neutral core; per-agent adapters are optional. Claude, Codex, opencode first | user |
| Versioning | Semver. Current main tagged `v0.1.0` (96f0810) before this work; this feature ships as `0.2.0` | user |

## Terms

Herdr nests **server** (one, all your work) → **workspace** → **tab** → **pane**. This doc
uses those words only; "session" is avoided.

## Pipeline

```
facts  ──►  key  ──►  place  ──►  shape
```

### Facts

Per pane, from:

- **Herdr snapshot** (verified on 0.9.3): `cwd`, `foreground_cwd`, `agent`, `agent_status`,
  `agent_session`, `terminal_title`, `workspace_id`, `tab_id`.
- **Git**, from the pane's folder: repo root (the main checkout's root for a worktree),
  worktree name, branch. Cached per folder for 10 s.
- **Reports** from adapters (`heelr report`), latest per pane.
- **Tag** set with `heelr tag <task> [--pane ID]`. Overrides everything for `task`.

An agent's process folder does not follow the agent: a `cd` in Claude's Bash tool leaves
`/proc/<claude>/cwd` unchanged (verified 2026-10-05). So for agents, the pane folder means
"where it was started", and adapters or tags are how mid-session moves are seen.

### Key

`role`, `repo`, `task`.

1. If `classify` is set, run it (contract below). Fields it returns win.
2. Missing fields come from the rules in `roles.toml` (today's role rules, first match
   wins) and these defaults:
   - `repo`: per-repo `.heelr.toml` `name`, else the repo root's folder name, else the
     pane folder's name.
   - `task`, first that gives one: tag → report (`task`, else its `worktree`/`branch`) →
     the pane folder's worktree name → branch → `unsorted`.
3. **Ticket extraction:** `re.search` of the ticket regex on the task string. The regex
   is the per-repo `.heelr.toml` `ticket` if set, which overrides the global
   `placement.toml` `ticket`. A match replaces the task with group 1 if the regex has a
   group, else the whole match (`fix/GH-330-crash` → `GH-330`); no match keeps it whole.

The default branch (`main`/`master`, or the remote's HEAD) gives `task = "main"`.

### Place

Templates in `~/.config/heelr/placement.toml`:

```toml
[placement]
enabled = false             # shipped default; see "Default: off"
workspace = "{repo}"        # default: repo-first
tab = "{task}"              # tab = "{repo}" = one tab per repo
ticket = 'GH-\d+'           # optional global ticket regex
create_workspaces = true
classify = ""               # optional script path

[placement.shape]
default = "agents-logs"     # layout applied to each placed tab
# by_tab = { main = "main-left" }
```

- Workspaces and tabs are matched by label. Missing ones are created (workspaces only when
  `create_workspaces = true`; otherwise the pane stays in its current workspace and only
  the tab template applies).
- Tabs are created **with their final label** in one step. tab-autoname leaves any label
  that isn't the default or its own last one alone, so no cross-plugin ordering is
  needed. (Whether `tab create` takes a label atomically is a spike item.)
- **Labels are never changed after creation**, whoever made the tab. heelr renames,
  removes or (in `back`) closes only tabs it created, recorded in state.
- **Label clash:** a new tab whose label would clash with an existing tab of a different
  key is created qualified: `GH-330 · heelr`. The existing tab keeps its label. A tab you
  named is matched as a destination only by exact label; it is never renamed.

### Shape

The destination tab is laid out with its shape layout, using today's `assign` /
`bind_rule` (roles fill role slots). Existing presets, unchanged.

## When placement runs

| Trigger | Action | Moves panes |
|---|---|---|
| `pane.created` hook | Key the new pane, move it to its tab (creating it), re-lay that tab; focus follows | the new pane |
| Drift: shell prompt hook, `heelr report`, focus events, periodic check | Record drifted panes in state and show the **focused tab's** count (`↗ 2`) in a `[ui] tab_bar_right` entry; labels untouched | no |
| `heelr sort` / `--workspace` / `--all` | Move drifted panes in the focused tab / the focused workspace / every workspace on the Herdr server to their tabs | yes |
| `heelr pull <task>` | Gather every pane with that task, across workspaces, into a `<task>` tab | yes |
| `heelr back` | Undo the last place / sort / pull | yes |

**Drift** means the pane's (repo, task) maps to a different workspace or tab than the one
it is in. Role is not part of drift: a role change (an agent exits, the pane becomes a
shell) only changes which slot it gets the next time the tab is laid out.

The periodic check uses Herdr's `[ui] tab_bar_right` `command` entry, which runs a command
every `interval_seconds` and shows its last stdout line (Herdr 0.9.3 config reference; the
tab-sleep work used it briefly). Default interval 30 s.

### One process with auto-tile

`heelr auto` already runs on `pane.created`, `pane.closed` and `pane.moved` from the
tab-colours plugin. Placement is **not a second hook**: it runs inside `heelr auto`, in one
process under `core.apply_lock` (`<state_dir>/apply.lock`): place the new pane, then
auto-tile the tabs it left and entered. No two heelr hooks re-lay the same tab.

### What placement ignores

- **Panes heelr created** (core records every pane it creates during apply / pull / sort /
  back), and every `pane.created` / `pane.moved` event raised while heelr holds
  `apply_lock` for its own moves.
- **Bulk creation** (e.g. `herdr-resume-work` restoring a session, or a Herdr restart):
  more than 3 `pane.created` events within 2 s switch placement to quiet mode for that
  burst. Panes are placed once the burst ends, with no focus-follow; nothing jumps across
  workspaces during a restore.
- **Sleeping tabs and panes:** tabs labelled `💤 …` and panes in a sleep record are never
  marked as drifted, and new panes are never placed into a sleeping tab.
- `unsorted` panes are left alone and never marked.

Invariants:

- The focused pane never moves on its own (a single pane created just now is the
  exception: it moves and focus follows).
- Panes are never closed or killed.
- Auto-tile's per-tab off switch also turns placement off for that tab.
- No dependence on hook order across plugins.

### Default: off

Plugins load from the main checkout, so landing this changes a live session. Placement is
**off** unless `~/.config/heelr/placement.toml` exists with `enabled = true`. With it off,
`heelr auto` behaves exactly as today. `report`, `tag`, `sort`, `pull` still work on demand.

### `back` after things changed

If a pane in the undo record has closed or been moved by hand since, `back` skips it and
reports it ("skipped 1 pane: closed"). It restores the rest.

## Contracts

### `heelr report` (facts in)

```
heelr report [--pane ID]          # default $HERDR_PANE_ID
stdin: {"cwd","worktree","branch","task","agent","session_id"}   # all optional
```

Stores the report per pane with a timestamp; dropped when the pane closes. Never moves
anything and never runs git, so it returns in milliseconds. Unknown fields are ignored.

### Classify script (key out)

```
stdin:  {"pane_id","role_hint","agent","agent_status","cwd","foreground_cwd",
         "git":{"root","worktree","branch"},"report":{...},"tag", "repo_config":{...}}
stdout: {"role","repo","task"}    # any may be omitted
```

1 s timeout. Error, timeout or bad JSON: fall back to the rules for that pane, log it.
Results are cached per pane by a hash of the stdin JSON, so a periodic check does not run
the script again for a pane whose facts haven't changed.

### Adapters (shipped as examples, opt-in)

- **Shell:** `eval "$(heelr shell-hook bash)"` (also zsh, fish). Appends to
  `PROMPT_COMMAND` (never replaces it, the way direnv does); when `$PWD` changed since the
  last prompt, runs `heelr report` in the background with the new folder.
- **Claude Code:** one line in the user's own status line script, passing its stdin JSON to
  `heelr report` in the background. heelr never takes the `statusLine` setting.
- **Codex:** a `~/.codex/hooks.json` entry calling `heelr report`.
- **opencode:** a small plugin calling `heelr report` on session events.

## Failures

| Failure | Behaviour |
|---|---|
| Classify script fails | Rules decide; log to `~/.local/state/heelr/placement.log`; at most one toast per minute |
| Folder not in git | `repo` = folder name, `task` = `unsorted` |
| Herdr call fails mid-move | Stop; record what moved; `back` undoes the partial move |
| heelr's `apply_lock` or the shared layout lock is held | Wait up to heelr's timeout, then skip; the next event retries |
| Untested Herdr version | Existing version gate; placement off unless `--allow-untested` |

## Versioning

- `heelr/__init__.py` holds `__version__`; `heelr --version` prints it; a test checks the
  tab-colours manifest version matches.
- Tags `vX.Y.Z` on main; `CHANGELOG.md` with a section per release.
- Before 1.0: breaking config or CLI change → minor; features and fixes → patch.
- This feature: `0.2.0` (new config files, wider meaning of roles).

## Spike results (2026-10-05)

See `docs/spikes/2026-10-05-role-placement-spikes.md`. What changed in this design:

- **Moves never steal focus:** every heelr move passes `--no-focus`, except the single
  just-created pane, which moves with `--focus`.
- **Cross-workspace moves change the pane id.** State keyed by pane id (reports, tags,
  drift, classify cache, undo records) is re-keyed from `previous_pane_id` on
  `pane.moved` and from the move's own reply. Cross-workspace moves use `--tab <id>`, or
  `--new-tab --workspace W` when the destination tab doesn't exist.
- **New tabs:** `tab create --label <final> --workspace W --no-focus`, so no default
  label ever shows.
- **Locks:** heelr takes `apply_lock`, then the shared `<HERDR_SOCKET_PATH>.layout.lock`
  (flock, with heelr's timeout) for any reshape, so it doesn't fight other layout plugins.
- **Hooks from different plugins run concurrently, in no order:** confirms the
  no-ordering design; tab-autoname is unaffected because labels are final at creation.
- **Event data comes from env** (`HERDR_PLUGIN_EVENT_JSON`); the pane's folder is in it,
  so a new pane can be keyed without waiting.
- **Adapters:** Claude via `CwdChanged` / worktree hooks or the status line (follows the
  agent). Codex and opencode: launch folder or tag for now; their adapters report what
  they have.

## Spike (as planned)

1. Does each adapter see a changed folder or worktree mid-session (Claude status line and
   hooks, Codex hooks, opencode events)? Which fields?
2. Does `pane.created` arrive before the pane has a folder?
3. Does `pane move --workspace` steal focus when you're elsewhere?
4. Does the shell hook coexist with the user's `PROMPT_COMMAND` and conda setup?
5. Cross-plugin hooks: are hooks from different plugins for the same event run in a fixed
   order, and can they run concurrently?
6. Does `tab create` take a label atomically (no moment with the default label)?
7. Third-party layout plugins reportedly share a lock file. Find its name and semantics
   in their source; if it exists, honour it alongside `apply_lock`.

Results that contradict this design update this document before the plan is written.

## Testing

- Unit (offline): key extraction and ticket regexes (global and per repo), templates and
  label qualifiers, classify contract (bad JSON, timeout, partial output), drift detection,
  planner output for place / sort / pull and their `back`.
- `heelr selftest` (throwaway Herdr): new pane lands in its tab; cross-workspace move;
  pull then back restores; the focused pane never moves.
- Mutation checks on key and drift logic.

## Out of scope

- Triage by agent state (moving "needs input" agents forward).
- Generating classify scripts (`heelr classify --init`).
- Repo setup for open source (branches, PRs, templates): separate piece of work.
