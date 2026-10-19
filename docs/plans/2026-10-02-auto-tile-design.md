# Auto-tile and the combined heelr plugin: design (2026-10-02)

A tab heelr has laid out keeps its layout when panes open, close or move in or out.
Agreed in a brainstorm. The tab-colours plugin becomes the one heelr plugin (display name
"heelr") that runs both features.

## Behaviour

- A new pane goes to the **end of the stack**. Main stays where it is.
- A closed pane: the layout closes the gap. If the main pane closes, the **top of the stack**
  becomes main.
- The tab's nudged ratio is kept. Focus stays where Herdr put it: the pane you were in, or
  the new one.
- No undo record is saved: `prefix+u` still undoes the last deliberate change.

## When it acts (all must hold)

1. `auto_tile = true` in `~/.config/heelr/config.toml`. **Off by default.**
2. The tab has a recorded layout (an `apply`, not since undone by `back` or replaced by a
   multi-tab apply; both already drop it) and that layout is a rule layout. Saved trees
   and multi-tab layouts are skipped.
3. Auto-tile isn't switched off for the tab (`"auto": false` in its record).
4. The tab's panes differ from the recorded pane list. A pane moving around *within* the tab
   doesn't trigger it.
5. It holds the apply lock. If another heelr change holds it, `auto` **waits** for it,
   up to 5 s; after that it logs and exits 0. Waiting rather than skipping matters: a pane
   opened while another change runs would otherwise be missed until the next pane event,
   which may never come. Runs are idempotent, so a queued run that finds nothing just exits.
6. No `heelr-park` tab is open in the workspace. The first time a park tab blocks it,
   `auto` logs that to auto.log, so a park tab left behind by a crash doesn't disable
   auto-tile without a trace.

## Off switches

- Everywhere: `auto_tile = false` (the default).
- One tab: `heelr auto-toggle` (prefix+a) flips `"auto"` in the tab's record and shows a
  notification, "auto-tile off for this tab" or "on". `apply` never resets it.

## Per-tab record

`tabs/<tab>.json` gains `"panes"`: pane ids in slot order, main first. It's written by every
heelr change that lays out the tab (apply, nudge's fallback, auto) and by nothing else.
promote writes it with main and the focused pane swapped, and only if the tab already has a
record (no record stays no record). nudge's resize path moves no panes, so it doesn't
write it. `"auto"` is optional and is only written by the toggle.

Accepted limitation: moving or swapping panes by hand within the tab doesn't update
`"panes"`. The next time a pane opens or closes, auto-tile re-binds from the recorded order
and undoes the hand-made swap. Use promote (which records) or re-apply instead.

## `heelr auto`

1. Read one snapshot. For each tab in it with a recorded rule layout and a pane set
   different from `"panes"` (also tabs whose recorded panes all closed: nothing to do), work
   out the new order: the recorded order minus panes that are gone, then the new panes in
   Herdr's order. If the old main is gone, the first remaining pane is main.
2. Target = the layout expanded for that many panes at the tab's ratio, with the leaves
   bound in that order (no role-based assignment). It never creates a pane: a layout's
   `add_missing` is ignored. Role layouts (`agents-logs`) are the exception to the order:
   their bands are bound by role, as `apply` does. Run it with `core.run` under the lock,
   then record `panes`.
3. Re-read and repeat while something changed, at most 3 passes, so bursts settle.
4. The tab is found from the snapshot, not from the event payload. That also covers
   `pane.closed`, where the pane can no longer be asked about.

Loop safety: heelr's own moves and creations happen under the lock, and `panes` is
recorded before the lock is released, so the events they cause (queued behind the lock)
find nothing to do. Pane ids survive `core.run`'s park round trip (R2), which the
`"panes"` record depends on. It's checked offline as well as live.

## Failures

`auto` runs from a plugin hook with no terminal. It catches every exception, and one tab's
failure doesn't stop the others. A notification is shown only when the error differs from
the last one logged. A clean run after a logged error logs `ok: auto-tile working again`,
so the same error later notifies again.
Interactive commands (apply, back, pick, promote, nudge) wait up to 2 s for the lock, so a
brief auto-tile run doesn't make them fail.

 Errors go to `state/auto.log` (one line
each, kept under 64 KB) plus one Herdr notification. The tab is left as it is, and
`auto` never raises out of the hook.

## Combined plugin

- The plugin keeps its directory (`plugins/tab-colours/`) and its id (`heelr.tab-colours`);
  only `name` becomes "heelr" and the description covers both features. Herdr keys the
  plugin's state and config dirs by id, and `tab_colours.py` uses the id for its fallback
  paths and as its metadata `--source`. A new id would start with empty slot memory and no
  pins, so every tab could change colour. And because Herdr installs plugins by reference
  (`plugin_root` is the checkout), renaming the directory would break the live install the
  moment main moves. `tab_colours.py` and its config are unchanged.
- The manifest keeps the colour hooks exactly as they are and adds `pane.created`,
  `pane.closed` and `pane.moved` entries running heelr's `auto`. Each entry is its own
  process, so one feature's failure or slowness can't block the other.
- The hook runs the heelr in the plugin's own checkout (`../../bin/heelr` relative to the
  plugin root), never whichever `heelr` is on PATH. That relies on install by reference:
  `plugins.json` records `plugin_root` as the checkout itself, not a copy.
- Herdr copies the hooks into `plugins.json` at install time, so the new auto hooks take
  effect only when the plugin is re-registered (rollout step 2), not when main moves.
- tab-autoname stays a separate plugin in its own repo.

## Rollout (after landing; each step confirmed with the user)

1. Back up `~/.config/herdr/config.toml` and `plugins.json`.
2. Re-register the plugin from `plugins/tab-colours` with `herdr plugin link <checkout>/plugins/tab-colours`
   so Herdr picks up the new hooks and name. Checked on a throwaway 0.9.3: linking an id
   that's already linked updates it in place (one `plugins.json` entry, same id, so the same
   state and config dirs). Check that colours are unchanged.
3. Add the `prefix+a` shell binding for `heelr auto-toggle`, after `herdr config check`
   shows no conflict.
4. `auto_tile = true` in the heelr config only when the user asks for it.

## Known cost

`core.run` moves panes through `heelr-park`, so a pane opening may flicker briefly. A
fast path for "one pane added to the end of the stack" can come later if it's annoying.

## Testing

TDD against the offline fake Herdr, which needs a `pane.closed`-style removal if it lacks
one. Cases:

- open, close, close main, a burst of several panes;
- globally off, off for the tab, park tab open (logged once), a fixed layout, no record;
- lock held: a pane opened while another change holds the lock is tiled once the lock is
  released (the queued run waits); a lock held past the timeout logs and exits 0;
- promote, then a pane opens: the promoted pane stays main;
- pane ids survive `core.run` (offline, in the fake);
- no loop: a second `auto` straight after does nothing;
- `back` and multi apply still stop auto-tile;
- the toggle round trip and its notification;
- a manifest test for the combined plugin: id unchanged, the colour hooks unchanged, and
  the auto hooks point at the checkout's heelr.

A live test in `heelr selftest` opens and closes panes with auto-tile on and checks pane
ids and processes survive.
