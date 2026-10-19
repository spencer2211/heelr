"""The prefix+m carousel: one scrolling row of layout cards with a highlight."""
import pytest

from heelr import cli, core
from test_apply_safety import fake, heelr  # noqa: F401


from heelr.layouts import PRESET_DIR

NAMES = sorted(p.stem for p in PRESET_DIR.glob("*.toml"))   # the 11 presets: always loadable
KEYS = cli.MENU_KEYS[: len(NAMES)]


@pytest.fixture(autouse=True)
def no_user_layouts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def marker_col(lines):
    """Column where the highlight bar under the selected card starts."""
    row = next(l for l in lines if "━" in l)
    return row.index("━")


def test_menu_keys_leave_out_the_menus_own_letters():
    for k in "bqhl":
        assert k not in cli.MENU_KEYS


@pytest.mark.parametrize("seq,key", [("\x1b[C", "RIGHT"), ("\x1b[D", "LEFT"), ("\x1bOC", "RIGHT"),
                                     ("\x1bOD", "LEFT"), ("\x1b[1;1C", "RIGHT"), ("\r", "\r"),
                                     ("\x1b[13u", "\r"), ("\x1b", "\x1b"), ("2", "2")])
def test_decode_key_knows_arrows_and_enter(seq, key):
    assert cli._decode_key(seq) == key


def test_carousel_highlights_the_selected_card_and_names_it_in_full():
    lines = cli._carousel(NAMES, KEYS, set(), 0, cols=96, rows=16)
    assert lines[0].rstrip().endswith(NAMES[0])
    first = marker_col(lines)
    lines = cli._carousel(NAMES, KEYS, set(), 1, cols=96, rows=16)
    assert lines[0].rstrip().endswith(NAMES[1]) and marker_col(lines) > first


@pytest.mark.parametrize("sel", range(11))
def test_carousel_scrolls_to_keep_the_highlight_in_view(sel):
    lines = cli._carousel(NAMES, KEYS, set(), sel, cols=96, rows=16)
    label = next(i for i, l in enumerate(lines) if f"{KEYS[sel]} {NAMES[sel][:10]}" in l)
    bar = lines[label + 1]
    col = lines[label].index(f"{KEYS[sel]} {NAMES[sel][:10]}")
    assert bar[col] == "━"                    # the highlight sits under the selected card
    assert len(lines) <= 16


def test_carousel_marks_more_cards_off_either_side():
    left = cli._carousel(NAMES, KEYS, set(), 0, cols=96, rows=16)
    mid = cli._carousel(NAMES, KEYS, set(), 5, cols=96, rows=16)
    right = cli._carousel(NAMES, KEYS, set(), 10, cols=96, rows=16)
    assert not any("◀" in l for l in left) and any("▶" in l for l in left)
    assert any("◀" in l for l in mid) and any("▶" in l for l in mid)
    assert any("◀" in l for l in right) and not any("▶" in l for l in right)


def test_carousel_all_fit_no_arrows():
    lines = cli._carousel(NAMES[:3], KEYS[:3], set(), 1, cols=96, rows=16)
    assert not any("◀" in l or "▶" in l for l in lines)


def test_carousel_falls_back_to_a_list_when_too_narrow():
    lines = cli._carousel(NAMES, KEYS, {NAMES[2]}, 2, cols=30, rows=16)
    picked = [l for l in lines if l.startswith(">")]
    assert len(picked) == 1 and NAMES[2] in picked[0] and "★" in picked[0]
    assert sum(1 for l in lines if l[:1] in "> " and l[2:3] in KEYS and l[2:3]) == len(NAMES)


def test_carousel_uses_smaller_cards_in_a_short_popup():
    tall = cli._carousel(NAMES, KEYS, set(), 0, cols=96, rows=26)
    short = cli._carousel(NAMES, KEYS, set(), 0, cols=96, rows=12)
    assert len(short) <= 12 < len(tall)


# ------------------------------------------------------------ the pick loop

def _press(monkeypatch, *keys):
    it = iter(keys)
    monkeypatch.setattr(cli, "_getkey", lambda: next(it))


def _order():
    names = sorted(cli.discover())
    favs = [n for n in cli.favourites() if n in names]
    return favs + [n for n in names if n not in favs]


def test_pick_starts_on_the_tabs_recorded_layout(fake, monkeypatch):
    tab, _ = fake.add_tab("w1", "main", 3)
    assert heelr("apply", "main-right", "--tab", tab) == 0
    order = _order()
    _press(monkeypatch, "RIGHT", "\r")
    assert heelr("pick", "--tab", tab) == 0
    want = order[(order.index("main-right") + 1) % len(order)]
    assert core.tab_record(tab)["layout"] == want


def test_pick_with_no_record_starts_on_the_first_and_left_wraps(fake, monkeypatch):
    tab, _ = fake.add_tab("w1", "main", 3)
    _press(monkeypatch, "LEFT", "\r")
    assert heelr("pick", "--tab", tab) == 0
    assert core.tab_record(tab)["layout"] == _order()[-1]


def test_pick_h_and_l_move_too(fake, monkeypatch):
    tab, _ = fake.add_tab("w1", "main", 3)
    _press(monkeypatch, "l", "l", "h", "\r")
    assert heelr("pick", "--tab", tab) == 0
    assert core.tab_record(tab)["layout"] == _order()[1]


def test_pick_layout_key_still_applies_at_once(fake, monkeypatch):
    tab, _ = fake.add_tab("w1", "main", 3)
    order = _order()
    _press(monkeypatch, cli.MENU_KEYS[order.index("main-right")])
    assert heelr("pick", "--tab", tab) == 0
    assert core.tab_record(tab)["layout"] == "main-right"


