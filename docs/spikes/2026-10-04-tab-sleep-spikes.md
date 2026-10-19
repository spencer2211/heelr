# Tab sleep spikes (2026-10-04)

Run on a throwaway Herdr 0.9.3 server (own HOME under `/run/user/<uid>/hx-ts`, own tmux
socket) with a throwaway Claude Code 2.1.289 session (Haiku, `--session-id` fixed,
`--settings` adding a SessionEnd hook that appends to a marker file). The live server
was not touched. The throwaway server, its processes and the throwaway session's
transcript were removed afterwards.

## Spike 1: SIGTERM to the process group

| Check | Result |
|---|---|
| Claude exits on SIGTERM | Yes, 3 of 3 runs |
| Time until the group is empty | 0.6 s, 1.1 s (with an MCP server), 5.2 s (first run) |
| MCP server in Claude's group | Yes: `uv tool uvx mcp-server-fetch` had Claude's pgid; the group was empty afterwards |
| SessionEnd hooks run | Yes, 3 of 3 |
| Transcript | Ends cleanly (a `cost-state` record); resume restores the whole conversation |
| Other Claude still holding the session | None (`pgrep` on the id) |
| Group RSS before the kill | 271–296 MiB |
| Terminal afterwards | Normal shell prompt; Claude prints "Resume this session with: claude --resume <id>". `stty` flags match an idle bash pane, so no reset is needed |

## Spike 2: Herdr after the agent exits

| Check | Result |
|---|---|
| Pane flags right after the exit | **Stale**: still `agent = claude`, `idle`, same `agent_session` |
| `session.json` after the exit | The session is **gone** from it (it was listed while Claude ran) |
| Restore after stop + relaunch | Did **not** type a resume into the slept pane; flags cleared (`agent` unset, `unknown`) |
| Tab ids after stop + relaunch | Same (`w1:t1`, `w1:t2`), as are pane ids |

## Spike 3: typed resume

| Check | Result |
|---|---|
| `ctrl+u` via `pane send-keys` before typing | Clears leftover text on the line |
| `cd -- <cwd> && claude --resume <id>` | Conversation restored, same session id, same transcript file (no new one) |
| Herdr re-detects the agent | Yes, `claude idle <same id>` |
| A turn after the resume | Appended to the same transcript, same `sessionId` |
| `--resume <id>` from the wrong cwd (`/tmp`) | **Works**: the session is found by id from any cwd |
| `--resume <id>` while the session is live in a pane | **Not refused**: a second copy ran and wrote to the same transcript |

## Consequences for the design

- Deadline: raise it from 5 s to 10 s (one run took 5.2 s).
- Spike 2 confirms process-info as the authority (stale flags right after an exit).
- Herdr's restore never wakes a slept agent: `session.json` drops it at exit.
- Tab ids are stable across a restart, so the record stays keyed by tab id; the
  pane-id fallback is dropped.
- No terminal reset step is needed.
- The `cd` before the resume stays, but for the agent's working directory, not to find
  the transcript.
- The already-running check is essential: Claude itself doesn't refuse a second copy.
