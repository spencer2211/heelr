"""Per-tab state: the layout last applied to a tab and its nudged ratios."""
import json

import pytest

from heelr import core
from test_apply_safety import fake, heelr  # noqa: F401  (the offline Herdr fixture)


def test_apply_records_the_layout_on_the_tab(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert core.tab_record(tab) == {}
    assert heelr("apply", "main-right", "--tab", tab) == 0
    assert core.tab_record(tab)["layout"] == "main-right"


def test_apply_records_the_layout_even_when_already_in_it(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert heelr("apply", "main-right", "--tab", tab) == 0
    core.update_tab_record(tab, layout="grid")
    assert heelr("apply", "main-right", "--tab", tab) == 0     # nothing to do, but it IS main-right
    assert core.tab_record(tab)["layout"] == "main-right"


def test_dry_run_records_nothing(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert heelr("apply", "main-right", "--tab", tab, "--dry-run") == 0
    assert core.tab_record(tab) == {}


def test_a_failed_apply_records_nothing(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    fake.fail_at = 1
    assert heelr("apply", "main-right", "--tab", tab) == 1
    assert core.tab_record(tab) == {}


def test_apply_uses_the_tabs_nudged_ratio_for_that_layout_only(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    other, _ = fake.add_tab("w1", "other", 3)
    core.update_tab_record(tab, ratios={"main-right": 0.7})
    assert heelr("apply", "main-right", "--tab", tab) == 0
    assert abs(fake.tree(tab).ratio - 0.3) < 1e-6           # main-right: stack gets 1 - 0.7
    assert heelr("apply", "main-right", "--tab", other) == 0
    assert abs(fake.tree(other).ratio - 0.4) < 1e-6         # the preset's 0.6 elsewhere
    assert heelr("apply", "main-left", "--tab", tab) == 0
    assert abs(fake.tree(tab).ratio - 0.6) < 1e-6           # another layout on the same tab


def test_update_merges_and_keeps_other_fields(fake):
    core.update_tab_record("w1:t9", layout="grid")
    core.update_tab_record("w1:t9", ratios={"main-right": 0.5})
    assert core.tab_record("w1:t9") == {"layout": "grid", "ratios": {"main-right": 0.5}}


def test_a_broken_or_odd_record_reads_as_empty(fake):
    tab, _ = fake.add_tab("w1", "main", 3)
    p = core._tab_record_path(tab)
    p.parent.mkdir(parents=True, exist_ok=True)
    for junk in ("{not json", "[1, 2]", json.dumps({"layout": 5, "ratios": {"main-right": "big"}}),
                 json.dumps({"ratios": {"main-right": 1.5}})):
        p.write_text(junk)
        rec = core.tab_record(tab)
        assert not isinstance(rec.get("layout"), int)
        assert core.tab_ratio(tab, "main-right") is None
        assert heelr("apply", "main-right", "--tab", tab) == 0
        assert abs(fake.tree(tab).ratio - 0.4) < 1e-6


def test_tab_ids_are_made_safe_for_file_names():
    assert core._tab_record_path("w1:t../../x").parent.name == "tabs"


def test_the_record_keeps_the_pane_order_and_the_auto_switch(fake):
    core.update_tab_record("w1:t1", panes=["w1:p2", "w1:p1"], auto=False)
    assert core.tab_record("w1:t1") == {"panes": ["w1:p2", "w1:p1"], "auto": False}


@pytest.mark.parametrize("bad", [{"panes": "w1:p1"}, {"panes": ["w1:p1", 3]}, {"auto": "no"}, {"auto": 0}])
def test_a_malformed_pane_order_or_switch_is_dropped(fake, bad):
    p = core._tab_record_path("w1:t1")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bad))
    assert core.tab_record("w1:t1") == {}
