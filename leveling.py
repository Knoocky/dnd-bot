from __future__ import annotations

import copy
from math import ceil

import game_data

XP_THRESHOLDS = {
    1: 0,
    2: 300,
    3: 900,
    4: 2700,
    5: 6500,
    6: 14000,
    7: 23000,
    8: 34000,
    9: 48000,
    10: 64000,
    11: 85000,
    12: 100000,
    13: 120000,
    14: 140000,
    15: 165000,
    16: 195000,
    17: 225000,
    18: 265000,
    19: 305000,
    20: 355000,
}

HP_GAIN_MODES = {
    "1": "fixed",
    "fixed": "fixed",
    "фикс": "fixed",
    "фиксированный": "fixed",
    "2": "roll",
    "roll": "roll",
    "бросок": "roll",
    "кубик": "roll",
    "3": "choose_each_level",
    "choose": "choose_each_level",
    "choice": "choose_each_level",
    "выбор": "choose_each_level",
    "каждый": "choose_each_level",
}

SUPPORTED_2024_CLASSES = {
    "barbarian",
    "bard",
    "cleric",
    "druid",
    "fighter",
    "monk",
    "paladin",
    "ranger",
    "rogue",
    "sorcerer",
    "warlock",
    "wizard",
}

FEAT_LEVELS_BY_CLASS = {
    "barbarian": {4, 8, 12, 16, 19},
    "bard": {4, 8, 12, 16, 19},
    "cleric": {4, 8, 12, 16, 19},
    "druid": {4, 8, 12, 16, 19},
    "fighter": {4, 6, 8, 12, 14, 16, 19},
    "monk": {4, 8, 12, 16, 19},
    "paladin": {4, 8, 12, 16, 19},
    "ranger": {4, 8, 12, 16, 19},
    "rogue": {4, 8, 10, 12, 16, 19},
    "sorcerer": {4, 8, 12, 16, 19},
    "warlock": {4, 8, 12, 16, 19},
    "wizard": {4, 8, 12, 16, 19},
}

FULL_CASTER_SLOTS = {
    1: {1: 2}, 2: {1: 3}, 3: {1: 4, 2: 2}, 4: {1: 4, 2: 3}, 5: {1: 4, 2: 3, 3: 2},
    6: {1: 4, 2: 3, 3: 3}, 7: {1: 4, 2: 3, 3: 3, 4: 1}, 8: {1: 4, 2: 3, 3: 3, 4: 2},
    9: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1}, 10: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    11: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1}, 12: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    13: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1}, 14: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1}, 16: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1}, 18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 1, 7: 1, 8: 1, 9: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 1, 8: 1, 9: 1}, 20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1},
}

HALF_CASTER_SLOTS = {
    1: {}, 2: {1: 2}, 3: {1: 3}, 4: {1: 3}, 5: {1: 4, 2: 2}, 6: {1: 4, 2: 2},
    7: {1: 4, 2: 3}, 8: {1: 4, 2: 3}, 9: {1: 4, 2: 3, 3: 2}, 10: {1: 4, 2: 3, 3: 2},
    11: {1: 4, 2: 3, 3: 3}, 12: {1: 4, 2: 3, 3: 3}, 13: {1: 4, 2: 3, 3: 3, 4: 1}, 14: {1: 4, 2: 3, 3: 3, 4: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 2}, 16: {1: 4, 2: 3, 3: 3, 4: 2}, 17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1}, 19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2}, 20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
}

WARLOCK_SLOTS = {
    1: {1: 1}, 2: {1: 2}, 3: {2: 2}, 4: {2: 2}, 5: {3: 2}, 6: {3: 2}, 7: {4: 2}, 8: {4: 2}, 9: {5: 2}, 10: {5: 2},
    11: {5: 3}, 12: {5: 3}, 13: {5: 3}, 14: {5: 3}, 15: {5: 3}, 16: {5: 3}, 17: {5: 4}, 18: {5: 4}, 19: {5: 4}, 20: {5: 4},
}

KNOWN_CANTRIPS = {
    "bard": {1: 2, 4: 3, 10: 4},
    "cleric": {1: 3, 4: 4, 10: 5},
    "druid": {1: 2, 4: 3, 10: 4},
    "sorcerer": {1: 4, 4: 5, 10: 6},
    "warlock": {1: 2, 4: 3, 10: 4},
    "wizard": {1: 3, 4: 4, 10: 5},
}

