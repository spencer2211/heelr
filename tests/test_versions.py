"""Version gate: tested-versions data files and the patch-release relaxation."""
import pytest

from heelr import herdr


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Never read the real ~/.config/heelr/tested-versions."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("_HEELR_SELFTEST_ALLOW_VERSION", raising=False)
    monkeypatch.setattr(herdr, "_PACKAGE_TESTED", tmp_path / "pkg-tested.txt")
    (tmp_path / "pkg-tested.txt").write_text("# shipped\n0.9.1\n0.9.3\n\n")
    return tmp_path


def test_tested_versions_reads_package_file_ignoring_comments():
    assert herdr.tested_versions() == {"0.9.1", "0.9.3"}


def test_shipped_package_file_lists_at_least_0_9_3():
    from pathlib import Path
    shipped = Path(herdr.__file__).with_name("tested_versions.txt")
    assert {"0.9.1", "0.9.3"} <= herdr._read_versions(shipped)


def test_record_appends_to_user_file_once(isolated_config):
    assert herdr.record_tested("0.9.4") is True
    assert herdr.record_tested("0.9.4") is False  # already there
    assert herdr.record_tested("0.9.3") is False  # shipped as tested
    user = isolated_config / "cfg" / "heelr" / "tested-versions"
    assert user.read_text().split() == ["0.9.4"]
    assert "0.9.4" in herdr.tested_versions()


@pytest.mark.parametrize("v,verdict", [
    ("0.9.3", "tested"),
    ("0.9.4", "patch"),
    ("0.9.0", "patch"),
    ("0.10.0", "untested"),
    ("1.0.0", "untested"),
    ("0.9.4-rc1", "untested"),   # unknown format: refuse as before
    ("0.9", "untested"),
    ("", "untested"),
    (None, "untested"),
    ("v0.9.4", "untested"),
])
def test_gate_verdicts(v, verdict):
    assert herdr.gate(v) == verdict


def test_check_version_tested_is_silent(capsys):
    herdr.check_version({"version": "0.9.3"})
    assert capsys.readouterr().err == ""


def test_check_version_patch_warns_one_line_and_proceeds(capsys):
    herdr.check_version({"version": "0.9.4"})
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "0.9.4" in err and "selftest" in err


def test_check_version_new_minor_refuses_and_mentions_selftest():
    with pytest.raises(herdr.HerdrError, match="untested.*heelr selftest"):
        herdr.check_version({"version": "0.10.0"})


def test_check_version_prerelease_refuses():
    with pytest.raises(herdr.HerdrError):
        herdr.check_version({"version": "0.9.4-rc1"})


def test_allow_untested_overrides(capsys):
    herdr.check_version({"version": "1.0.0"}, allow_untested=True)
    assert capsys.readouterr().err == ""


def test_old_public_allow_version_name_is_not_honoured(monkeypatch):
    monkeypatch.setenv("HEELR_ALLOW_VERSION", "0.10.0")
    with pytest.raises(herdr.HerdrError):
        herdr.check_version({"version": "0.10.0"})


def test_selftest_allow_version_env_accepts_exactly_that_version(monkeypatch):
    monkeypatch.setenv("_HEELR_SELFTEST_ALLOW_VERSION", "0.10.0")
    herdr.check_version({"version": "0.10.0"})
    with pytest.raises(herdr.HerdrError):
        herdr.check_version({"version": "0.11.0"})


def test_parse_version():
    assert herdr.parse_version("0.9.3") == (0, 9, 3)
    assert herdr.parse_version("10.20.30") == (10, 20, 30)
    for bad in ("0.9.3-rc1", "0.9", "x", "", None, " 0.9.3", "0.9.3\n"):
        assert herdr.parse_version(bad) is None


def test_record_tested_onto_file_without_trailing_newline(isolated_config):
    f = herdr.user_tested_file()
    f.parent.mkdir(parents=True)
    f.write_text("0.9.4")  # hand-edited, no final newline
    assert herdr.record_tested("0.9.5") is True
    assert f.read_text() == "0.9.4\n0.9.5\n"
    assert {"0.9.4", "0.9.5"} <= herdr.tested_versions()
