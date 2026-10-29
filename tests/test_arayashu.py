"""Regression tests execute real functions with game I/O mocked."""
import ast
import copy
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from source.arayashu import (
    SoloSetupError, guard_fusion_selection, guard_web_fusion,
    normalize_solo_options, sacrifice_turn_limit, solo_profile,
    use_winrate, validate_sacrifice_progress, validate_solo_selection,
)

ROOT = Path(__file__).resolve().parents[1]

def function(path, name, namespace=None, owner=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = tree.body
    if owner:
        nodes = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == owner).body
    node = copy.deepcopy(next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name))
    node.decorator_list = []
    ns = dict(namespace or {})
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
    return ns[name], ns

def literal(path, name):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(name)

class PolicyTests(unittest.TestCase):
    def test_hard_extreme_does_not_override_current_hos_toggle(self):
        for hard in (False, True):
            for extra in (False, True):
                self.assertFalse(use_winrate(hard, extra, True))
                self.assertEqual(use_winrate(hard, extra, False), hard or extra)

    def test_presets_have_the_promised_budget_and_unique_deployment(self):
        prices = [10, 10, 20, 20, 30, 30, 40, 40, 50, 60]
        for name, budget in (("deep", 410), ("economy", 250)):
            profile = solo_profile(name)
            self.assertEqual(sum(a*b for a, b in zip(prices, profile["buff"])) + 70, budget)
            self.assertEqual(profile["sinners"][0], 3)
            self.assertEqual(len(set(profile["sinners"])), 10)
            self.assertNotIn(4, profile["sinners"])
            self.assertNotIn(11, profile["sinners"])
            self.assertEqual(set(profile["cards"]), set(range(5)))
        a = solo_profile()
        a["buff"][0] = 0
        a["keywordless"]["0"] = 0
        self.assertEqual(solo_profile()["buff"][0], 1)
        self.assertEqual(solo_profile()["keywordless"]["0"], 3)

    def test_sacrifice_limits_cover_first_and_resumed_battles(self):
        self.assertEqual(sacrifice_turn_limit(10, 0), 5)
        self.assertEqual(sacrifice_turn_limit(10, 7), 2)
        self.assertEqual(sacrifice_turn_limit(10, 9), 0)
        self.assertEqual(sacrifice_turn_limit(1, 0), 0)
        self.assertEqual(sacrifice_turn_limit(10, 12), 0)

    def test_bad_deployment_is_rejected_before_a_paid_start(self):
        validate_solo_selection(["RYOSHU", "OUTIS"])
        for sinners in ([], ["OUTIS", "RYOSHU"], ["RYOSHU", "GREGOR"],
                        ["RYOSHU", "MEURSAULT"], ["RYOSHU", "RYOSHU"]):
            with self.subTest(sinners=sinners), self.assertRaises(SoloSetupError):
                validate_solo_selection(sinners)

    def test_no_progress_stops_but_partial_progress_and_solo_continue(self):
        with self.assertRaises(SoloSetupError):
            validate_sacrifice_progress(0, 0, 5, 10)
        validate_sacrifice_progress(None, 0, 0, 10)
        validate_sacrifice_progress(0, 3, 2, 10)
        validate_sacrifice_progress(9, 9, 5, 10)
        validate_sacrifice_progress(0, 0, 1, 10)

    def test_invalid_failure_limit_cannot_disable_the_loss_limit(self):
        for value in (None, {}, {"max_failed_runs": 0}, {"max_failed_runs": True},
                      {"max_failed_runs": 999}, {"adversities": "surprise"}):
            self.assertEqual(normalize_solo_options(value), {
                "adversities": "all", "max_failed_runs": 2
            })
        self.assertEqual(normalize_solo_options({
            "adversities": "none", "max_failed_runs": 1
        }), {"adversities": "none", "max_failed_runs": 1})

