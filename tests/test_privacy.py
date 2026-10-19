"""Public-release guard: tracked files must not carry personal paths or private project names."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Generic patterns only. Maintainers keep their private patterns, one regex per line, in the
# git-ignored `.privacy-deny` at the repo root. Built from fragments so this file does not match itself.
GENERIC = [
    "gma" + "il",
    r"/home/(?!(?:user|you|u|someone)(?![\w.-]))[\w.-]+",  # a real home dir, not a placeholder
]
DENY_FILE = ROOT / ".privacy-deny"


def _load_deny(path):
    """Regex lines from the local deny file; [] when absent. A malformed line raises ValueError."""
    path = Path(path)
    if not path.is_file():
        return []
    lines = []
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            re.compile(line)
        except re.error as e:
            raise ValueError(f"{path.name}:{n}: bad regex {line!r}: {e}") from e
        lines.append(line)
    return lines


def _build_deny(path):
    return re.compile("|".join(f"(?:{p})" for p in GENERIC + _load_deny(path)), re.I)


DENY = _build_deny(DENY_FILE)
# The public GitHub handle (and its noreply address) is deliberately public.
ALLOW = re.compile("spen" + "cer2211", re.I)
EXCLUDED = {"tasks.md", ".privacy-deny"}  # private by design
EXCLUDED_PREFIXES = ("docs/research/",)  # private by design; third-party text
SKIP_DIRS = {".git", "__pycache__", ".worktrees", ".pytest_cache"}


def _git_files(root):
    """Tracked files, or None when root is not a git checkout (export, tarball)."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True)
    except OSError:
        return None
    if out.returncode:
        return None
    return [n for n in out.stdout.split("\n") if n]


def _walk_files(root):
    return [p.relative_to(root).as_posix() for p in sorted(root.rglob("*"))
            if p.is_file() and not SKIP_DIRS & set(p.relative_to(root).parts)]


def _hits(root, names, deny=DENY):
    hits = []
    for name in names:
        p = root / name
        if name in EXCLUDED or name.startswith(EXCLUDED_PREFIXES) or not p.is_file():
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        hits += [f"{name}:{n}" for n, line in enumerate(text.splitlines(), 1) if deny.search(ALLOW.sub("", line))]
    return hits


def test_tracked_files_have_no_private_names_or_paths(real_subprocess):
    names = _git_files(ROOT)
    if names is None:
        names = _walk_files(ROOT)
    assert not _hits(ROOT, names)


