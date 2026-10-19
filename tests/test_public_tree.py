"""Public-release guard: personal tooling stays out of the tracked tree."""
import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

from heelr import core

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _real(real_subprocess):
    """Every test here runs `git ls-files`, which conftest's subprocess stub would otherwise block."""


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [n for n in out.stdout.split("\n") if n]


# Paths built from fragments so this file does not match its own reference scan.
_SR, _UP, _HX = "sub" + "reaper", "herdr" + "-upgrade", "hx" + "-setup"
REMOVED = [
    "bin/herdr-" + _SR, "bin/" + _UP, "tests/test_herdr_" + "upgrade.py",
    "docs/spikes/2026-10-01-" + _UP + "-automation.md",
    "docs/spikes/2026-10-01-same-version-handoff.md",
    "docs/spikes/2026-10-01-" + _HX + ".sh", ".mcp" + ".json",
]


def test_personal_tooling_not_tracked():
    files = tracked()
    assert [p for p in REMOVED if p in files] == []
    assert [p for p in files if p.startswith(".serena/")] == []


def test_no_references_to_removed_tooling():
    # Built from fragments so this file does not match itself.
    needles = ["sub" + "reaper", "herdr" + "-upgrade", "herdr" + "_upgrade", "hx" + "-setup"]
    skip = ("docs/plans/", "docs/research/")
    hits = []
    for name in tracked():
        if name.startswith(skip) or name in ("tasks.md", "CHANGELOG.md"):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        # The kept "After a Herdr upgrade" section's anchor legitimately contains the needle.
        text = path.read_text(errors="ignore").replace("after-a-herdr-upgrade", "")
        hits += [f"{name}: {n}" for n in needles if n in text]
    assert hits == []


def test_private_files_are_gitignored():
    lines = (ROOT / ".gitignore").read_text().split("\n")
    assert "tasks.md" in lines
    assert "docs/research/" in lines


UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
SYNTHETIC_PREFIX = "00000000-0000-4000-8000-"


def test_fixture_ids_are_synthetic():
    bad = {}
    for name in tracked():
        if not name.startswith("tests/"):
            continue
        try:
            text = (ROOT / name).read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for m in UUID4.findall(text):
            if not m.startswith(SYNTHETIC_PREFIX):
                bad.setdefault(name, set()).add(m)
    assert bad == {}


COMMUNITY = [
    "LICENSE", "CONTRIBUTING.md", "SECURITY.md", ".github/CODEOWNERS",
    ".github/ISSUE_TEMPLATE/bug.yml", ".github/ISSUE_TEMPLATE/feature.yml",
    ".github/ISSUE_TEMPLATE/config.yml", ".github/pull_request_template.md",
    ".github/workflows/test.yml", ".github/dependabot.yml",
]


def test_community_files():
    # Existence is checked on disk: new files only show in `git ls-files` after `git add`.
    assert [p for p in COMMUNITY if not (ROOT / p).is_file()] == []
    text = lambda p: (ROOT / p).read_text()
    assert text("LICENSE").startswith("MIT License")
    assert "The heelr authors" in text("LICENSE")
    assert "* @spencer2211" in text(".github/CODEOWNERS")
    assert "blank_issues_enabled: false" in text(".github/ISSUE_TEMPLATE/config.yml")
    wf = text(".github/workflows/test.yml")
    for needle in ("pull_request", '"3.11"', '"3.13"', "contents: read"):
        assert needle in wf
    assert "github-actions" in text(".github/dependabot.yml")


def test_readme_for_public():
    readme = (ROOT / "README.md").read_text()
    for needle in ("## Requirements", "## Install", "## Tab marks", "LICENSE", "[[keys.command]]"):
        assert needle in readme
    versions = [ln.strip() for ln in (ROOT / "heelr" / "tested_versions.txt").read_text().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
    assert versions
    for v in versions:
        assert v in readme
    spec = importlib.util.spec_from_file_location(
        "tab_colours_for_readme", ROOT / "plugins" / "tab-colours" / "tab_colours.py")
    tab_colours = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tab_colours)
    assert tab_colours.DRIFT_MARK.pattern.strip() in readme
    assert core.SLEEP_MARK in readme
