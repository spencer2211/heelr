# Role placement spikes (2026-10-05)

Herdr 0.9.3 on a throwaway server (an isolated throwaway Herdr server),
plus docs and source reading. Never the live server.

| # | Question | Answer |
|---|---|---|
| 1a | Claude: does an adapter see a folder/worktree change? | Yes. Hook `cwd` follows `cd` and worktree entry; `CwdChanged`, `WorktreeCreate`, `WorktreeRemove` events exist. Status line `cwd` / `workspace.current_dir` (= cwd), `workspace.project_dir` (launch dir), `workspace.git_worktree`, `worktree.{name,path,branch,original_cwd}` (docs: code.claude.com/docs/en/hooks, /statusline). |
| 1b | Codex 0.156 | Hooks (`SessionStart`, `UserPromptSubmit`, `Pre/PostToolUse`, `Stop`, …) carry `cwd`, documented as the session's folder; no change event. `notify` program gets `cwd` on turn complete. Treat as launch folder or tag. |
| 1c | opencode 1.18 | `session.created/updated` carry `info.directory` (SDK type `Session.directory`); whether it changes mid-session is unverified. |
| 2 | Does `pane.created` see the pane's folder? | Yes: `cwd` and `foreground_cwd` are in the event JSON and the snapshot when the hook runs. |
| 3 | Does a cross-workspace move steal focus? | Yes by default. `pane move` takes `--focus` / `--no-focus`. `--workspace` only works with `--new-tab`; otherwise move with `--tab <id>`. **A cross-workspace move gives the pane a new id** (`w1:p7` → `w2:p5`); same-workspace moves keep it. `pane.moved` reports `previous_pane_id`. |
| 4 | Shell hook vs the user's `PROMPT_COMMAND` / conda | Coexists when appended after the line that sets `PROMPT_COMMAND`; fires on each folder change, also after `conda activate`. |
| 5 | Hook order across plugins | None: hooks for the same event from different plugins start within ~1 ms and run concurrently; order varied. |
| 6 | `tab create` with a label | `herdr tab create --label X [--workspace W] [--cwd D] [--no-focus]`; `tab.created` already carries `X`. |
| 7 | Third-party layout lock | Real: `<HERDR_SOCKET_PATH>.layout.lock`, blocking `flock(LOCK_EX)`, no timeout, held over export+reshape+apply (caoer/ccc-herdr-layout `internal/herdr/client.go`, iurysza/herdr-pane-layouts `src/cli.py`). |

Event payloads: no stdin. Env: `HERDR_PLUGIN_EVENT`, `HERDR_PANE_ID`, `HERDR_TAB_ID`,
`HERDR_WORKSPACE_ID`, `HERDR_PLUGIN_CONTEXT_JSON`, and `HERDR_PLUGIN_EVENT_JSON` =
`{"event": "pane_created", "data": {"pane": {...}}}` (`pane_moved`: `previous_pane_id`,
`previous_workspace_id`, `previous_tab_id`, `pane`, `created_tab`).

Unverified: whether a split inherits the source shell's live `cd` folder.

## Hook budget (measured)

Herdr 0.9.3, throwaway server only (plugin linked into the throwaway HOME, via the `heelr selftest` harness).

- Plugin hook timeout: five `pane.created` hooks sleeping 2, 5, 10, 30 and 70 s all ran to completion; none was killed and `herdr plugin log` stayed empty. The timeout is above 70 s or absent; the upper bound is unknown. Neither `herdr plugin --help` nor the binary says.
- Placement through the real hook (tab-colours plugin linked, `auto_tile = true`, default `burst_window` 0.75 s): about 0.85-0.93 s from the split to the pane being placed (single pane into a new tab 0.84 s, new workspace and tab 0.89 s, burst of 4 all placed 0.93 s). That is the 0.75 s window plus roughly 0.1-0.2 s of work.
- Method: split a pane with `--cwd` set to a worktree folder and poll `api snapshot` every 30-50 ms until the pane sat in its task tab.
- Budget: about 1 s against a timeout of at least 70 s.