def test_walk_fallback_finds_hits_and_skips_excluded_dirs(tmp_path):
    (tmp_path / "ok.txt").write_text("fine\n")
    (tmp_path / "bad.txt").write_text("mail me at a" + "@gma" + "il.com\n")
    (tmp_path / "tasks.md").write_text("/home/" + "realname/x\n")
    (tmp_path / ".privacy-deny").write_text("gma" + "il\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "x").write_text("gma" + "il\n")
    names = _walk_files(tmp_path)
    assert sorted(names) == [".privacy-deny", "bad.txt", "ok.txt", "tasks.md"]
    assert _hits(tmp_path, names) == ["bad.txt:1"]


def test_not_a_git_checkout_returns_none(tmp_path, monkeypatch, real_subprocess):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert _git_files(tmp_path) is None


def test_public_handle_is_allowed(tmp_path):
    handle = "spen" + "cer2211"
    (tmp_path / "a.md").write_text(
        f"@{handle}\n28957500+{handle}@users.noreply.github.com\n")
    assert _hits(tmp_path, ["a.md"]) == []


def test_load_deny_ignores_comments_and_blank_lines(tmp_path):
    f = tmp_path / ".privacy-deny"
    f.write_text("# a comment\n\nexampleprivate\n   \nother.word\n")
    assert _load_deny(f) == ["exampleprivate", "other.word"]


def test_load_deny_missing_file_gives_nothing(tmp_path):
    assert _load_deny(tmp_path / ".privacy-deny") == []


def test_local_deny_file_adds_case_insensitive_hits(tmp_path):
    f = tmp_path / ".privacy-deny"
    f.write_text("exampleprivate\n")
    (tmp_path / "a.md").write_text("an ExamplePrivate word\nclean\n")
    deny = _build_deny(f)
    assert _hits(tmp_path, ["a.md"], deny) == ["a.md:1"]


def test_without_deny_file_only_generic_patterns_apply(tmp_path):
    deny = _build_deny(tmp_path / ".privacy-deny")
    (tmp_path / "a.md").write_text("exampleprivate\n")
    (tmp_path / "b.md").write_text("x@gma" + "il.com\n")
    (tmp_path / "c.md").write_text("/home/" + "realname/.config\n")
    assert _hits(tmp_path, ["a.md", "b.md", "c.md"], deny) == ["b.md:1", "c.md:1"]


def test_home_placeholders_are_allowed(tmp_path):
    (tmp_path / "a.md").write_text("/home/user/x /home/you/y /home/someone/z /home/u/w\n")
    assert _hits(tmp_path, ["a.md"]) == []


def test_home_path_with_a_real_looking_name_is_a_hit_and_each_placeholder_is_not(tmp_path):
    (tmp_path / "bad.md").write_text("/home/" + "alice/x\n")
    assert _hits(tmp_path, ["bad.md"]) == ["bad.md:1"]
    for i, name in enumerate(["user", "you", "u", "someone"]):
        (tmp_path / f"ok{i}.md").write_text(f"/home/{name}/x\n")
        assert _hits(tmp_path, [f"ok{i}.md"]) == [], name


def test_home_names_that_merely_start_with_a_placeholder_are_hits(tmp_path):
    for i, name in enumerate(["u-name", "user.x"]):
        (tmp_path / f"h{i}.md").write_text(f"/home/{name}/y\n")
        assert _hits(tmp_path, [f"h{i}.md"]) == [f"h{i}.md:1"], name


def test_malformed_deny_line_fails_loudly_naming_the_line(tmp_path):
    f = tmp_path / ".privacy-deny"
    f.write_text("fine\n# c\nbroken(\n")
    with pytest.raises(ValueError, match=r"\.privacy-deny:3.*broken\("):
        _load_deny(f)


def test_several_deny_lines_are_each_caught(tmp_path):
    f = tmp_path / ".privacy-deny"
    f.write_text("# c\nalpha-one\n\nbeta\\d+\n/gamma/path\n")
    for i, text in enumerate(["x alpha-one", "beta42", "see /gamma/path/y"]):
        (tmp_path / f"{i}.md").write_text(text + "\n")
    (tmp_path / "clean.md").write_text("nothing here\n")
    names = ["0.md", "1.md", "2.md", "clean.md"]
    assert _hits(tmp_path, names, _build_deny(f)) == ["0.md:1", "1.md:1", "2.md:1"]


def test_handle_stays_allowed_when_a_deny_line_matches_part_of_it(tmp_path):
    handle = "spen" + "cer2211"
    f = tmp_path / ".privacy-deny"
    f.write_text(handle[:5] + "\n")
    (tmp_path / "a.md").write_text(f"@{handle}\n")
    (tmp_path / "b.md").write_text(handle[:5] + " alone\n")
    assert _hits(tmp_path, ["a.md", "b.md"], _build_deny(f)) == ["b.md:1"]


def test_docs_research_is_skipped_but_sibling_docs_are_scanned(tmp_path):
    (tmp_path / "docs" / "research").mkdir(parents=True)
    (tmp_path / "docs" / "research" / "r.md").write_text("gma" + "il\n")
    (tmp_path / "docs" / "other.md").write_text("gma" + "il\n")
    names = ["docs/research/r.md", "docs/other.md"]
    assert _hits(tmp_path, names) == ["docs/other.md:1"]