KNOWN_SPELLS = {
    "bard": [0, 4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 15, 16, 17, 17, 18, 18, 19, 20, 21, 22],
    "ranger": [0, 0, 2, 3, 4, 4, 5, 6, 7, 7, 9, 9, 10, 11, 11, 12, 13, 14, 14, 15, 16],
    "sorcerer": [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 11, 12, 13, 13, 14, 15, 15, 16, 17, 17],
    "warlock": [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15],
}

PREPARED_CASTER_FORMULAS = {
    "cleric": lambda level, mod: max(1, level + mod),
    "druid": lambda level, mod: max(1, level + mod),
    "paladin": lambda level, mod: max(1, ceil(level / 2) + mod) if level >= 2 else 0,
    "wizard": lambda level, mod: max(1, level + mod),
}

SPELLCASTING_ABILITIES = {
    "bard": "charisma",
    "cleric": "wisdom",
    "druid": "wisdom",
    "paladin": "charisma",
    "ranger": "wisdom",
    "sorcerer": "charisma",
    "warlock": "charisma",
    "wizard": "intelligence",
}

WIZARD_SPELLBOOK_GAIN = 2

LEVELUP_TIMEOUT_MINUTES = 15


def get_hp_gain_mode(choice: str | None) -> str | None:
    if choice is None:
        return None
    return HP_GAIN_MODES.get(str(choice).strip().lower())


def is_supported_class_for_campaign(class_key: str, ruleset: str = "dnd_2024_phb") -> bool:
    if ruleset != "dnd_2024_phb":
        return True
    return class_key in SUPPORTED_2024_CLASSES


def get_available_classes(ruleset: str = "dnd_2024_phb") -> list[dict]:
    return [cls for cls in game_data.get_classes() if is_supported_class_for_campaign(cls["key"], ruleset)]


def level_for_xp(exp: int) -> int:
    value = max(int(exp), 0)
    current_level = 1
    for level, threshold in XP_THRESHOLDS.items():
        if value >= threshold:
            current_level = level
    return current_level


def proficiency_bonus_for_level(level: int) -> int:
    normalized = max(1, min(int(level), 20))
    return 2 + (normalized - 1) // 4


def next_level_xp(level: int) -> int | None:
    normalized = max(1, min(int(level), 20))
    if normalized >= 20:
        return None
    return XP_THRESHOLDS[normalized + 1]


def progress_summary(exp: int, level: int) -> dict:
    next_threshold = next_level_xp(level)
    if next_threshold is None:
        return {"current": exp, "next": None, "remaining": 0, "progress": 1.0}
    current_threshold = XP_THRESHOLDS[level]
    earned = max(0, exp - current_threshold)
    span = max(1, next_threshold - current_threshold)
    return {
        "current": exp,
        "next": next_threshold,
        "remaining": max(0, next_threshold - exp),
        "progress": min(1.0, earned / span),
    }


def fixed_hp_gain_for_class(class_key: str) -> int:
    class_data = game_data.find_class(class_key)
    hit_die = int(class_data["base_hp"]) if class_data else 8
    return hit_die // 2 + 1


def hit_die_label_for_class(class_key: str) -> str:
    class_data = game_data.find_class(class_key)
    hit_die = int(class_data["base_hp"]) if class_data else 8
    return f"d{hit_die}"


def build_class_levels(class_key: str, level: int) -> dict[str, int]:
    return {class_key: int(level)}


def build_hit_dice(class_key: str, level: int) -> dict[str, int]:
    return {hit_die_label_for_class(class_key): int(level)}


def _baseline_spellcasting(class_key: str) -> dict:
    if class_key not in SPELLCASTING_ABILITIES:
        return {
            "caster_type": "none",
            "spellcasting_ability": None,
            "slots": {},
            "known_spells": [],
            "known_cantrips": [],
            "known_spells_limit": 0,
            "known_cantrips_limit": 0,
            "prepared_limit": 0,
            "spellbook": [],
            "notes": [],
        }
    caster_type = "prepared"
    if class_key in {"bard", "ranger", "sorcerer"}:
        caster_type = "known"
    if class_key == "warlock":
        caster_type = "warlock"
    if class_key == "wizard":
        caster_type = "wizard"
    return {
        "caster_type": caster_type,
        "spellcasting_ability": SPELLCASTING_ABILITIES[class_key],
        "slots": {},
        "known_spells": [],
        "known_cantrips": [],
        "known_spells_limit": 0,
        "known_cantrips_limit": 0,
        "prepared_limit": 0,
        "spellbook": [],
        "notes": [],
    }


