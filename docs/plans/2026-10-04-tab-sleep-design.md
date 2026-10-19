# Tab sleep and wake: design (2026-10-04)

Free the memory held by a tab's idle Claude agents, and bring them back quickly when they
are needed. Agreed in a brainstorm; revised after review. A Claude agent plus its MCP
servers holds roughly 250–400 MiB, so a five-agent tab frees over 1 GiB.

Herdr has no command to pause or stop a tab's agents. heelr adds one, using the session
id Herdr already reports for each agent pane.

## Behaviour

- **Sleep** ends the Claude process group in every agent pane of the **current tab**.
  The panes stay where they are, now showing their shell, so the layout (and
  auto-tile's `"panes"` record) is unchanged. Each pane's session id, name and cwd are
  saved first.
- **Wake** types `cd -- <cwd> && claude --resume <id>` into each slept pane, the way
  Herdr's own restore does after a reboot. The `~/.bashrc` wrapper adds bypass mode
  inside Herdr panes, so a woken agent comes back in the same mode as a restored one.
- One toggle, `heelr sleep-toggle`, on `prefix+shift+z`: it wakes the tab if its record
  has any slept pane, and otherwise sleeps it. Plain `heelr sleep` and `heelr wake`
  exist too. (`prefix+z` is a Herdr default; `prefix+shift+z` is free in both Herdr's
  defaults and the user's config.)
- A notification reports the result, with the memory actually freed: "slept 5 agents,
  freed 1.3 GiB; left running: pW (npm run dev)", or "woke 5 agents".
- A sleeping tab's label gets a `💤 ` prefix; wake restores the old label if the tab still
  reads `💤 <old>`. tab-autoname skips labels it didn't set and resumes once restored;
  tab-colours strips the prefix so slots and pins hold, and so does every heelr label
  comparison (`core.base_label`: the park guard, the overflow tab, multi layouts). (First built as a `$sleep` pane
  token in the sidebar's agent rows, which never showed: Herdr drops a pane from those rows
  as soon as Claude exits, and has no per-tab sidebar row. Then a `tab_bar_right` list,
  which worked but sat away from the tabs.)

## Which panes count as agents

**`pane process-info` is the authority**, not Herdr's agent flag. Claude's Herdr hook
reports only SessionStart, so after a sleep Herdr may keep `agent = "claude"`, an idle
status and the old `agent_session` for a pane with no Claude in it.

- **Live agent pane:** the foreground group holds a `claude` process (its leader).
- **Stale:** Herdr says agent, but no `claude` is running. Not signalled; reported in
  the summary as "already asleep or stale".
- Everything else is never touched. An idle shell is left as it is; a shell running
  something else (a dev server, `kubectl port-forward`, `tail -f`) is left running and
  named in the summary.

## Sleep refuses the whole tab unless every live agent pane passes

All or nothing: either every live agent in the tab sleeps or none does. A refusal names
every blocking pane, in one shape:

```
can't sleep this tab:
  p7 'Terminal B' is not a named session (/rename it, then retry)
  p3 is unnamed (no transcript found)
  p6 'prompt-aliases' is working
  p1 'task-orch' has a running task: bash (running 21m)
```

1. **Named session.** The session's transcript (`~/.claude/projects/*/<id>.jsonl`) has
   a `custom-title` record; the last one counts. A missing transcript is "unnamed (no
   transcript found)". Why: a named session is one the user chose to keep, and its name
   is the manual fallback. Even if heelr's record is lost, every sleeping agent can be
   found by name in `claude --resume`. Any name passes, including placeholders like
   "Terminal A"; heelr doesn't judge names.
2. **Idle.** Herdr reports `agent_status = "idle"`.
3. **No running task.** Herdr's idle comes from turn state, so an idle agent can still
   have a background shell or Monitor running, and a kill would silently lose that work.
   Measured on the live machine: MCP servers share Claude's process group, while each
   Bash-tool shell (foreground or `run_in_background`) gets its own group. So any
   descendant of `claude` outside Claude's process group blocks the sleep, named with
   its age. (One idle agent on the live machine has a bash child that has run for 8 h;
   this rule would block it, which is right until the user decides about that task.)
   **Accepted risk:** a background *subagent* runs inside the Claude process and can't
   be detected. The success summary says so: "background subagents, if any, were
   stopped".
4. **Session id known.** Herdr reports `agent_session.value`. In practice this rule
   exists to refuse a Claude that Herdr didn't detect: such a pane never has a session
   id, so there is nothing to resume.

## Sleep record

`state/sleep/<tab>.json`. Each pane has a status, and the file is rewritten (tmp +
rename) after every change:

```json
{"tab": "wA:t1", "slept_at": "2026-10-04T10:30:00Z",
 "panes": [{"pane": "wA:p7", "session": "e4d6509c-…", "name": "Terminal B",
            "cwd": "/home/user/project",
            "status": "slept", "rss": 312000000}]}
```

- `status`: `pending` (written before the kill), `slept` (group gone), or `failed`
  (still running after the deadline). Wake acts only on `slept` panes.
- Records are keyed by tab id. Spike 2: tab and pane ids survive a Herdr stop +
  relaunch.
- **Sleep on a tab that already has a record** merges: slept panes stay as they are,
  and the checks and kill apply to the tab's live agent panes. A `failed` pane is just a
  live agent pane again: it goes through the checks, and its entry is overwritten.
- **The toggle on a half-slept tab** wakes the slept panes. Sleeping the rest is a second
  press.

Wake resumes **by session id, never by name**. Names needn't be unique (two tabs can
each have an agent called "reviewer"); the name is only for the manual fallback and
the messages.

