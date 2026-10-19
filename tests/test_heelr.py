"""Offline tests against JSON recorded from Herdr 0.9.1."""
import json
from pathlib import Path

import pytest

from heelr import core
from heelr.assign import DEFAULT_ROLES, Mismatch, PaneInfo, assign, bind_rule, classify
from heelr.layouts import RULES, discover, expand_rule, load, parse
from heelr.plan import plan
from heelr.tree import Leaf, Split, first_leaf, from_dict, from_herdr_layout, leaves, same_structure, shape

FX = Path(__file__).parent / "fixtures"


def layout_fx(name):
    return json.loads((FX / name).read_text())["result"]["layout"]


def simulate(ops, tabs: dict[str, list[str]]):
    """Replay ops on a toy model: tab -> tree. Enough to check the invariant
    that re-insertion rebuilds the target exactly."""
    def chain(ps):
        return Leaf(pane=ps[0]) if len(ps) == 1 else Split("right", 0.5, Leaf(pane=ps[0]), chain(ps[1:]))
    trees = {t: chain(ps) for t, ps in tabs.items()}
    new_tab = iter(f"sim{i}" for i in range(100))
    sym = {}
    r = lambda x: sym.get(x, x)

    def remove(pane):
        for t, tr in list(trees.items()):
            here = {l.pane for l in leaves(tr)}
            if pane in here:
                rem = core.prune(tr, here - {pane})
                if rem is None:
                    del trees[t]  # Herdr closes an emptied tab
                else:
                    trees[t] = rem
                return
        raise AssertionError(f"moved pane {pane} does not exist")

    def insert(tab, target, pane, d, ratio):
        def walk(n):
            if isinstance(n, Leaf):
                return Split(d, ratio, n, Leaf(pane=pane)) if n.pane == target else n
            return Split(n.dir, n.ratio, walk(n.a), walk(n.b))
        trees[tab] = walk(trees[tab])

    for op in ops:
        if op.kind in ("park_new", "overflow_new"):
            remove(r(op.pane))
            t = next(new_tab)
            sym["$park" if op.kind == "park_new" else "$overflow"] = t
            trees[t] = Leaf(pane=r(op.pane))
        elif op.kind == "split":
            sym[op.pane] = op.pane.replace("$", "N")
            insert(r(op.tab), r(op.target), sym[op.pane], op.dir, op.ratio)
        else:
            remove(r(op.pane))
            insert(r(op.tab), r(op.target), r(op.pane), op.dir, op.ratio)
    return trees


# ------------------------------------------------------------------- tree

def test_rebuild_2x2_from_recorded_layout():
    t = from_herdr_layout(layout_fx("layout_2x2.json"))
    assert shape(t) == "right(0.50)[down(0.50)[wA:p1,wA:p7],down(0.50)[wA:p6,wA:p8]]"


def test_rebuild_1plus2():
    t = from_herdr_layout(layout_fx("layout_1plus2.json"))
    assert shape(t) == "right(0.50)[wA:pF,down(0.50)[wA:pG,wA:pH]]"


def test_rebuild_nested_same_direction():
    t = from_herdr_layout(layout_fx("layout_nested_down.json"))
    assert shape(t) == "down(0.50)[down(0.30)[wB:p1,wB:p2],wB:p3]"


def test_rebuild_every_layout_in_snapshot():
    snap = json.loads((FX / "snapshot_0.9.1.json").read_text())["result"]["snapshot"]
    for lay in snap["layouts"]:
        t = from_herdr_layout(lay)
        assert sorted(l.pane for l in leaves(t)) == sorted(p["pane_id"] for p in lay["panes"])


def test_same_tab_move_is_a_noop_in_0_9_1():
    # the reason the planner parks panes; if this fixture changes on upgrade, revisit
    mr = json.loads((FX / "move_same_tab_noop.json").read_text())["result"]["move_result"]
    assert mr["changed"] is False and mr["reason"] == "same_tab"


# ----------------------------------------------------------------- layouts

