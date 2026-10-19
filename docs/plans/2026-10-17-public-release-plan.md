# Public release: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the heelr tree ready to publish as the public, MIT-licensed repo `spencer2211/heelr`. Publishing itself is Task 6 and needs the owner's go.

**Architecture:** Every change is a file edit on branch `public-release` (worktree `.worktrees/public-release`). A new test file, `tests/test_public_tree.py`, pins the release rules: excluded paths gone, community files present, fixture IDs synthetic, README facts. Personal files (`tasks.md`, `docs/research/`) stay tracked on this branch and are untracked only in the orphan public commit, so that fast-forwarding main never deletes them from the local checkout.

**Tech Stack:** Python 3 (stdlib only), pytest, GitHub Actions, GitHub issue forms (YAML).

**Spec:** `docs/plans/2026-10-17-public-release-design.md`

## Global Constraints

- Python floor is **3.11**, because the code uses `tomllib`. CI tests on 3.11 and 3.13.
- No personal name, home path, personal email address or private project name in any tracked file. `tests/test_privacy.py` enforces this. The GitHub handle `spencer2211` is the only allowed exception (Task 1).
- LICENSE copyright line: `Copyright (c) 2026 The heelr authors` (ruling: the "no personal names in tracked files" rule beats the spec's personal-name line).
- Synthetic IDs use the form `00000000-0000-4000-8000-0000000000NN`.
- Never touch the live Herdr server. Tests are offline only, plus `heelr selftest` on a throwaway server if a task asks for it.
- Never use bare `git stash`. Commit on `public-release` only. Do not push, and do not touch main.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. This branch's commits may also carry a `Claude-Session` line; the public commit drops it (Task 6).

## Review Focus

1. **A fast-forward of main that deletes local files.** If this branch untracked `tasks.md` or `docs/research/`, merging it would delete them from the main checkout. Task 2's test asserts `tasks.md` and `docs/research/` are git-ignored.
2. **Tests that pass here only because of the owner's machine.** CI has no `herdr` binary, no `~/.config/heelr` and a different HOME. Task 4 runs the suite with a temporary HOME, a PATH without herdr, and under `python3.11`.
3. **Dangling references to removed files:** links, TOC anchors, conftest docstrings, spike docs. Task 2's test greps the tracked tree.
4. **A privacy allowance that is too wide.** Allowing `spencer2211` must not also let a deny line that matches part of the handle flag the handle, while the same text alone is still a hit. Task 1 pins both cases.
5. **README tab-mark rule out of sync with the code.** Task 5's test checks that the regex printed in the README matches `plugins/tab-colours/tab_colours.py` `DRIFT_MARK.pattern`, and that the sleep prefix matches `heelr.core.SLEEP_MARK`.

---

### Task 1: Privacy guard allows the public handle

The baseline suite has 1 failure today: `test_privacy` flags the spec's GitHub handle and the personal name.

**Files:**
- Modify: `tests/test_privacy.py`
- Modify: `docs/plans/2026-10-17-public-release-design.md` (lines 19 and 58)

**Interfaces:** Produces `ALLOW = re.compile(r"spencer2211", re.I)`. `_hits` blanks ALLOW matches out of each line before it runs `DENY.search`.

- [ ] **Step 1: Add tests**
  - `test_public_handle_is_allowed(tmp_path)`: a file with `@spen` + `cer2211` and the noreply address `28957500+spen` + `cer2211@users.noreply.github.com` gives no hits. Build both strings from fragments.
  - Synthetic-word tests (no real names): a git-ignored `.privacy-deny` in `tmp_path` holding `exampleprivate` makes a file with that word a hit; with no deny file only the generic patterns apply; a deny line matching part of the handle does not flag the handle.
- [ ] **Step 2:** Run `python3 -m pytest tests/test_privacy.py -q`. Expected: the handle test FAILS.
- [ ] **Step 3:** Implement ALLOW. In the spec, line 19 becomes "authored with the owner's GitHub noreply identity (`28957500+spencer2211@users.noreply.github.com`)" and line 58 becomes `MIT, "Copyright (c) 2026 The heelr authors"`.
- [ ] **Step 4:** Run `python3 -m pytest -q`. Expected: all pass, 0 failed.
- [ ] **Step 5:** Commit: `privacy guard: allow the public GitHub handle; spec drops the personal name`.

### Task 2: Remove personal tooling from the tree

**Files:**
- Delete with `git rm`: `bin/herdr-subreaper`, `bin/herdr-upgrade`, `tests/test_herdr_upgrade.py`, `docs/spikes/2026-10-01-herdr-upgrade-automation.md`, `docs/spikes/2026-10-01-same-version-handoff.md`, `docs/spikes/2026-10-01-hx-setup.sh`, `.mcp.json`, and the whole of `.serena/`.
- Modify: `tests/conftest.py:8`. Drop "the hx-setup tests run /bin/sh, the subreaper usage check runs python" and replace it with a neutral example taken from a remaining `real_subprocess` user (`grep -rln real_subprocess tests`).
- Modify: `docs/spikes/2026-10-05-role-placement-spikes.md:3`. Replace the hx-setup path with "an isolated throwaway Herdr server".
- Modify: `docs/USER_GUIDE.md`. Delete lines 739–755, the "Upgrading with `bin/herdr-upgrade` (prototype)" subsection, up to the next `##`.
- Modify: `docs/user-guide.html`. Delete the matching `<h3>Upgrading with …</h3>` block and its paragraphs. There is no generator, so edit it by hand.
- Modify: `.gitignore`. Append `tasks.md` and `docs/research/`. Do **not** untrack them (Review Focus 1).
- Create: `tests/test_public_tree.py`.

**Interfaces:** Produces `tests/test_public_tree.py`, with helpers `tracked() -> list[str]` (`git ls-files`) and `ROOT`. Tasks 3–5 add their tests to this file.

- [ ] **Step 1: Write tests**
  - `test_personal_tooling_not_tracked`: none of the deleted paths above is in `tracked()`, and no tracked path starts with `.serena/`.
  - `test_no_references_to_removed_tooling`: no tracked file outside `docs/plans/`, `tasks.md` and `docs/research/` contains `subreaper`, `herdr-upgrade`, `herdr_upgrade` or `hx-setup`. Build the needles from fragments so this test file doesn't match itself.
  - `test_private_files_ignored_but_still_tracked_here`: `.gitignore` lines include `tasks.md` and `docs/research/`, and `tasks.md` is in `tracked()`.
- [ ] **Step 2:** Run `python3 -m pytest tests/test_public_tree.py -q`. Expected: 3 FAIL.
- [ ] **Step 3:** Make the deletions and edits above.
- [ ] **Step 4:** Run `python3 -m pytest -q`. Expected: all pass, with fewer tests in total because `test_herdr_upgrade.py` is gone.
- [ ] **Step 5:** Commit: `public tree: drop personal upgrade tooling, editor config and their docs`.

### Task 3: Synthetic IDs in the fixture

**Files:** Modify `tests/fixtures/snapshot_0.9.1.json`, and add a test to `tests/test_public_tree.py`.

- [ ] **Step 1:** Add `test_fixture_ids_are_synthetic`. Every v4 UUID in tracked files under `tests/` (regex `[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}`) starts with `00000000-0000-4000-8000-`.
- [ ] **Step 2:** Run it. Expected: FAIL, listing the 7 IDs from snapshot_0.9.1.json.
- [ ] **Step 3:** Replace the 7 distinct IDs with `00000000-0000-4000-8000-000000000101` … `…107`, in order of first appearance. Each ID appears twice; both copies get the same new value. Before replacing, check that no `.py` file hard-codes the old values (`grep -rn 28a27358 tests` and so on for each ID).
- [ ] **Step 4:** Run `python3 -m pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit: `fixtures: synthetic terminal ids in the 0.9.1 snapshot`.

### Task 4: Licence, community files and CI

**Files to create:**
- `LICENSE`: standard MIT text, with copyright line `Copyright (c) 2026 The heelr authors`.
- `CONTRIBUTING.md`, covering:
  - setup: clone, then `ln -s "$PWD/bin/heelr" ~/.local/bin/heelr`;
  - tests: `python3 -m pytest` (offline), and `heelr selftest`, which runs on a throwaway server and never touches yours;
  - flow: branch, then PR, then CI, then code-owner review;
  - a CHANGELOG entry under "Unreleased", following the semver rules quoted from CHANGELOG.md's header;
  - "open an issue before a large PR";
  - security reports go to SECURITY.md.
- `SECURITY.md`: report through GitHub private vulnerability reporting (the repo's Security tab, then "Report a vulnerability"), never in a public issue. Only the latest release is supported.
- `.github/CODEOWNERS`: `* @spencer2211`.
- `.github/ISSUE_TEMPLATE/bug.yml`: an issue form. Required fields: heelr version (`heelr --version`, or the commit), `herdr --version`, OS and terminal, steps, expected, actual. Optional textarea: output, with `render: text`. Label: `bug`.
- `.github/ISSUE_TEMPLATE/feature.yml`. Required: the problem. Also: proposed behaviour, and optional alternatives. Label: `enhancement`.
- `.github/ISSUE_TEMPLATE/config.yml`: `blank_issues_enabled: false`, plus one `contact_links` entry, "Questions", pointing to `https://github.com/spencer2211/heelr/discussions`.
- `.github/pull_request_template.md`: checkboxes for tests pass, CHANGELOG updated, docs updated if behaviour changed, and linked issue.
- `.github/workflows/test.yml`, with name `tests`:
  - runs `on: pull_request` and `push: branches: [main]`, with `permissions: contents: read`;
  - one job, `pytest`, on `ubuntu-latest`;
  - a matrix with `python-version: ["3.11", "3.13"]`;
  - steps: `actions/checkout@v4`, `actions/setup-python@v5`, `pip install pytest`, `python -m pytest -q`.
- `.github/dependabot.yml`: version 2, `package-ecosystem: github-actions`, directory `/`, weekly.

If `heelr --version` does not exist, the bug form asks for "heelr commit (`git -C <checkout> rev-parse --short HEAD`)" instead. Don't add a version command; that is out of scope.

- [ ] **Step 1:** Add `test_community_files` to `tests/test_public_tree.py`, with string checks only (no YAML library):
  - every file above exists;
  - LICENSE starts with `MIT License` and contains `The heelr authors`;
  - CODEOWNERS contains `* @spencer2211`;
  - config.yml contains `blank_issues_enabled: false`;
  - test.yml contains `pull_request`, `"3.11"`, `"3.13"` and `contents: read`;
  - dependabot.yml contains `github-actions`.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3:** Create the files.
- [ ] **Step 4:** Run `python3 -m pytest -q`. Expected: all pass.
- [ ] **Step 5 (Review Focus 2):** Run the suite the way CI will see it:
  - `env -i HOME="$(mktemp -d)" PATH=/usr/bin:/bin python3.11 -m pytest -q`
  - `env -i HOME="$(mktemp -d)" PATH=/usr/bin:/bin python3 -m pytest -q`

  Expected: both pass, with skips allowed. If either fails, fix the test so it doesn't depend on the machine, using systematic-debugging. Never mark a test skip just to get it green. If `actionlint` is installed, run it on `test.yml`.
- [ ] **Step 6:** Commit: `community files, MIT licence and CI (pytest on 3.11 and 3.13)`.

### Task 5: README for strangers, and CHANGELOG

**Files:** Modify `README.md` and `CHANGELOG.md`, and add a test to `tests/test_public_tree.py`.

The README order after this task:
1. Title and a one-line pitch that leads with role placement: agents' panes go to the right workspace and tab for their repo and task. Layouts come second.
2. The existing command block.
3. New `## Requirements`:
   - Herdr, tested on the versions in `heelr/tested_versions.txt` (list them);
   - Python 3.11+, stdlib only;
   - Linux. macOS is untested.
4. New `## Install`: the clone and symlink commands, then "add the keys below".
5. The existing `## Keys` section, with its snippet checked against `docs/USER_GUIDE.md`.
6. The existing `## Tab colours plugin` section.
7. New `## Tab marks`. heelr appends ` ↗N` for N panes in a tab that belong elsewhere, and prefixes `💤 ` to a sleeping tab. A plugin that sets tab labels should strip both: the suffix matches the regex ` ↗\d+$`, and the prefix is the literal `💤 `.
8. New `## Contributing and licence`: links to CONTRIBUTING.md and SECURITY.md, and "MIT, see LICENSE".

CHANGELOG `## Unreleased` gains: "First public release: MIT licence, contributing guide, issue forms, CI. Removed the personal upgrade tooling (`bin/herdr-upgrade`, `bin/herdr-subreaper`)."

- [ ] **Step 1:** Add `test_readme_for_public`:
  - the README contains `## Requirements`, `## Install`, `## Tab marks` and `LICENSE`;
  - every version in `tested_versions.txt` appears in the README;
  - the README contains `tab_colours.DRIFT_MARK.pattern.strip()` minus its leading space (load the plugin module via `importlib.util.spec_from_file_location`);
  - the README contains `heelr.core.SLEEP_MARK`.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3:** Edit README.md and CHANGELOG.md as above.
- [ ] **Step 4:** Run `python3 -m pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit: `README: requirements, install, tab marks, licence; changelog`.

### Task 6: Publish (owner-gated; not for subagents)

This runs only after heelr-reviewer approves the branch tip and the owner says go for each step. The controller runs it; no subagent does. It follows spec §5:

1. Fast-forward main to the approved SHA in the main checkout, run `python3 -m pytest -q`, and push to `origin`, which is still private.
2. In a scratch clone (`git clone <main-checkout> "$scratch/pub"`):
   - `git checkout --orphan public`, then `git rm -r --cached tasks.md docs/research`, then `rm -rf tasks.md docs/research`;
   - rename CHANGELOG `## Unreleased` to `## 0.4.0`;
   - `git -c user.name='<owner name>' -c user.email='28957500+spencer2211@users.noreply.github.com' commit` with message `heelr 0.4.0: first public release` and only the `Co-Authored-By` trailer;
   - `git tag v0.4.0`.
3. Gate: `python3 -m pytest -q` passes in the scratch clone, `git ls-files | grep -E '^(tasks.md|docs/research/)'` prints nothing, and the privacy test passes.
4. `gh repo rename heelr-private -R spencer2211/heelr`.
5. `gh repo create spencer2211/heelr --public --description "<one line from README>"`, then `git push <new> public:main v0.4.0`.
6. Settings:
   - `gh api -X PATCH repos/spencer2211/heelr -f has_discussions=true`;
   - private vulnerability reporting: `gh api -X PUT repos/spencer2211/heelr/private-vulnerability-reporting`;
   - `gh label create herdr-upstream`;
   - a ruleset on `~DEFAULT_BRANCH`: `pull_request` with 1 required approval and `require_code_owner_review`; `required_status_checks` set to `pytest (3.11)` and `pytest (3.13)`; `non_fast_forward`; `deletion`; with `bypass_actors` set to the repository admin role (`actor_id` 5, `RepositoryRole`), `bypass_mode: always`.
7. Local checkout:
   - `git remote rename origin private`, then `git remote add origin <public>`, then `git fetch origin`;
   - with the owner's OK, `git checkout -B main origin/main`, keeping `tasks.md` and `docs/research/` as untracked files. Back them up to the scratchpad first.
8. Verify:
   - the first CI run on main is green;
   - `gh api repos/spencer2211/heelr/community/profile` shows README, license, contributing, security and templates present;
   - the issue form renders.

   Then message heelr-reviewer: from now on, approvals are PR comments on the public repo.
