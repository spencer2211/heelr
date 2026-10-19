# Claude Code adapter (status line)

Claude Code passes JSON to your status line script after every message. Add one line to your
own script (heelr never takes the `statusLine` setting); `$input` is the JSON your script read:

```sh
[ -n "$HERDR_PANE_ID" ] && (printf '%s' "$input" | heelr report >/dev/null 2>&1 &)
```

heelr reads `workspace.current_dir` and `worktree.name` / `worktree.branch` from it.