class BattleTests(unittest.TestCase):
    def submit_context(self, waiting=True, accepts_enter=True):
        state = {"waiting": waiting}
        calls = []
        def press(key, *args):
            calls.append(key)
            if key == "enter" and accepts_enter:
                state["waiting"] = False
        ns = {
            "time": SimpleNamespace(sleep=Mock()),
            "now": SimpleNamespace(button=lambda name: state["waiting"]),
            "gui": SimpleNamespace(press=press),
            "wait_while_condition": lambda condition, **kw: not condition(),
            "SoloSetupError": SoloSetupError,
        }
        submit, _ = function("source/battle.py", "submit_hos_turn", ns)
        return submit, state, calls

    def test_prepared_defense_is_submitted_without_p(self):
        submit, state, calls = self.submit_context()
        submit()
        self.assertEqual(calls, ["enter"])
        self.assertFalse(state["waiting"])

    def test_already_running_chain_is_not_resubmitted(self):
        submit, _, calls = self.submit_context(waiting=False)
        submit()
        self.assertEqual(calls, [])

    def test_unsubmitted_turn_stops_instead_of_using_winrate(self):
        submit, _, calls = self.submit_context(accepts_enter=False)
        with self.assertRaises(SoloSetupError):
            submit()
        self.assertEqual(calls, ["enter"])

    def fight_context(self, focused=False, portrait=True):
        state = {"waiting": True}
        calls = []
        class ImageMissing(Exception):
            pass
        def press(key, *args):
            calls.append(("press", key))
            if key == "enter":
                state["waiting"] = False
        def button(name, *args, **kwargs):
            if name == "winrate":
                return state["waiting"]
            if name == "Move":
                return not state["waiting"]
            return False
        def locate_edge(*args, **kwargs):
            if focused:
                raise ImageMissing()
            return (100, 800, 140, 50)
        p = SimpleNamespace(
            HOS_MODE=True, DEAD=0, SELECTED=["RYOSHU"] + ["X"]*9,
            HOS_DEFENSE_TURNS=0, LIMBUS_NAME="LimbusCompany", RESTART=False,
            ALTF4=False,
        )
        ns = dict(
            p=p, time=SimpleNamespace(sleep=Mock()),
            now=SimpleNamespace(button=button),
            now_rgb=SimpleNamespace(button=button),
            loc=SimpleNamespace(button=button),
            gui=SimpleNamespace(
                press=press, ImageNotFoundException=ImageMissing,
                center=lambda box: (box[0]+box[2]/2, box[1]+box[3]/2),
                getActiveWindowTitle=lambda: "LimbusCompany",
            ),
            LocateEdges=SimpleNamespace(try_locate=locate_edge),
            LocateRGB=SimpleNamespace(locate=lambda *a, **kw: (200, 950, 50, 50) if portrait else None),
            PTH={key:key for key in ("gear", "gear2", "arayashu", "arayashu2", "arayashu3")},
            REG={"arayashu": (0, 940, 1920, 140)},
            screenshot=Mock(return_value=None),
            defense_skill=Mock(side_effect=lambda *a: calls.append(("defense",))),
            chain=Mock(side_effect=lambda *a, **kw: calls.append(("chain", kw.get("is_defense", False)))),
            win_click=lambda *a, **kw: calls.append(("portrait",)),
            wait_while_condition=lambda condition, **kw: not condition(),
            sacrifice_turn_limit=sacrifice_turn_limit, SoloSetupError=SoloSetupError,
            is_ego=lambda: None, exit_if=["Move"], logging=logging,
        )
        submit, _ = function("source/battle.py", "submit_hos_turn", ns)
        ns["submit_hos_turn"] = submit
        focused_defense, _ = function("source/battle.py", "focused_hos_defense", ns)
        ns["focused_hos_defense"] = focused_defense
        fight, _ = function("source/battle.py", "fight", ns)
        return fight, p, calls

    def test_real_hard_hos_fight_keeps_defense_through_submission(self):
        fight, p, calls = self.fight_context()
        self.assertTrue(fight())
        self.assertIn(("defense",), calls)
        self.assertIn(("chain", True), calls)
        self.assertEqual([x for x in calls if x[0] == "press"], [("press", "enter")])
        self.assertEqual(p.HOS_DEFENSE_TURNS, 1)

    def test_focused_fallback_selects_defense_after_winrate(self):
        fight, p, calls = self.fight_context(focused=True)
        self.assertTrue(fight())
        self.assertEqual(calls, [("press", "p"), ("portrait",), ("press", "enter")])
        self.assertEqual(p.HOS_DEFENSE_TURNS, 1)

    def test_unrecognized_focused_portrait_does_not_submit_attack(self):
        fight, _, calls = self.fight_context(focused=True, portrait=False)
        with self.assertRaises(SoloSetupError):
            fight()
        self.assertNotIn(("press", "enter"), calls)

