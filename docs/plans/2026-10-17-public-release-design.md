# Public release: design

Status: approved in chat 2026-10-17. The written spec still needs the owner's review.

## Goal

Publish heelr as a public, MIT-licensed GitHub repo, `spencer2211/heelr`, that a
stranger can install, use and contribute to. After launch, development moves to
that repo. The current private repo is renamed `heelr-private` and kept as an
archive.

## Decisions

| Topic | Decision |
|---|---|
| Scope | Minimal: ship what exists today, minus the excluded files below. Splitting into core and optional tiers, and making sleep work without Claude, come later as 0.x releases. |
| Licence | MIT |
| Account | Personal (`spencer2211`), not an organisation |
| History | One fresh root commit, authored with the owner's GitHub noreply identity (`28957500+spencer2211@users.noreply.github.com`). It keeps `Co-Authored-By` and has no `Claude-Session` line. |
| After launch | Work moves to the public repo. `tasks.md` and `docs/research/` stay in the local checkout, untracked and git-ignored. |
| CI | Offline Python tests on every PR and push to main. No Herdr binary; `heelr selftest` stays a local step. |
| Left out for now | Funding (`FUNDING.yml` waits until the Sponsors and Ko-fi accounts exist), code of conduct, DCO, CLA, stale bot |
| tab-autoname | Stays private and separate. The README documents heelr's tab marks so that any auto-naming plugin can strip them. |

## 1. What leaves the public tree

| Path | Why |
|---|---|
| `bin/herdr-subreaper`, `bin/herdr-upgrade` | Personal upgrade tooling, not part of heelr |
| `tests/test_herdr_upgrade.py` and any other tests for the two scripts | Tests for removed code |
| `docs/spikes/2026-10-01-herdr-upgrade-automation.md`, `docs/spikes/2026-10-01-same-version-handoff.md` | They document the removed scripts |
| `.serena/`, `.mcp.json` | Personal editor and agent config |
| `tasks.md`, `docs/research/` | Internal backlog and research. Untracked and added to `.gitignore`. |

These references must be removed or reworded in the same change:
- `docs/USER_GUIDE.md`, the "Upgrading with `bin/herdr-upgrade`" section (around line 739). The rest of "After a Herdr upgrade" stays.
- `docs/user-guide.html`, the matching section.
- `tests/conftest.py:8`, the subreaper mention.
- Any other match of `grep -rn 'subreaper\|herdr-upgrade'` outside `.git`.

`docs/spikes/2026-10-01-hx-setup.sh` stays unless the plan finds it exists only for the removed scripts.

## 2. Scrub

- `tests/fixtures/snapshot_0.9.1.json`: replace the 7 real-looking v4 UUIDs with
  synthetic ones that are obviously fake but well formed, e.g.
  `00000000-0000-4000-8000-00000000000N`. Keep each mapping one-to-one so that
  cross-references inside the fixture still line up. Check the tests do not
  hard-code the old values.
- Gate before publishing: a grep of the final tree, with `.git` excluded, finds no
  personal name, home path, personal email address, private project name or real session or
  terminal ID. Placeholders such as `/home/user/...` are fine.

## 3. New files

| File | Content |
|---|---|
| `LICENSE` | MIT, "Copyright (c) 2026 The heelr authors" |
| `CONTRIBUTING.md` | Dev setup (clone, then symlink `bin/heelr`). Tests: `python3 -m pytest`, plus `heelr selftest`, which only touches a throwaway server. Flow: branch, then PR, then CI, then the code-owner's review. CHANGELOG entry and semver rules. Open an issue before a large PR. |
| `SECURITY.md` | Report privately through GitHub's private vulnerability reporting, not in issues. Supported version: the latest release. |
| `.github/CODEOWNERS` | `* @spencer2211` |
| `.github/ISSUE_TEMPLATE/bug.yml` | Issue form asking for heelr version, `herdr --version`, OS and terminal, steps to reproduce, expected and actual behaviour, and command output |
| `.github/ISSUE_TEMPLATE/feature.yml` | Issue form asking for the problem it solves, the proposed behaviour and alternatives considered |
| `.github/ISSUE_TEMPLATE/config.yml` | `blank_issues_enabled: false`, plus a contact link to Discussions for questions |
| `.github/pull_request_template.md` | Checklist: tests pass, CHANGELOG updated, docs updated if behaviour changed, issue linked |
| `.github/workflows/test.yml` | `ubuntu-latest`, `actions/setup-python` with Python versions as below, `pip install pytest`, `python3 -m pytest`. Runs on `pull_request` and on `push` to `main`. `permissions: contents: read`. |
| `.github/dependabot.yml` | `github-actions` ecosystem only, weekly |

Python versions in CI: the oldest version the code supports, plus the current
stable release. The plan finds the floor from the code; if nothing sets one, it
uses 3.11.

## 4. README

The README leads with role placement, the differentiator, then layouts. It gains:
- **Requirements:** Herdr, with the versions in `heelr/tested_versions.txt` (0.9.1 and 0.9.3 today); Python 3 at the floor found above; Linux or macOS. Platforms not tested are labelled as such.
- **Install:** clone, then `ln -s "$PWD/bin/heelr" ~/.local/bin/heelr`.
- **Keybindings:** the `config.toml` snippet for `prefix+m` and the others, already partly in the README, checked against current behaviour.
- **Tab marks:** heelr appends ` ↗N` to a tab label for N panes that are out of place, and prefixes `💤 ` to a sleeping tab. Other plugins that set tab labels should strip both. Include the regex heelr uses.
- **Licence** line and a link to CONTRIBUTING.

## 5. Publishing

Every step here needs the owner's explicit go at the time it runs.

1. heelr-reviewer approves the release branch in the private repo.
2. Build the release tree: `git checkout --orphan public` from the approved commit, untrack the excluded paths, and make a single commit with the noreply author.
3. Re-run the scrub gate and `python3 -m pytest` on that tree.
4. `gh repo rename heelr-private` on the current repo.
5. `gh repo create spencer2211/heelr --public`, then push `public:main` and tag `v0.4.0`, adding a CHANGELOG entry "first public release".
6. Repo settings: enable Discussions and private vulnerability reporting, and add a `herdr-upstream` label. Ruleset on `main`: require a PR, require the CI check to pass, require 1 approval from a code owner, block force-pushes and deletion. The repo admin (the owner) is a bypass actor, so the owner's own PRs can merge.
7. Point the local checkout at the new remote. `origin` becomes the public repo; the old one is kept as remote `private`. The local `main` is reset to the public root commit, with the owner's OK, because the histories differ. `tasks.md` and `docs/research/` stay as untracked local files.
8. Tell heelr-reviewer the new flow: from now on, an approval is a PR comment on the public repo, because GitHub does not let an account approve its own PR.

## 6. Testing and verification

- `python3 -m pytest` passes in the worktree after the removals.
- Reviewer: run the scrub grep, check every removed script has no dangling reference, and read the community files.
- `actionlint` on the workflow if it is available; otherwise the first CI run on the public repo is the check.
- After step 5: CI goes green on the first push, the community profile at `/community` lists README, licence, contributing, security, and issue and PR templates, and a test issue form renders. Any test issue is closed afterwards.

## Out of scope

The core/optional tiers, sleep without Claude, the funding files, a code of
conduct, PyPI or Homebrew packaging, and making tab-autoname public.
