# promote, nudge and the carousel menu: design (2026-10-01)

Three ways to change a tab's layout faster, agreed in a brainstorm.

## Per-tab state

`<state>/heelr/tabs/<tab>.json`: `{"layout": "main-right", "ratios": {"main-right": 0.7}}`.

- A single-tab `apply` records `layout`. `back` and multi-tab applies drop it from the
  tabs they change (nudge then asks for a layout, the menu starts at the top).
- `nudge` records `ratios[layout]`. A later `apply` of that layout on that tab uses it in
  place of the preset's `main_ratio`. Other tabs and the preset keep their own size.
- Written atomically under the apply lock. A missing or broken file means "no record".
- Tab ids reused after a server restart can inherit an old record: worst case a stale
  highlight or ratio (same family as the deferred pane-id-reuse item).

## promote (`heelr promote`, prefix+Enter)

- The largest pane in the tab is "main" (ties: top-left first). Works in every layout.
- Focused pane already largest: a notification says "already main", nothing changes.
- Otherwise one `herdr pane swap --source-pane <focused> --target-pane <largest>`. Herdr
  0.9.3 swaps exactly the two panes and focuses the source, so the promoted pane keeps
  focus in the main slot. Nothing goes through heelr-park.
- Saves the undo record and sets `last = tab` first, under the apply lock, so
  `prefix+u` puts the panes back.

## nudge (`heelr nudge +|-`, prefix+= / prefix+-)

- Needs a recorded rule layout with a main area (any rule but grid / even-*). Otherwise a
  notification says why and nothing changes.
- New ratio = current (recorded, else the layout's `main_ratio`) ± 0.1, kept within
  0.2-0.8. At a bound: "already at the limit", nothing changes.
- Target = the layout expanded with the new ratio and bound like `apply`. If the tab has
  the same structure apart from ratios, each split whose ratio differs is set with
  `herdr pane resize` (no panes move). Otherwise it falls back to a normal apply with the
  new ratio (undo recorded as usual).
- Resize driving: for split S and change d > 0, resize the leaf of S.a that touches the
  divider (`right`/`down`, amount d); for d < 0, the leaf of S.b that touches it
  (`left`/`up`, amount -d). Herdr resizes the nearest such split, which is S.
- No undo record: `-` reverses `+`, and nudges don't fill the undo history.

## Carousel (`heelr pick`, prefix+m)

- One sideways-scrolling row of layout thumbnails, favourites first then alphabetical,
  as before. As many cards as fit; the row scrolls to keep the highlight in view, with
  ◀ / ▶ marking more.
- Starts on the tab's recorded layout, else the first entry.
- ←/→ (and h/l) move and wrap; Enter applies the highlighted one; a layout's key applies
  it at once; `b` back; `q`/Esc cancel.
- Too narrow for 2 cards: a plain list with a `>` marker, same keys.
- After landing (approved by the user): the prefix+m popup gets shorter (height 26 -> 16), and
  three `[[keys.command]]` shell bindings are added for promote and nudge. Herdr 0.9.3's
  `config check` accepts prefix+enter, prefix+= and prefix+- with no conflict.

## Testing

TDD against the offline fake Herdr, which gains `pane swap` and `pane resize` modelled on
what a throwaway 0.9.3 server did. `test_live.py` gets promote and nudge round trips, run
by `heelr selftest`.
