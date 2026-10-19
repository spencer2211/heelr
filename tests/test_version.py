import pytest
import re
import tomllib
from pathlib import Path

import heelr
from heelr import cli

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_semver_without_v():
    assert re.fullmatch(r"\d+\.\d+\.\d+", heelr.__version__)


def test_cli_prints_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"heelr {heelr.__version__}"


def test_plugin_manifest_matches():
    m = tomllib.loads((ROOT / "plugins/tab-colours/herdr-plugin.toml").read_text())
    assert m["version"] == heelr.__version__


def test_changelog_has_this_version():
    assert f"## {heelr.__version__}" in (ROOT / "CHANGELOG.md").read_text()
