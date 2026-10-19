# main-center layout: design (2026-10-01)

A rule preset that puts the main pane in the middle column, between two stacks.

```
┌─────┬──────────┬─────┐
│  2  │          │  4  │
├─────┤    1     │     │
│  3  │          │     │
└─────┴──────────┴─────┘
```

## Shape

- The other `m = n - 1` panes split in order: the first `ceil(m / 2)` go to the left
  stack, the rest to the right stack. An odd count gives the left the extra pane.
- Each stack is an even `down` chain.
- `main_ratio` is the main column's width. The two stacks share the rest equally:
  `right(side)[left, right(main / (main + side))[main, right]]` with
  `side = (1 - main_ratio) / 2`.
- Preset default: `main_ratio = 0.5` (25 / 50 / 25).

## Edge cases

- 2 panes: the right stack would be empty, so the tree is `right(1 - main_ratio)[L, main]`.
  That looks like main-right; Herdr can't hold an empty column open.
- 1 pane: the main leaf alone. The preset has `add_missing = true` and
  `RULE_MIN["main-center"] = 2`, so a one-pane tab gets one new shell, the same as main-right.
- The main leaf is neither the first nor the last leaf, so the rule tests that assume
  "main is first, or last for main-right/main-bottom" need a main-center case.

## Changes

- `heelr/layouts.py`: add `main-center` to `RULES`, `RULE_MIN`, and `expand_rule`.
- `heelr/presets/main-center.toml`.
- `heelr/thumb.py`: sample at 5 panes so the menu thumbnail shows both stacks.
- Docs: README layout list, `docs/USER_GUIDE.md` and `docs/user-guide.html` preset tables.
- `tests/test_live.py`: include main-center in the preset round-trip on the throwaway server.
- `tasks.md`: a "Possible layouts" list (spiral, monocle if it cycles panes,
  main-right-wide, columns-main).

## Testing

TDD. Unit tests for the tree shape at 2-7 panes, main placement, ratios, the preset's
one-pane top-up, planner rebuild, and back after a cut apply on the fake Herdr.
Then `heelr selftest` on a throwaway server.

## Out of the repo

After it lands, `~/.config/heelr/config.toml` gets
`favourites = ["main-right", "main-center", "two-mains", "main-bottom", "agents-logs"]`
so menu keys stop shifting when presets are added.