class GiftSafetyTests(unittest.TestCase):
    def test_selling_cannot_reach_ui_in_hos_even_if_web_match_is_missing(self):
        fail = Mock(side_effect=AssertionError("Sale UI must not be used"))
        p = SimpleNamespace(HOS_MODE=True)
        sell, _ = function("source/shop.py", "sell", {
            "p": p, "balance": lambda: 50, "logging": logging, "Action": fail
        })
        search, _ = function("source/shop.py", "search_sell", {
            "p": p, "inventory_check": fail
        })
        self.assertFalse(sell({"upgrade": 300}))
        self.assertFalse(search((0, 0, 1, 1)))
        fail.assert_not_called()

    def test_sufficient_cost_does_not_need_a_sale(self):
        sell, _ = function("source/shop.py", "sell", {
            "p": SimpleNamespace(HOS_MODE=True), "balance": lambda: 500, "logging": logging
        })
        self.assertTrue(sell({"upgrade": 300}))

    def test_missing_web_blocks_actual_fuse_before_any_selection(self):
        dangerous = Mock(side_effect=AssertionError("Fusion must not be attempted"))
        fuse, _ = function("source/shop.py", "fuse", {
            "time": SimpleNamespace(sleep=Mock()),
            "handle_available_fusion": lambda: False,
            "get_inventory": lambda: ({}, {}, {}),
            "p": SimpleNamespace(HOS_MODE=True),
            "guard_web_fusion": guard_web_fusion, "fuse_search": dangerous,
        })
        with self.assertRaises(SoloSetupError):
            fuse()
        dangerous.assert_not_called()

    def test_verified_web_position_is_never_an_ingredient(self):
        protected = guard_web_fusion({"spiderweb": (1000, 500, 0)})
        with self.assertRaises(SoloSetupError):
            guard_fusion_selection([(950, 450, 0)], protected)
        guard_fusion_selection([(1200, 500, 0), (1000, 500, 1)], protected)
        with self.assertRaises(SoloSetupError):
            guard_fusion_selection([], None)

    def test_last_line_fusion_guard_precedes_clicks(self):
        perform, _ = function("source/shop.py", "perform_clicks", {
            "p": SimpleNamespace(HOS_MODE=True, HOS_PROTECTED_GIFT=(1000,500,0)),
            "guard_fusion_selection": guard_fusion_selection,
        })
        with self.assertRaises(SoloSetupError):
            perform([(1000,500,0)])

    def test_wishmaking_does_not_mutate_global_recipe_lists(self):
        from itertools import combinations_with_replacement
        combos = list(combinations_with_replacement(range(1,5),3))
        extra = list(combos)
        two = list(combinations_with_replacement(range(1,5),2))
        ns = dict(
            p=SimpleNamespace(SUPER="supershop", WISHMAKING=True),
            fusion_ranges={4:(25,45)}, super_ranges={4:(22,75)},
            COMBOS=combos, EXTRA=extra, TWO_ITEM_COMBOS=two,
            item_points={1:3,2:6,3:10,4:15},
            combo_counter=lambda seq: {t:seq.count(t) for t in seq},
        )
        decide, _ = function("source/shop.py", "decide_fusion", ns)
        before = list(extra)
        for _ in range(3):
            decide(4, {1:[1]*5,2:[1]*5,3:[1]*5,4:[1]*5})
        self.assertEqual(extra, before)
        self.assertEqual(combos, before)