@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("n", range(1, 10))
def test_rules_produce_n_leaves(rule, n):
    t = expand_rule(rule, n)
    assert len(list(leaves(t))) == n
    if rule.startswith("main"):
        mains = [l for l in leaves(t) if l.main]
        assert len(mains) == 1
        if rule == "main-center":   # after the left stack: ceil((n - 1) / 2) panes
            assert mains[0] is list(leaves(t))[n // 2]
            return
        last = rule in ("main-right", "main-bottom") and n > 1
        assert mains[0] is (list(leaves(t))[-1] if last else first_leaf(t))


def test_new_rule_shapes():
    assert shape(expand_rule("main-right", 3)) == "right(0.40)[down(0.50)[L,L],L]"
    assert shape(expand_rule("main-bottom", 3)) == "down(0.40)[right(0.50)[L,L],L]"
    assert shape(expand_rule("main-left-grid", 5, 0.5)) == \
        "right(0.50)[L,right(0.50)[down(0.50)[L,L],down(0.50)[L,L]]]"
    assert shape(expand_rule("two-mains", 4, 0.65)) == "down(0.65)[right(0.50)[L,L],right(0.50)[L,L]]"
    assert shape(expand_rule("two-mains", 2)) == "right(0.50)[L,L]"


def test_main_center_shapes():
    # left stack, main, right stack; the left gets the extra pane; stacks share 1 - main_ratio
    assert shape(expand_rule("main-center", 2, 0.5)) == "right(0.50)[L,L]"
    assert shape(expand_rule("main-center", 3, 0.5)) == "right(0.25)[L,right(0.67)[L,L]]"
    assert shape(expand_rule("main-center", 4, 0.5)) == \
        "right(0.25)[down(0.50)[L,L],right(0.67)[L,L]]"
    assert shape(expand_rule("main-center", 5, 0.5)) == \
        "right(0.25)[down(0.50)[L,L],right(0.67)[L,down(0.50)[L,L]]]"
    assert shape(expand_rule("main-center", 6, 0.6)) == \
        "right(0.20)[down(0.33)[L,down(0.50)[L,L]],right(0.75)[L,down(0.50)[L,L]]]"


@pytest.mark.parametrize("n", range(2, 8))
@pytest.mark.parametrize("ratio", [0.5, 0.6])
def test_main_center_column_widths(n, ratio):
    t = expand_rule("main-center", n, ratio)
    if n == 2:
        assert t.b.main and abs(t.ratio - (1 - ratio)) < 1e-9
        return
    side = t.ratio
    main = (1 - side) * t.b.ratio
    assert t.b.a.main
    assert abs(main - ratio) < 1e-9 and abs(side - (1 - ratio) / 2) < 1e-9


@pytest.mark.parametrize("n_role,expect_top", [(3, 3), (0, 0), (5, 0)])
def test_agents_logs_splits_by_role(n_role, expect_top):
    t = expand_rule("agents-logs", 5, 0.7, n_role, "agent")
    tagged = [l for l in leaves(t) if l.role == "agent"]
    assert len(tagged) == expect_top
    if expect_top:  # agents on top, the rest in one row along the bottom
        assert t.dir == "down" and all(l.role == "agent" for l in leaves(t.a))
        assert all(l.role is None for l in leaves(t.b))


def test_bind_agents_logs_puts_each_role_in_its_band():
    panes = [P("s1"), P("a1", "agent"), P("l1", "logs"), P("a2", "agent", focused=True), P("a3", "agent")]
    t = bind_rule(expand_rule("agents-logs", 5, 0.7, 3, "agent"), panes, None)
    assert [l.pane for l in leaves(t.a)] == ["a1", "a2", "a3"]
    assert [l.pane for l in leaves(t.b)] == ["s1", "l1"]


def test_target_for_agents_logs_uses_main_role(tmp_path):
    from heelr import core
    lay = parse({"name": "x", "kind": "rule", "rule": "agents-logs", "main_role": "logs"})
    ts = core.TabState("T", "W", (100, 40), None, [P("a", "agent"), P("l1", "logs"), P("l2", "logs")],
                       False, None)
    target, overflow = core.target_for(lay, ts)
    assert overflow == [] and [l.pane for l in leaves(target.a)] == ["l1", "l2"]


def test_thumbnails_draw_every_preset():
    from heelr.thumb import draw, for_layout
    for n in discover():
        rows = for_layout(load(n), 13, 7)
        assert len(rows) == 7 and all(len(r) == 13 for r in rows), n
    # a 2x2 grid: one cross in the middle, four corners
    g = draw(expand_rule("grid", 4), 5, 5)
    assert g == ["╭─┬─╮", "│ │ │", "├─┼─┤", "│ │ │", "╰─┴─╯"]
    m = draw(expand_rule("main-top", 2), 5, 5)
    assert m[1] == "│░░░│" and m[3] == "│   │"


def test_pick_menu_draws_real_presets_and_falls_back_to_a_list_when_small():
    from heelr import cli
    names = sorted(discover())
    keys = cli.MENU_KEYS[: len(names)]
    small = cli._carousel(names, keys, set(), 0, cols=30, rows=12)
    assert sum(l.startswith((">", " ")) and any(n in l for n in names) for l in small) == len(names)
    big = cli._carousel(names, keys, set(), 0, cols=96, rows=16)
    assert len(big) <= 16 and any("╭" in l for l in big)


def test_pick_menu_survives_a_broken_user_layout(monkeypatch, tmp_path):
    from heelr import cli
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    d = tmp_path / "heelr" / "layouts"
    d.mkdir(parents=True)
    (d / "broken.toml").write_text("kind = 'nonsense'\n")
    names = sorted(discover())
    lines = cli._carousel(names, cli.MENU_KEYS[: len(names)], set(), 0, cols=96, rows=16)
    assert any(l.startswith(">") for l in lines)    # a list instead of cards


def test_even_columns_are_even():
    t = expand_rule("even-columns", 3)
    assert shape(t) == "right(0.33)[L,right(0.50)[L,L]]"


def test_presets_all_load():
    names = discover()
    assert {"main-left", "main-top", "grid", "even-columns", "even-rows"} <= set(names)
    for n in names:
        load(n)


def test_bad_layouts_rejected():
    with pytest.raises(ValueError):
        parse({"name": "x", "kind": "rule", "rule": "spiral"})
    with pytest.raises(ValueError):
        parse({"name": "x", "kind": "fixed"})
    with pytest.raises(ValueError):
        parse({"name": "x", "kind": "fixed", "root": {"dir": "left", "a": {}, "b": {}}})


# -------------------------------------------------------------- assignment

def P(pid, role="shell", focused=False, agent=None):
    return PaneInfo(pid, agent=agent, focused=focused, role=role)


def test_classify_first_match_wins():
    assert classify(PaneInfo("a", agent="claude", cmdline="tail -f x"), DEFAULT_ROLES) == "agent"
    assert classify(PaneInfo("b", cmdline="kubectl logs -f foo"), DEFAULT_ROLES) == "logs"
    assert classify(PaneInfo("c", cmdline="bash"), DEFAULT_ROLES) == "shell"


def test_assign_role_then_focus_then_order():
    tree = from_dict({"dir": "right", "ratio": 0.6,
                      "a": {"role": "agent", "main": True},
                      "b": {"dir": "down", "ratio": 0.5, "a": {"role": "agent"}, "b": {"role": "logs"}}})
    panes = [P("L", "logs"), P("A1", "agent"), P("A2", "agent", focused=True)]
    bound, over = assign(tree, panes)
    assert [l.pane for l in leaves(bound)] == ["A2", "A1", "L"] and over == []


def test_assign_focused_pane_of_wrong_role_does_not_get_main():
    tree = from_dict({"dir": "right", "ratio": 0.5, "a": {"role": "agent", "main": True}, "b": {}})
    bound, _ = assign(tree, [P("S", "shell", focused=True), P("A", "agent")])
    assert [l.pane for l in leaves(bound)] == ["A", "S"]


def test_assign_roles_are_preferences_not_filters():
    tree = from_dict({"dir": "right", "ratio": 0.5, "a": {"role": "logs"}, "b": {"role": "logs"}})
    bound, _ = assign(tree, [P("A"), P("B")])
    assert [l.pane for l in leaves(bound)] == ["A", "B"]


def test_assign_refuse_and_fill():
    tree = expand_rule("even-columns", 3)
    with pytest.raises(Mismatch):
        assign(tree, [P("a"), P("b")])
    bound, over = assign(tree, [P("a"), P("b")], "fill")
    assert [l.pane for l in leaves(bound)] == ["a", "b", None] and over == []
    bound, over = assign(tree, [P("a"), P("b"), P("c"), P("d")], "fill")
    assert over == ["d"]


def test_bind_rule_main_role_beats_focus():
    t = bind_rule(expand_rule("main-left", 3), [P("s", focused=True), P("x"), P("a", "agent")], "agent")
    assert [l.pane for l in leaves(t)] == ["a", "s", "x"]


# ----------------------------------------------------------------- planner

CASES = [
    ("right(0.5)[a,down(.5)[b,c]] -> grid4-ish",
     expand_rule("main-left", 4), ["a", "b", "c", "d"]),
    ("grid 5", expand_rule("grid", 5), ["a", "b", "c", "d", "e"]),
    ("rows 3", expand_rule("even-rows", 3), ["c", "a", "b"]),
    ("main-right 4", expand_rule("main-right", 4), ["b", "c", "d", "a"]),
    ("main-bottom 4", expand_rule("main-bottom", 4), ["d", "a", "b", "c"]),
    ("main-left-grid 6", expand_rule("main-left-grid", 6), ["a", "b", "c", "d", "e", "f"]),
    ("two-mains 5", expand_rule("two-mains", 5), ["e", "a", "b", "c", "d"]),
    ("main-center 5", expand_rule("main-center", 5, 0.5), ["b", "c", "a", "d", "e"]),
    ("agents-logs 5", expand_rule("agents-logs", 5, 0.7, 3), ["a", "b", "c", "d", "e"]),
]


@pytest.mark.parametrize("label,tree,order", CASES, ids=[c[0] for c in CASES])
def test_plan_rebuilds_target_exactly(label, tree, order):
    for l, p in zip(leaves(tree), order):
        l.pane = p
    current = sorted(order)
    ops = plan(tree, "T", current)
    trees = simulate(ops, {"T": current})
    assert list(trees) == ["T"], "park tab must be empty (auto-closed) at the end"
    assert same_structure(tree, trees["T"], tol=0) == []


def test_plan_with_new_panes_and_overflow():
    tree = expand_rule("grid", 4)
    bound, over = assign(tree, [P("a"), P("b"), P("c")], "fill")
    ops = plan(bound, "T", ["a", "b", "c", "z"], overflow=["z"])
    kinds = [o.kind for o in ops]
    assert kinds[0] == "overflow_new" and kinds.count("split") == 1
    trees = simulate(ops, {"T": ["a", "b", "c", "z"]})
    assert len(trees) == 2  # T + overflow tab holding z
    assert len(list(leaves(trees["T"]))) == 4


def test_plan_never_parks_the_anchor_and_rejects_strangers():
    tree = expand_rule("even-columns", 2)
    for l, p in zip(leaves(tree), ["a", "b"]):
        l.pane = p
    assert all(o.pane != "a" for o in plan(tree, "T", ["a", "b"]))
    with pytest.raises(ValueError):
        plan(tree, "T", ["a"])  # b is neither here nor declared external
    ops = plan(tree, "T", ["a"], external={"b"})
    assert [o.kind for o in ops] == ["move"]


def test_prune_collapses_missing_panes():
    t = from_dict({"dir": "right", "ratio": 0.5, "a": {"pane": "a"},
                   "b": {"dir": "down", "ratio": 0.5, "a": {"pane": "b"}, "b": {"pane": "c"}}})
    assert shape(core.prune(t, {"a", "c"})) == "right(0.50)[a,c]"
    assert core.prune(t, set()) is None


def test_save_roundtrip(tmp_path):
    import tomllib
    t = from_herdr_layout(layout_fx("layout_1plus2.json"))
    for l in leaves(t):
        l.role, l.pane = "agent", None
    first_leaf(t).main = True
    lay = parse(tomllib.loads(core.to_toml("x", t)))
    assert same_structure(lay.root, t, tol=0.001) == []
    assert first_leaf(lay.root).main


# --------------------------------------------------------------- multi-tab

from heelr import multi
from heelr.multi import Dest, TabNow, WorkspaceState


def ws_of(tabs: dict[tuple[str, str], list[tuple[str, str]]]) -> WorkspaceState:
    """{(tab_id, label): [(pane, role), ...]} -> WorkspaceState (column layout)."""
    out, info = [], {}
    for (tid, label), ps in tabs.items():
        tree = expand_rule("even-columns", len(ps))
        for l, (pid, role) in zip(leaves(tree), ps):
            l.pane = pid
            info[pid] = PaneInfo(pid, role=role)
        out.append(TabNow(tid, label, tree, [p for p, _ in ps]))
    return WorkspaceState("w", out, info, None)


FLEET = parse({"name": "fleet", "kind": "multi", "tabs": [
    {"label": "agents", "gather": ["agent"], "layout": {"kind": "rule", "rule": "grid"}},
    {"label": "ops", "gather": ["logs", "shell"], "max": 2}]})


def sim_gather(ws, ops):
    where = {p: t.tab_id for t in ws.tabs for p in t.panes}
    alive = {t.tab_id for t in ws.tabs}
    sym = {}
    for op in ops:
        src = where[op.pane]
        if op.kind == "gather_new":
            sym[op.tab] = op.tab
            alive.add(op.tab)
        else:
            assert op.tab in alive, f"moved into a closed tab: {op.describe()}"
            assert where[op.target] == op.tab, "target pane not in destination tab"
        where[op.pane] = op.tab
        if src not in where.values():
            alive.discard(src)  # Herdr closes an emptied tab
    return where


def test_multi_parse_validation():
    with pytest.raises(ValueError):
        parse({"name": "x", "kind": "multi", "tabs": []})
    with pytest.raises(ValueError):
        parse({"name": "x", "kind": "multi", "tabs": [{"label": "a", "gather": ["x"]}, {"label": "a", "gather": ["y"]}]})
    with pytest.raises(ValueError):
        parse({"name": "x", "kind": "multi", "tabs": [{"label": "overflow", "gather": ["x"]}]})
    with pytest.raises(ValueError):
        parse({"name": "x", "kind": "multi", "tabs": [{"label": "a", "gather": ["x"], "max": 0}]})


def test_assign_tabs_by_role_with_max_and_park():
    ws = ws_of({("t1", "1"): [("a1", "agent"), ("s1", "shell")],
                ("t2", "2"): [("l1", "logs"), ("a2", "agent"), ("s2", "shell")]})
    dests = multi.assign_tabs(FLEET, ws)
    assert [(d.label, d.tab_id, d.panes) for d in dests] == [
        ("agents", None, ["a1", "a2"]), ("ops", None, ["s1", "l1"]), ("overflow", None, ["s2"])]


def test_assign_tabs_prefers_panes_already_in_the_labelled_tab():
    ws = ws_of({("t1", "1"): [("s1", "shell")], ("t2", "ops"): [("s2", "shell"), ("s3", "shell")]})
    dests = multi.assign_tabs(FLEET, ws)
    ops = next(d for d in dests if d.label == "ops")
    assert ops.tab_id == "t2" and ops.panes == ["s2", "s3"]
    assert multi.plan_gather(ws, dests)[0].pane == "s1"  # only the extra pane moves


def test_plan_gather_lands_every_pane_and_survives_closing_tabs():
    # 'ops' exists but holds only an agent, which leaves first -> ops closes -> recreated
    ws = ws_of({("t1", "ops"): [("a1", "agent")], ("t2", "2"): [("s1", "shell"), ("a2", "agent")]})
    dests = multi.assign_tabs(FLEET, ws)
    ops = multi.plan_gather(ws, dests)
    where = sim_gather(ws, ops)
    for d in dests:
        assert {where[p] for p in d.panes} == {d.tab_id}, d.label


def test_plan_gather_is_empty_when_already_gathered():
    ws = ws_of({("t1", "agents"): [("a1", "agent")], ("t2", "ops"): [("s1", "shell")]})
    assert multi.plan_gather(ws, multi.assign_tabs(FLEET, ws)) == []


def test_capture_roundtrips_and_skips_overflow():
    import tomllib
    ws = ws_of({("t1", "agents"): [("a1", "agent"), ("a2", "agent")],
                ("t2", "overflow"): [("s1", "shell")]})
    lay = parse(tomllib.loads(multi.capture(ws, "snap")))
    assert [t.label for t in lay.tabs] == ["agents"]
    assert lay.tabs[0].max == 2 and lay.tabs[0].gather == ["agent"]
    assert same_structure(lay.tabs[0].layout.root, ws.tabs[0].tree, check_panes=False) == []


def test_decode_key_handles_kitty_protocol():
    from heelr.cli import _decode_key
    assert _decode_key("2") == "2"
    assert _decode_key("\x1b[50u") == "2"          # kitty CSI u for '2'
    assert _decode_key("\x1b[97;1u") == "a"
    assert _decode_key("\x1b[27u") == "\x1b"       # Esc still cancels
    assert _decode_key("\x1b") == "\x1b"
    assert _decode_key("") == "q"


# ------------------------------------------------------------ add_missing

def _ts(panes):
    from heelr import core
    return core.TabState("T", "W", (100, 40), None, panes, False, None)


def test_add_missing_parse():
    lay = parse({"name": "x", "kind": "rule", "rule": "main-left", "add_missing": True,
                 "new_pane_command": "htop"})
    assert lay.add_missing and lay.new_pane_command == "htop"
    fx = parse({"name": "f", "kind": "fixed", "add_missing": True,
                "root": {"dir": "right", "ratio": 0.5, "a": {}, "b": {}}})
    assert fx.on_mismatch == "fill"
    with pytest.raises(ValueError):
        parse({"name": "f", "kind": "fixed", "add_missing": True, "on_mismatch": "refuse",
               "root": {"dir": "right", "ratio": 0.5, "a": {}, "b": {}}})
    with pytest.raises(ValueError):
        parse({"name": "x", "kind": "rule", "rule": "grid", "add_missing": "yes"})


def test_add_missing_tops_rules_up_to_their_minimum():
    from heelr import core
    lay = parse({"name": "x", "kind": "rule", "rule": "two-mains", "add_missing": True})
    t, _ = core.target_for(lay, _ts([P("a", focused=True)]))
    assert [l.pane for l in leaves(t)] == ["a", None, None]
    lay.add_missing = False
    t, _ = core.target_for(lay, _ts([P("a", focused=True)]))
    assert [l.pane for l in leaves(t)] == ["a"]


def test_add_missing_agents_logs_adds_the_empty_band():
    from heelr import core
    lay = parse({"name": "x", "kind": "rule", "rule": "agents-logs", "add_missing": True})
    t, _ = core.target_for(lay, _ts([P(f"a{i}", "agent") for i in range(4)]))
    assert [l.pane for l in leaves(t.a)] == ["a0", "a1", "a2", "a3"]
    assert [l.pane for l in leaves(t.b)] == [None]
    t, _ = core.target_for(lay, _ts([P("s1"), P("s2")]))   # no agents: plain grid, nothing added
    assert [l.pane for l in leaves(t)] == ["s1", "s2"] and t.dir == "right"


def test_bind_rule_never_drops_a_pane():
    t = bind_rule(expand_rule("agents-logs", 3, 0.7, 2, "agent"), [P("x"), P("y"), P("z")], None)
    assert sorted(l.pane for l in leaves(t)) == ["x", "y", "z"]


@pytest.mark.parametrize("rule,panes", [("main-right", ["a"]), ("main-bottom", ["a"]),
                                        ("two-mains", ["a"])])
def test_plan_creates_missing_panes_including_the_anchor(rule, panes):
    from heelr import core
    lay = parse({"name": "x", "kind": "rule", "rule": rule, "add_missing": True})
    target, _ = core.target_for(lay, _ts([P(p, focused=i == 0) for i, p in enumerate(panes)]))
    ops = plan(target, "T", panes)
    trees = simulate(ops, {"T": list(panes)})
    assert list(trees) == ["T"]
    created = {o.pane: o.pane.replace("$", "N") for o in ops if o.kind == "split"}
    assert same_structure(core._bind_new(target, created), trees["T"], tol=0) == []


def test_close_created_only_closes_idle_or_own_command(monkeypatch):
    from heelr import core, herdr
    info = {
        "idle": {"foreground_process_group_id": 10, "shell_pid": 10, "foreground_processes": []},
        "mine": {"foreground_process_group_id": 11, "shell_pid": 1,
                 "foreground_processes": [{"cmdline": "htop"}]},
        "busy": {"foreground_process_group_id": 12, "shell_pid": 2,
                 "foreground_processes": [{"cmdline": "vim notes"}]},
    }
    closed = []

    def call(*a):
        if a[:2] == ("pane", "process-info"):
            return {"process_info": info[a[-1]]}
        if a[:2] == ("pane", "close"):
            closed.append(a[2])
            return {}
        raise AssertionError(a)
    monkeypatch.setattr(herdr, "call", call)
    created = [{"pane": "idle", "command": None}, {"pane": "mine", "command": "htop"},
               {"pane": "busy", "command": "htop"}, {"pane": "gone", "command": None}]
    assert core.close_created(created, {"idle", "mine", "busy"}, False, log=lambda *_: None) == ["idle", "mine"]
    assert closed == ["idle", "mine"]


# Which shipped presets create panes for empty slots, and how many panes a
# one-pane tab ends up with. grid stays off: any count is a valid grid, and
# multi layouts (examples/fleet.toml) use it for agent-only tabs.
PRESET_ADD_MISSING = {"main-left": 2, "main-right": 2, "main-top": 2, "main-bottom": 2,
                      "main-left-grid": 2, "two-mains": 3, "even-columns": 2, "even-rows": 2,
                      "main-center": 2, "grid": None,
                      "agents-logs": 1}   # shells only: a plain grid, nothing added (it would never fill)


@pytest.mark.parametrize("name,total", PRESET_ADD_MISSING.items())
def test_presets_fill_a_one_pane_tab(name, total):
    from heelr import core
    lay = load(name)
    assert lay.add_missing is (total is not None)
    assert lay.new_pane_command is None          # plain shell
    t, over = core.target_for(lay, _ts([P("a", focused=True)]))
    got = [l.pane for l in leaves(t)]
    assert over == [] and "a" in got
    assert len(got) == (total or 1) and got.count(None) == (total or 1) - 1


@pytest.mark.parametrize("name", [n for n, t in PRESET_ADD_MISSING.items() if t])
def test_presets_never_add_panes_once_the_shape_shows(name):
    from heelr import core
    lay = load(name)
    for n in range(PRESET_ADD_MISSING[name], 7):
        t, _ = core.target_for(lay, _ts([P(f"p{i}", focused=i == 0) for i in range(n)]))
        assert None not in [l.pane for l in leaves(t)], (name, n)


@pytest.mark.parametrize("name", ["main-left", "main-left-grid", "even-columns", "even-rows", "two-mains",
                                  "main-center"])
def test_plan_fills_one_pane_tab_for_newly_enabled_presets(name):
    from heelr import core
    lay = load(name)
    target, _ = core.target_for(lay, _ts([P("a", focused=True)]))
    ops = plan(target, "T", ["a"])
    trees = simulate(ops, {"T": ["a"]})
    created = {o.pane: o.pane.replace("$", "N") for o in ops if o.kind == "split"}
    assert len(created) == PRESET_ADD_MISSING[name] - 1
    assert same_structure(core._bind_new(target, created), trees["T"], tol=0) == []


def test_user_file_can_turn_add_missing_off(tmp_path):
    from heelr import core
    (tmp_path / "main-left.toml").write_text(
        'name = "main-left"\nkind = "rule"\nrule = "main-left"\nadd_missing = false\n')
    lay = load("main-left", user_dir=tmp_path)
    assert not lay.add_missing
    t, _ = core.target_for(lay, _ts([P("a", focused=True)]))
    assert [l.pane for l in leaves(t)] == ["a"]


ROLE_MIXES = [["shell"], ["shell", "shell"], ["shell"] * 3, ["agent"], ["agent", "agent"],
              ["agent", "shell"], ["logs", "agent", "agent"], ["logs"]]


@pytest.mark.parametrize("name", [n for n, t in PRESET_ADD_MISSING.items() if t])
@pytest.mark.parametrize("roles", ROLE_MIXES, ids=["-".join(r) for r in ROLE_MIXES])
def test_reapplying_a_preset_adds_panes_at_most_once(name, roles):
    """Apply, let the new panes appear as shells, apply again: nothing more is added."""
    from heelr import core
    lay = load(name)
    panes = [P(f"p{i}", r, focused=i == 0, agent="claude" if r == "agent" else None)
             for i, r in enumerate(roles)]
    t, _ = core.target_for(lay, _ts(panes))
    panes += [P(f"new{i}") for i, l in enumerate(leaves(t)) if l.pane is None]
    t, _ = core.target_for(lay, _ts(panes))
    assert None not in [l.pane for l in leaves(t)], (name, roles)


@pytest.mark.parametrize("extra", [{"main_role": "shell"},
                                   {"main_role": "logs", "new_pane_command": "tail -f app.log"}],
                         ids=["main_role-shell", "logs-with-logs-command"])
def test_agents_logs_never_adds_a_pane_that_would_join_the_full_band(extra):
    """If the new pane would have the band's own role, it can't fill the other
    band: adding it would just repeat on every apply (3 -> 4 -> 5)."""
    from heelr import core
    lay = parse({"name": "x", "kind": "rule", "rule": "agents-logs", "add_missing": True, **extra})
    panes = [P(f"p{i}", extra["main_role"], focused=i == 0) for i in range(3)]
    t, _ = core.target_for(lay, _ts(panes))
    assert None not in [l.pane for l in leaves(t)]


def test_agents_logs_still_adds_a_shell_to_an_all_logs_tab():
    from heelr import core
    lay = parse({"name": "x", "kind": "rule", "rule": "agents-logs", "add_missing": True, "main_role": "logs"})
    t, _ = core.target_for(lay, _ts([P("l1", "logs"), P("l2", "logs")]))
    assert [l.pane for l in leaves(t.b)] == [None]


def _closable_with(monkeypatch, fg):
    from heelr import core, herdr
    info = {"foreground_process_group_id": 11, "shell_pid": 1,
            "foreground_processes": [{"cmdline": c, "pid": 11 + i} for i, c in enumerate(fg)]}
    monkeypatch.setattr(herdr, "call", lambda *a: {"process_info": info})
    return lambda command: core._closable("p", command, log=lambda *_: None)


def test_a_pane_running_its_own_pipeline_is_closable(monkeypatch):
    closable = _closable_with(monkeypatch, ["tail -f app.log", "grep  ERROR"])
    assert closable("tail -f app.log | grep ERROR")
    assert closable("tail -f app.log|grep ERROR")


def test_a_pipeline_with_any_other_stage_is_not_closable(monkeypatch):
    closable = _closable_with(monkeypatch, ["tail -f app.log", "grep WARN"])
    assert not closable("tail -f app.log | grep ERROR")


def test_children_of_the_command_do_not_stop_it_being_closable(monkeypatch):
    """Herdr lists the whole foreground group, e.g. an agent's helper processes."""
    closable = _closable_with(monkeypatch, ["npm run dev", "node server.js"])
    assert closable("npm run dev")
    assert not closable("node server.js")  # not the group leader: something else runs there


def test_closable_uses_the_real_process_info_shape_whatever_the_order(monkeypatch):
    """Herdr 0.9.3 lists the whole foreground group (an agent and its helper processes)."""
    from heelr import core, herdr
    info = json.loads((FX / "process_info_agent_0.9.3.json").read_text())["result"]["process_info"]
    agent = info["foreground_processes"][0]["cmdline"]
    helper = info["foreground_processes"][1]["cmdline"]
    for procs in (info["foreground_processes"], info["foreground_processes"][::-1]):
        monkeypatch.setattr(herdr, "call", lambda *a, p=procs: {"process_info": {**info, "foreground_processes": p}})
        assert core._closable("wA:p1", agent, log=lambda *_: None)
        assert not core._closable("wA:p1", helper, log=lambda *_: None)   # a helper is not the leader
