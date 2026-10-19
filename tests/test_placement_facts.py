import json
from pathlib import Path

import pytest

from heelr import core, facts


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock"))
    return tmp_path


def make_repo(root: Path, branch="main", origin_head="main"):
    g = root / ".git"
    (g / "refs/heads").mkdir(parents=True)
    (g / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    if origin_head:
        (g / "refs/remotes/origin").mkdir(parents=True)
        (g / "refs/remotes/origin/HEAD").write_text(f"ref: refs/remotes/origin/{origin_head}\n")
    return root


def make_worktree(repo: Path, name: str, branch: str) -> Path:
    wt = repo / ".worktrees" / name
    wt.mkdir(parents=True)
    gd = repo / ".git/worktrees" / name
    gd.mkdir(parents=True)
    (gd / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    (gd / "commondir").write_text("../..\n")
    (wt / ".git").write_text(f"gitdir: {gd}\n")
    return wt


def test_main_checkout(state):
    r = make_repo(state / "heelr")
    (r / "sub").mkdir()
    f = facts.git_facts(str(r / "sub"))
    assert (f.repo, f.worktree, f.branch, f.default_branch) == ("heelr", None, "main", "main")
    assert f.root == str(r)


def test_worktree_maps_to_its_main_repo(state):
    r = make_repo(state / "heelr")
    wt = make_worktree(r, "GH-330", "fix/GH-330-crash")
    f = facts.git_facts(str(wt))
    assert (f.repo, f.worktree, f.branch) == ("heelr", "GH-330", "fix/GH-330-crash")
    assert f.root == str(r)


def test_submodule_is_its_own_repo_not_a_worktree(state):          # Review Focus 1
    sup = make_repo(state / "super")
    mod = sup / "lib"
    mod.mkdir()
    md = sup / ".git/modules/lib"
    md.mkdir(parents=True)
    (md / "HEAD").write_text("ref: refs/heads/main\n")
    (mod / ".git").write_text("gitdir: ../.git/modules/lib\n")
    f = facts.git_facts(str(mod))
    assert (f.repo, f.worktree, f.root) == ("lib", None, str(mod))


def test_detached_head_and_missing_folder(state):                  # Review Focus 5
    r = make_repo(state / "heelr")
    (r / ".git/HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
    assert facts.git_facts(str(r)).branch is None
    gone = facts.git_facts(str(state / "deleted/folder"))
    assert gone == facts.GitFacts(None, None, None, None, None)


def test_packed_refs_match_whole_branch(state):
    r = make_repo(state / "heelr", origin_head=None)
    (r / ".git/packed-refs").write_text("abc refs/heads/maintenance\n")
    assert facts.git_facts(str(r)).default_branch is None


def test_not_a_repo(state):
    (state / "plain").mkdir()
    assert facts.git_facts(str(state / "plain")).repo is None


def test_report_tag_and_rekey(state):
    facts.save_report("w1:p2", {"cwd": "/x", "junk": 1})
    facts.set_tag("w1:p2", "GH-9")
    core.write_atomic(core.state_dir() / "back" / "t.json", json.dumps({"created": [{"pane": "w1:p2"}]}))
    assert facts.rekey("w1:p2", "w2:p7") >= 2
    p = facts.load_pane("w2:p7")
    assert p["report"]["cwd"] == "/x" and p["tag"] == "GH-9"
    assert not facts._pane_path("w1:p2").exists()
    assert "w2:p7" in (core.state_dir() / "back" / "t.json").read_text()


def test_rekey_does_not_touch_prefix_matches(state):
    core.write_atomic(core.state_dir() / "x.json", json.dumps({"a": "w1:p2", "b": "w1:p21"}))
    facts.rekey("w1:p2", "w2:p1")
    assert json.loads((core.state_dir() / "x.json").read_text()) == {"a": "w2:p1", "b": "w1:p21"}


def test_rekey_follows_sleep_records_too(state):
    p = core.state_dir() / "sleep" / "tab1.json"
    core.write_atomic(p, json.dumps({"tab": "t", "panes": [{"pane": "w1:p2", "status": "slept"}]}))
    before = p.read_bytes()
    core.write_atomic(core.state_dir() / "x.json", json.dumps({"a": "w1:p2"}))
    assert facts.rekey("w1:p2", "w2:p1") == 2          # x.json and the sleep record
    assert "w2:p1" in p.read_text() and "w1:p2" not in p.read_text()


def test_default_branch_found_only_in_packed_refs(state):
    r = make_repo(state / "heelr", origin_head=None)
    (r / ".git/packed-refs").write_text("abc refs/heads/main\n")
    assert facts.git_facts(str(r)).default_branch == "main"


def test_rekey_leaves_an_alias_that_report_and_tag_follow(state):            # G3
    facts.rekey("w1:p2", "w2:p7")
    facts.set_tag("w1:p2", "GH-9")                       # the shell still says w1:p2
    facts.save_report("w1:p2", {"cwd": "/x"})
    assert facts.load_pane("w2:p7")["tag"] == "GH-9" and facts.load_pane("w2:p7")["report"]["cwd"] == "/x"
    assert facts.load_pane("w1:p2")["tag"] == "GH-9"      # readers follow it too
    assert not facts._pane_path("w1:p2").exists()
    facts.rekey("w2:p7", "w3:p1")                          # moved again: the old id still lands on the newest
    assert facts.resolve("w1:p2") == "w3:p1"


def test_alias_cycles_and_reuse_do_not_loop(state):                          # G3
    facts.rekey("a", "b")
    facts.rekey("b", "a")                                  # moved back, the id came round again
    assert facts.resolve("a") == "a" and facts.resolve("b") == "a"
    core.write_atomic(core.state_dir() / "pane-alias.json", json.dumps({"x": "y", "y": "x"}))
    assert facts.resolve("x") in ("x", "y")                # terminates


def test_alias_map_is_bounded_and_forget_prunes_it(state):                   # G3
    for i in range(facts.ALIAS_MAX + 50):
        facts.rekey(f"o{i}", f"n{i}")
    al = json.loads((core.state_dir() / "pane-alias.json").read_text())
    assert len(al) == facts.ALIAS_MAX and "o0" not in al and f"o{facts.ALIAS_MAX + 49}" in al
    facts.forget(f"n{facts.ALIAS_MAX + 49}")
    assert f"o{facts.ALIAS_MAX + 49}" not in json.loads((core.state_dir() / "pane-alias.json").read_text())


def test_worktree_of_a_bare_repo_is_named_after_the_bare_dir(state):             # G5
    bare = state / "heelr.git"
    (bare / "refs/heads").mkdir(parents=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    (bare / "refs/heads/main").write_text("x")
    gd = bare / "worktrees/GH-5"
    gd.mkdir(parents=True)
    (gd / "HEAD").write_text("ref: refs/heads/GH-5\n")
    (gd / "commondir").write_text("../..\n")
    wt = state / "checkouts/GH-5"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {gd}\n")
    (bare / ".heelr.toml").write_text('name = "hl"\n')
    f = facts.git_facts(str(wt))
    assert (f.repo, f.worktree, f.root, f.default_branch) == ("heelr.git", "GH-5", str(bare), "main")
    from heelr import key
    assert key.repo_config(f.root) == {"name": "hl"}                     # .heelr.toml read from that root


def test_worktree_of_a_submodule_is_named_after_the_submodule(state):             # G5
    sup = make_repo(state / "super")
    md = sup / ".git/modules/lib"
    (md / "refs/heads").mkdir(parents=True)
    (md / "HEAD").write_text("ref: refs/heads/main\n")
    gd = md / "worktrees/GH-6"
    gd.mkdir(parents=True)
    (gd / "HEAD").write_text("ref: refs/heads/GH-6\n")
    (gd / "commondir").write_text("../..\n")
    wt = state / "lib-wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gd}\n")
    f = facts.git_facts(str(wt))
    assert (f.repo, f.worktree) == ("lib", "GH-6")
