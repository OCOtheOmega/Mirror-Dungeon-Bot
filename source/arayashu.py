"""Arayashu policy without GUI or game-input dependencies."""
from copy import deepcopy

class SoloSetupError(Exception):
    """Stop a run safely instead of silently discarding the solo strategy."""

SOLO_DEFAULTS = {"adversities": "all", "max_failed_runs": 2}
SOLO_ORDER = [3, 9, 2, 7, 6, 1, 10, 8, 0, 5]
SOLO_CARDS = [3, 1, 2, 0, 4]
SOLO_GRACES = {
    "deep": [1, 1, 1, 3, 0, 0, 3, 3, 0, 0],
    "economy": [1, 1, 1, 1, 0, 0, 1, 2, 0, 0],
}
SOLO_WORDLESS = {
    str(i): state for i, state in enumerate([
        3, 3, 2, 3, 1, 2,
        1, 2, 1, 2, 2, 2,
        2, 2, 2, 2, 2, 2,
        2, 1, 2, 2, 2, 2,
    ])
}
SOLO_PRIORITY = [
    "FlatbrokeGamblers", "TheOutcast", "NestWorkshopandTechnology",
    "HellsChicken", "HatredandDespair", "Line2", "Line1",
    "CodePurple", "SeaCR", "MidspringDream2", "CertainLibrary", "BridleofInfinity",
]
SOLO_PRIORITY_FLOORS = {
    "FlatbrokeGamblers": 1, "TheOutcast": 1, "NestWorkshopandTechnology": 1,
    "HellsChicken": 2, "HatredandDespair": 3, "Line2": 4,
    "BridleofInfinity": 15,
}
# Line1 stays flexible on 5-10. Other EXTREME choices stay flexible on 11-14,
# so a missed CodePurple roll can still be used later. Reserve Bridle for 15.
SOLO_AVOID_EXTRA = [
    "TheSurrenderedWitnessing", "FourHousesandGreed", "MnesticExperience",
    "Textbook", "BladeandArtwork", "TheUnsevering",
]

def normalize_solo_options(value):
    result = dict(SOLO_DEFAULTS)
    if not isinstance(value, dict):
        return result
    if value.get("adversities") in ("all", "none"):
        result["adversities"] = value["adversities"]
    limit = value.get("max_failed_runs")
    if type(limit) is int and 1 <= limit <= 10:
        result["max_failed_runs"] = limit
    return result

def solo_profile(name="deep"):
    if name not in SOLO_GRACES:
        raise ValueError("Unknown solo profile")
    return {
        "sinners": list(SOLO_ORDER),
        "affinity": [5, 7],
        "cards": list(SOLO_CARDS),
        "buff": list(SOLO_GRACES[name]),
        "keywordless": deepcopy(SOLO_WORDLESS),
        "priority": list(SOLO_PRIORITY),
        "priority_floors": dict(SOLO_PRIORITY_FLOORS),
        "avoid": list(SOLO_AVOID_EXTRA),
    }

def use_winrate(hard, extra, hos_mode):
    # EXTREME and the previous run's global state must not force Win Rate in HoS.
    return bool(hard or extra) and not hos_mode

def sacrifice_turn_limit(selected, dead):
    alive = max(0, selected - dead)
    return min((alive + 1) // 2, 5) if alive > 1 else 0

def validate_solo_selection(sinners):
    if not sinners or sinners[0] != "RYOSHU":
        raise SoloSetupError("Arayashu requires Ryoshu in deployment slot 1.")
    if len(sinners) != len(set(sinners)):
        raise SoloSetupError("Arayashu deployment contains duplicate Sinners.")
    if {"MEURSAULT", "GREGOR"} & set(sinners):
        raise SoloSetupError(
            "Leave Meursault and Gregor on the support bench, outside deployment/backups."
        )

def validate_sacrifice_progress(previous_dead, dead, submitted_turns, selected):
    if (previous_dead is not None and submitted_turns >= 2
            and dead <= previous_dead and selected - dead > 1):
        raise SoloSetupError(
            "Arayashu made no sacrifice progress after defensive turns. "
            "Stopped before spending more Starlight. Check Ryoshu's Identity, "
            "Phantom Step, deployment order and Spiderweb Entangled in Red."
        )

def guard_web_fusion(have):
    if "spiderweb" not in have:
        raise SoloSetupError(
            "Spiderweb Entangled in Red was not recognized in the complete inventory. "
            "Fusion stopped to protect it. Check the gift and display scale, then resume."
        )
    return have["spiderweb"]

def guard_fusion_selection(items, protected):
    if protected is None:
        raise SoloSetupError("Fusion has no verified Spiderweb protection.")
    px, py, page = protected
    for x, y, item_page in items:
        if item_page == page and abs(x - px) < 100 and abs(y - py) < 100:
            raise SoloSetupError("Blocked a fusion selection overlapping Spiderweb Entangled in Red.")