class SettingsTests(unittest.TestCase):
    def test_priority_order_and_extreme_floor_assignments_survive_reload(self):
        packs = literal("source/utils/paths.py", "PACKS")
        wordless = literal("source/utils/paths.py", "WORDLESS")
        def clean(data, allowed):
            return {k:v for k,v in data.items() if k in allowed}
        verify, _ = function("source_app/settings_manager.py", "verify_file_data", {
            "PACKS": packs, "WORDLESS": wordless,
            "normalize_solo_options": normalize_solo_options,
        }, owner="SettingsManager")
        owner = SimpleNamespace(clean_entries=clean)
        priority = ["Line2", "CodePurple", "Line1", "Line2"]
        data, corrupted = verify(owner, {
            "SOLO":{"adversities":"none","max_failed_runs":1},
            "HARD":{"0":[priority,[],{"CodePurple":11,"Line2":8},{}]},
        })
        self.assertEqual(data["HARD"]["0"][0], ["Line2","CodePurple","Line1"])
        self.assertEqual(data["HARD"]["0"][2], {"CodePurple":11,"Line2":8})
        self.assertEqual(data["SOLO"]["adversities"], "none")
        self.assertFalse(corrupted)

    def test_ui_uses_current_toggle_instead_of_previous_global_state(self):
        def checked(value):
            return SimpleNamespace(isChecked=lambda: value)
        buttons = {f"on{i}": checked(i == 6) for i in range(12)}
        buttons.update({f"team{i}": checked(i == 0) for i in range(7)})
        buttons["log"] = checked(False)
        app = SimpleNamespace(
            inputField=SimpleNamespace(text=lambda:"1"),
            exp=SimpleNamespace(text=lambda:"0"), thd=SimpleNamespace(text=lambda:"0"),
            sync_webhook_settings=lambda **kw:True, apply_webhook_env=lambda:None,
            buttons=buttons, selected_affinity={i:[i] for i in range(7)},
            update_sinners=lambda:None, is_lux=False, is_proceed=False, team=0,
            sinner_selections={0:[3]}, sinners=[3], hard=True,
            get_packs=lambda i:([],[],{},{}), get_cards=lambda:[3,1,2,0,4],
            hos_mode=checked(True),
            solo_options={"adversities":"all","max_failed_runs":2}, keywordless={},
        )
        get_params, _ = function("App.py", "get_params", {
            "setup_logging":Mock(), "use_winrate":use_winrate,
            "Bot":SimpleNamespace(WORDLESS={}),
            "p":SimpleNamespace(HOS_MODE=False),
        }, owner="MyApp")
        self.assertTrue(get_params(app))
        self.assertTrue(app.settings["hos_mode"])
        self.assertTrue(app.settings["infinity"])
        self.assertFalse(app.settings["winrate"])

    def test_each_run_copies_gift_targets_and_resets_death_state(self):
        from source.teams import HARD, TEAMS
        p = SimpleNamespace(
            HARD=True, BUFF=[0]*10, HOS_MODE=False, EXTREME=False,
            DEAD=9, HOS_LAST_DEAD=9, HOS_DEFENSE_TURNS=5
        )
        set_team, _ = function("Bot.py", "set_team", dict(
            p=p, HARD=HARD, TEAMS=TEAMS, deepcopy=copy.deepcopy,
            SINNERS={"RYOSHU":()},
            generate_packs_pr=lambda x:{}, generate_packs_av=lambda x:{},
            generate_packs_all=lambda x:{}, logging=logging,
            validate_solo_selection=validate_solo_selection,
        ))
        teams={0:{"affinity":[0],"affinity_idx":0,"duplicates":False,
                  "sinners":[0],"priority":([],{}),"avoid":([],{},{})}}
        before=copy.deepcopy(HARD["BURN"])
        set_team(0,teams,{})
        self.assertEqual(HARD["BURN"],before)
        self.assertEqual(p.DEAD,0)
        self.assertEqual(p.HOS_DEFENSE_TURNS,0)
        self.assertIsNone(p.HOS_PROTECTED_GIFT)
        p.BUFF[3]=1
        set_team(0,teams,{})
        self.assertEqual(p.GIFTS[0]["uptie1"],before["uptie1"])

if __name__ == "__main__":
    unittest.main()