def _lookup_progression_value(progression: dict[int, int], level: int) -> int:
    value = 0
    for at_level in sorted(progression):
        if level >= at_level:
            value = progression[at_level]
    return value


def recalculate_spellcasting(class_key: str, class_level: int, ability_scores: dict[str, int], existing: dict | None = None) -> dict:
    state = copy.deepcopy(existing or _baseline_spellcasting(class_key))
    if class_key not in SPELLCASTING_ABILITIES:
        return state

    ability_key = SPELLCASTING_ABILITIES[class_key]
    ability_mod = (int(ability_scores.get(ability_key, 10)) - 10) // 2
    state["spellcasting_ability"] = ability_key

    if class_key in {"bard", "cleric", "druid", "sorcerer", "wizard"}:
        state["slots"] = {str(k): v for k, v in FULL_CASTER_SLOTS.get(class_level, {}).items()}
    elif class_key in {"paladin", "ranger"}:
        state["slots"] = {str(k): v for k, v in HALF_CASTER_SLOTS.get(class_level, {}).items()}
    elif class_key == "warlock":
        state["slots"] = {str(k): v for k, v in WARLOCK_SLOTS.get(class_level, {}).items()}

    state["known_cantrips_limit"] = _lookup_progression_value(KNOWN_CANTRIPS.get(class_key, {}), class_level)
    if class_key in KNOWN_SPELLS:
        state["known_spells_limit"] = KNOWN_SPELLS[class_key][class_level]
    if class_key in PREPARED_CASTER_FORMULAS:
        state["prepared_limit"] = PREPARED_CASTER_FORMULAS[class_key](class_level, ability_mod)
    if class_key == "wizard" and len(state.get("spellbook", [])) < 6 and class_level >= 1:
        state["spellbook"] = list(state.get("spellbook", []))
    return state


def initialize_build_state(class_key: str, level: int, ability_scores: dict[str, int], subclass_key: str | None = None) -> dict:
    spellcasting = recalculate_spellcasting(class_key, level, ability_scores)
    return {
        "class_levels": build_class_levels(class_key, level),
        "build_choices": {"subclass": subclass_key, "class_feature_choices": [], "audit_notes": []},
        "feats": [],
        "resources": {},
        "spellcasting": spellcasting,
        "hit_dice": build_hit_dice(class_key, level),
        "levelup_state": {"legacy_untracked": level > 1, "eligible_level": level, "last_completed_level": level},
    }


def ensure_character_build(char: dict) -> tuple[dict, dict]:
    class_key = char["class"]
    level = int(char.get("level", 1))
    ability_scores = {stat["key"]: int(char.get(stat["key"], 10)) for stat in game_data.get_stats()}
    updates = {}

    class_levels = copy.deepcopy(char.get("class_levels") or {})
    if not class_levels:
        class_levels = build_class_levels(class_key, level)
        updates["class_levels"] = class_levels

    build_choices = copy.deepcopy(char.get("build_choices") or {})
    build_choices.setdefault("subclass", char.get("subclass"))
    build_choices.setdefault("class_feature_choices", [])
    build_choices.setdefault("audit_notes", [])
    updates["build_choices"] = build_choices

    feats = list(char.get("feats") or [])
    updates["feats"] = feats

    resources = copy.deepcopy(char.get("resources") or {})
    updates["resources"] = resources

    spellcasting = recalculate_spellcasting(class_key, level, ability_scores, char.get("spellcasting") or {})
    updates["spellcasting"] = spellcasting

    hit_dice = copy.deepcopy(char.get("hit_dice") or {})
    if not hit_dice:
        hit_dice = build_hit_dice(class_key, level)
    updates["hit_dice"] = hit_dice

    levelup_state = copy.deepcopy(char.get("levelup_state") or {})
    levelup_state.setdefault("legacy_untracked", level > 1 and not bool(char.get("class_levels")))
    levelup_state.setdefault("eligible_level", level_for_xp(int(char.get("exp", 0))))
    levelup_state.setdefault("last_completed_level", level)
    updates["levelup_state"] = levelup_state

    normalized = dict(char)
    normalized.update(updates)
    normalized["subclass"] = normalized.get("subclass") or build_choices.get("subclass")
    return normalized, updates


def get_character_progress(char: dict) -> dict:
    current_level = int(char.get("level", 1))
    exp_value = int(char.get("exp", 0))
    eligible_level = level_for_xp(exp_value)
    summary = progress_summary(exp_value, current_level)
    return {
        "current_level": current_level,
        "eligible_level": eligible_level,
        "ready": eligible_level > current_level,
        **summary,
    }