## Ending the processes

After the checks pass for the whole tab:

1. Re-read every pane. A pane whose foreground `claude` pid changed, or that is no
   longer idle or has gained a running task, is skipped and reported; the others
   continue.
2. Record each pane as `pending` with the group's summed VmRSS (the memory figure in
   the summary).
3. Send SIGTERM to the **process group** (`kill -- -pgid`), only after confirming the
   pgid is not the shell's and the group's leader is the `claude` pid. Signalling only
   the pid could leave the MCP servers running. **Not `/exit`**: in this Claude version
   `/exit` moves the session to the background and the memory stays in use.
4. Wait for all groups in parallel, one 10 s deadline (spike 1 measured 0.6–5.2 s). Success is **the group is gone**,
   not just the pid. A group still alive at the deadline is `failed`, reported and left
   running. No SIGKILL.

Spike 1: no terminal reset is needed; the shell prompt comes back normally.

## Wake

Per `slept` pane:

1. **Pane gone:** report it with the name to find it by ("p7 is gone; resume
   'Terminal B' with `claude --resume`") and **drop it from the record**. It can't be
   recovered there, so keeping it would only block the record.
2. **Already running?** If the session is live anywhere wake can see, report it and
   **drop it from the record** (for example, Herdr's restore already woke it). Live
   means: a Herdr pane whose `agent_session.value` is the id **and** whose foreground
   holds a live `claude` (stale flags don't count), or any `claude` process whose
   `--resume`/`-r` argument is exactly the id or the recorded name (the designer itself
   runs as `claude --resume heelr-designer`). Exact match on that one token, never a
   substring of argv. A name match may be a different session with the same name, so its
   message gives the slept session's id to resume by hand. Within one wake, a session
   or pane already typed into counts as running. Not detectable: a Claude outside Herdr that was started as plain
   `claude` and renamed afterwards. Wake would start a second copy of that one.
3. **Pane busy:** its foreground is something other than its shell (the user is running
   something there). Keep it `slept` and report "p7 busy; retry wake later". This skip
   is transient.
4. Clear the shell line (`ctrl+u` via `send-keys`), then type
   `cd -- <shlex-quoted cwd> && claude --resume <id>` and Enter. The `cd` puts the
   agent back in its working directory. (Spike 3: `--resume <id>` finds the session
   from any cwd, so the agent would otherwise run in the wrong directory, not fail.)
   Step 2 is essential: spike 3 showed Claude doesn't refuse a second copy of a live
   session.

The record is deleted once no pane in it is `slept`, so a wake that only drops gone or
already-running panes empties it, and the next toggle sleeps the tab.

## Interactions

- **Auto-tile:** sleep and wake open and close no panes, so auto-tile has nothing to do.
- **Lock:** sleep and wake take the apply lock (2 s wait, like the interactive commands).
  Sleep holds it for at most about 10 s. Auto-tile waits up to 5 s for the lock, so a
  pane event during a sleep may be skipped and logged; acceptable, since sleep moves no
  panes.
- **Herdr restart or reboot while a tab sleeps:** Herdr's restore resumes only the
  agents in its `session.json`, and spike 2 showed Herdr drops a session from it when
  Claude exits. So a restart leaves slept agents asleep and wake works as usual. If the
  user resumes one by hand meanwhile, wake's step 2 drops it.

## Spikes

Done 2026-10-04; results in `docs/spikes/2026-10-04-tab-sleep-spikes.md`.

## Tests

- **Offline:** pane classification from process-info (live, stale, idle shell, busy
  shell, undetected Claude); task detection by process group; the refusal list
  (unnamed, no transcript, working, running task, no id, combined); name lookup (last
  `custom-title`, none, missing transcript); record statuses and the tmp+rename writes;
  merge (including `failed` panes) and half-slept toggle; the wake skip outcomes (gone
  and running are dropped, busy stays `slept`); a record whose only slept pane is gone
  is deleted by one wake, and the next toggle sleeps the tab; exact `--resume`/`-r`
  token matching (a short name like "a" doesn't match unrelated argv) and stale flags; the typed command's quoting.
- **selftest:** sleep then wake on the throwaway server, with a stub agent (one leader
  plus a child in its group) standing in for Claude.

## Not in scope

- Stopping non-agent processes (dev servers and the like). Possibly a separate, explicit
  option later.
- Sleeping several tabs at once, a tab picker, or sleeping automatically after a period
  idle.