def test_pick_ignores_unknown_keys_and_cancels_on_q(fake, monkeypatch):
    tab, _ = fake.add_tab("w1", "main", 3)
    before = fake.tree(tab)
    _press(monkeypatch, "!", cli._decode_key("\x1b[A"), "RIGHT", "q")   # up arrow too
    assert heelr("pick", "--tab", tab) == 0
    assert fake.calls == []                   # cancelling never touches Herdr
    assert core.tab_record(tab) == {}
    assert "cancel" in (core.state_dir() / "pick.log").read_text()


@pytest.mark.parametrize("seq", ["\x1b[A", "\x1b[B", "\x1b[H", "\x1b[5~", "\x1bOA", "\x1b[1;5A"])
def test_other_escape_sequences_do_not_cancel_the_menu(seq):
    assert cli._decode_key(seq) not in ("\x1b", "q")


FAVS = {"main-right", "main-center", "two-mains", "main-bottom", "agents-logs"}


@pytest.mark.parametrize("cols,rows", [(272, 14), (272, 16), (96, 16), (60, 14)])
def test_card_labels_are_whole_and_well_apart(cols, rows):
    order = sorted(FAVS) + [n for n in NAMES if n not in FAVS]
    for sel in range(len(order)):
        lines = cli._carousel(order, KEYS, FAVS, sel, cols=cols, rows=rows)
        label_row = next(l for i, l in enumerate(lines) if i + 1 < len(lines) and "━" in lines[i + 1])
        labels = [s for s in label_row.strip().split("   ") if s.strip()]
        want = [f"{KEYS[i]} {n}" + (" ★" if n in FAVS else "") for i, n in enumerate(order)]
        assert labels and all(l.strip() in want for l in labels), labels   # whole, 3+ spaces apart
        assert all(len(l) <= cols for l in lines[:-1])          # cards and labels fit (not the help line)


LONG = "my-review-work-layout-with-a-very-long-name"   # 43 characters


def _with_long_layout():
    d = core.config_dir() / "layouts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{LONG}.toml").write_text(f'name = "{LONG}"\nkind = "rule"\nrule = "main-left"\n')
    return NAMES + [LONG]


def _label_block(lines):
    """The label lines: between the cards' bottom border and the highlight bar."""
    bar = next(i for i, l in enumerate(lines) if "━" in l)
    bottom = max(i for i, l in enumerate(lines[:bar]) if "╰" in l)
    return lines[bottom + 1: bar]


def test_a_long_layout_name_wraps_instead_of_widening_every_card():
    order = _with_long_layout()
    keys = cli.MENU_KEYS[: len(order)]
    sel = len(order) - 1
    lines = cli._carousel(order, keys, set(), sel, cols=217, rows=16)
    assert any("╰" in l for l in lines), "fell back to the plain list"
    block = _label_block(lines)
    assert len(block) == 2
    col = block[0].index(f"{keys[sel]} my-")
    text = "".join(l[col:].split("   ")[0].strip() for l in block)
    assert text.replace("…", "") in f"{keys[sel]} {LONG}" and text.startswith(f"{keys[sel]} my-")
    assert lines[0].rstrip().endswith(LONG)             # full name still shown top right
    assert sum(l.count("╰") for l in lines) >= 8        # most cards still fit
    assert all(len(l) <= 217 for l in lines)


def test_wrap_breaks_after_hyphens_and_caps_at_two_lines():
    assert cli._wrap("a main-left-grid", 20) == ["a main-left-grid"]
    assert cli._wrap("c my-review-work-layout", 20) == ["c my-review-work-", "layout"]
    assert cli._wrap("z" * 30, 20) == ["z" * 20, "z" * 10]          # nowhere to break: split at the cap
    two = cli._wrap(f"c {LONG}", 20)
    assert len(two) == 2 and all(len(l) <= 20 for l in two) and two[1].endswith("…")


@pytest.mark.parametrize("rows", [12, 13, 14, 15, 16])
def test_wrapped_labels_still_leave_the_cursor_row_free(rows):
    order = _with_long_layout()
    keys = cli.MENU_KEYS[: len(order)]
    assert len(cli._carousel(order, keys, set(), 0, cols=217, rows=rows)) <= rows - 1


def test_wrap_never_breaks_between_the_key_and_the_name():
    lines = cli._wrap("a " + "averyveryverylongnamewith", 20)          # no hyphen, 27 characters
    assert lines == ["a averyveryverylongn", "amewith"]
    assert cli._wrap("a averyveryverylongnamewithnohyphens", 20)[0].startswith("a a")


def test_layouts_past_the_last_menu_key_are_still_reachable(fake, monkeypatch, tmp_path):
    user = tmp_path / "config" / "heelr" / "layouts"
    user.mkdir(parents=True)
    src = (PRESET_DIR / "main-right.toml").read_text()
    for i in range(30):
        (user / f"zz-{i:02}.toml").write_text(src.replace('"main-right"', f'"zz-{i:02}"', 1))
    order = _order()
    assert len(order) > len(cli.MENU_KEYS)
    tab, _ = fake.add_tab("w1", "main", 3)
    _press(monkeypatch, "LEFT", "\r")          # wraps to the last layout, which has no key
    assert heelr("pick", "--tab", tab) == 0
    assert core.tab_record(tab)["layout"] == order[-1]


def test_the_list_fallback_shows_layouts_past_the_last_key_without_one():
    names = [f"l{i:02}" for i in range(len(cli.MENU_KEYS) + 3)]
    lines = cli._carousel(names, cli.MENU_KEYS, set(), len(names) - 1, cols=30, rows=50)
    assert any(l.startswith(">") and l.endswith(names[-1]) for l in lines)