def get_available_feats(level: int, *, epic_only: bool = False) -> list[dict]:
    feats = game_data.get_feats()
    result = []
    for feat in feats:
        if int(feat.get("minimum_level", 4)) > level:
            continue
        if epic_only and feat.get("category") != "epic_boon":
            continue
        if not epic_only and feat.get("category") == "epic_boon":
            continue
        result.append(feat)
    return result


def get_feat_levels_for_class(class_key: str) -> set[int]:
    return FEAT_LEVELS_BY_CLASS.get(class_key, {4, 8, 12, 16, 19})


def build_level_steps(char: dict, campaign: dict) -> list[dict]:
    class_key = char["class"]
    next_level = int(char.get("level", 1)) + 1
    class_levels = char.get("class_levels") or {class_key: char.get("level", 1)}
    next_class_level = int(class_levels.get(class_key, 0)) + 1
    class_data = game_data.find_class(class_key)
    steps = []

    hp_mode = campaign.get("hp_gain_mode") or "fixed"
    if hp_mode == "choose_each_level":
        steps.append({"type": "hp_mode", "level": next_level, "options": ["fixed", "roll"]})

    subclass_level = int(class_data.get("subclass_level", 3)) if class_data else 3
    if next_class_level >= subclass_level and not char.get("subclass"):
        steps.append({
            "type": "subclass",
            "level": next_level,
            "class_level": next_class_level,
            "options": [item["key"] for item in class_data.get("subclasses", [])],
        })

    if next_class_level in get_feat_levels_for_class(class_key):
        steps.append({
            "type": "feat",
            "level": next_level,
            "class_level": next_class_level,
            "epic_only": next_class_level >= 19,
        })

    spell_step = build_spell_step(char, next_class_level)
    if spell_step:
        steps.append(spell_step)

    return steps


def build_spell_step(char: dict, next_class_level: int) -> dict | None:
    class_key = char["class"]
    current = recalculate_spellcasting(class_key, next_class_level - 1, char, char.get("spellcasting") or {})
    upcoming = recalculate_spellcasting(class_key, next_class_level, char, char.get("spellcasting") or {})
    notes = []
    cantrip_delta = int(upcoming.get("known_cantrips_limit", 0)) - int(current.get("known_cantrips_limit", 0))
    spell_delta = int(upcoming.get("known_spells_limit", 0)) - int(current.get("known_spells_limit", 0))
    if class_key == "wizard":
        notes.append("добавь 2 заклинания в книгу")
    if cantrip_delta > 0:
        notes.append(f"выбери {cantrip_delta} новых кантрипов")
    if spell_delta > 0:
        notes.append(f"выбери {spell_delta} новых заклинаний")
    if not notes:
        return None
    return {
        "type": "spell_notes",
        "level": int(char.get("level", 1)) + 1,
        "class_level": next_class_level,
        "prompt": "; ".join(notes),
    }


def parse_feat_choice(raw_value: str, level: int) -> dict | None:
    stripped = raw_value.strip()
    if not stripped:
        return None
    tokens = [token for token in stripped.replace(",", " ").split() if token]
    lowered = [token.casefold() for token in tokens]
    if lowered and lowered[0] in {"asi", "улучшение", "характеристики", "ability", "score"}:
        stat_tokens = tokens[1:]
        if len(stat_tokens) == 1:
            stat_key = game_data.resolve_stat_key(stat_tokens[0])
            if not stat_key:
                return None
            return {"type": "asi", "bonuses": {stat_key: 2}}
        if len(stat_tokens) == 2:
            stat_keys = [game_data.resolve_stat_key(token) for token in stat_tokens]
            if not all(stat_keys):
                return None
            bonuses = {}
            for stat_key in stat_keys:
                bonuses[stat_key] = bonuses.get(stat_key, 0) + 1
            return {"type": "asi", "bonuses": bonuses}
        return None

    feat = game_data.find_feat(stripped)
    if not feat:
        return None
    if int(feat.get("minimum_level", 4)) > level:
        return None
    return {"type": "feat", "feat": feat}


def apply_asi(char: dict, bonuses: dict[str, int]) -> tuple[dict, list[str]]:
    updates = {}
    summary = []
    for stat_key, bonus in bonuses.items():
        current_value = int(char.get(stat_key, 10))
        final_value = min(20, current_value + int(bonus))
        updates[stat_key] = final_value
        summary.append(f"{game_data.get_stat_label(stat_key)} +{final_value - current_value}")
    return updates, summary


def apply_level_gain(char: dict, campaign: dict, session_state: dict) -> tuple[dict, list[str]]:
    normalized, ensure_updates = ensure_character_build(char)
    updates = dict(ensure_updates)
    log_lines = []
    class_key = normalized["class"]
    current_level = int(normalized.get("level", 1))
    next_level = current_level + 1
    class_levels = copy.deepcopy(normalized.get("class_levels") or {})
    class_levels[class_key] = int(class_levels.get(class_key, 0)) + 1
    updates["class_levels"] = class_levels

    hp_mode_used = session_state.get("hp_mode_used") or campaign.get("hp_gain_mode") or "fixed"
    con_mod = (int(normalized.get("constitution", 10)) - 10) // 2
    hp_gain = fixed_hp_gain_for_class(class_key)
    if hp_mode_used == "roll":
        hp_gain = int(session_state.get("rolled_hp", fixed_hp_gain_for_class(class_key)))
    hp_gain = max(1, hp_gain + con_mod)
    updates["max_hp"] = int(normalized.get("max_hp", normalized.get("hp", 1))) + hp_gain
    updates["hp"] = int(normalized.get("hp", 1)) + hp_gain
    log_lines.append(f"HP +{hp_gain}")

    if session_state.get("subclass"):
        updates["subclass"] = session_state["subclass"]
        build_choices = copy.deepcopy(normalized.get("build_choices") or {})
        build_choices["subclass"] = session_state["subclass"]
        updates["build_choices"] = build_choices
        log_lines.append(f"Подкласс: {game_data.get_subclass_label(class_key, session_state['subclass'])}")

    feat_state = session_state.get("feat_choice")
    if feat_state:
        feats = list(normalized.get("feats") or [])
        if feat_state["type"] == "feat":
            feats.append({"level": next_level, "key": feat_state["feat"]["key"], "label": feat_state["feat"]["label"]})
            log_lines.append(f"Фит: {feat_state['feat']['label']}")
        else:
            asi_updates, asi_summary = apply_asi(normalized, feat_state["bonuses"])
            normalized.update(asi_updates)
            updates.update(asi_updates)
            feats.append({"level": next_level, "key": "ability-score-improvement", "label": "Ability Score Improvement", "bonuses": feat_state["bonuses"]})
            log_lines.append("ASI: " + ", ".join(asi_summary))
        updates["feats"] = feats

    spellcasting = recalculate_spellcasting(class_key, class_levels[class_key], normalized, normalized.get("spellcasting") or {})
    notes = list(spellcasting.get("notes", []))
    if session_state.get("spell_note"):
        notes.append({"level": next_level, "text": session_state["spell_note"]})
        log_lines.append("Заклинания обновлены")
    spellcasting["notes"] = notes
    updates["spellcasting"] = spellcasting

    hit_dice = copy.deepcopy(normalized.get("hit_dice") or {})
    die_key = hit_die_label_for_class(class_key)
    hit_dice[die_key] = int(hit_dice.get(die_key, 0)) + 1
    updates["hit_dice"] = hit_dice

    updates["level"] = next_level
    levelup_state = copy.deepcopy(normalized.get("levelup_state") or {})
    levelup_state["eligible_level"] = level_for_xp(int(normalized.get("exp", 0)))
    levelup_state["last_completed_level"] = next_level
    updates["levelup_state"] = levelup_state
    return updates, log_lines


def build_levelup_embed_lines(char: dict, campaign: dict, session_state: dict) -> list[str]:
    next_level = int(char.get("level", 1)) + 1
    lines = [f"Персонаж: **{char['name']}**", f"Текущий уровень: **{char['level']}** -> **{next_level}**"]
    lines.append(f"Класс: **{game_data.get_class_label(char['class'])}**")
    if session_state.get("steps"):
        current_step = session_state["steps"][session_state.get("step_index", 0)]
        lines.append(f"Текущий шаг: **{current_step['type']}**")
    else:
        lines.append("Все выборы уровня готовы к применению.")
    return lines


def build_audit_notes(char: dict) -> list[str]:
    notes = []
    class_data = game_data.find_class(char["class"])
    if int(char.get("level", 1)) >= int(class_data.get("subclass_level", 3)) and not char.get("subclass"):
        notes.append("У персонажа отсутствует обязательный подкласс для текущего уровня.")
    return notes
