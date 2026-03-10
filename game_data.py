import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
STATS_FILE = DATA_DIR / "stats.json"
RACES_FILE = DATA_DIR / "races.json"
CLASSES_FILE = DATA_DIR / "classes.json"
FEATS_FILE = DATA_DIR / "feats.json"


class GameDataError(RuntimeError):
    pass


def _normalize_lookup(value: str) -> str:
    return str(value).strip().casefold()


def _load_json_array(path: Path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise GameDataError(f"Не найден файл игровых данных: {path.name}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GameDataError(f"Некорректный JSON в {path.name}: {exc}") from exc

    if not isinstance(payload, list):
        raise GameDataError(f"Файл {path.name} должен содержать JSON-массив объектов.")

    validated = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise GameDataError(f"Элемент #{index} в {path.name} должен быть JSON-объектом.")
        validated.append(item)
    return validated


def _normalize_aliases(raw_aliases, context: str) -> list[str]:
    if raw_aliases is None:
        return []
    if not isinstance(raw_aliases, list):
        raise GameDataError(f"Поле `aliases` для {context} должно быть массивом строк.")
    normalized = []
    seen = set()
    for alias in raw_aliases:
        alias_text = str(alias).strip()
        if not alias_text:
            continue
        alias_key = _normalize_lookup(alias_text)
        if alias_key in seen:
            continue
        seen.add(alias_key)
        normalized.append(alias_text)
    return normalized


def _normalize_bonuses(raw_bonuses, context: str) -> dict[str, int]:
    if raw_bonuses is None:
        return {}
    if not isinstance(raw_bonuses, dict):
        raise GameDataError(f"Поле `bonuses` для {context} должно быть объектом.")

    stat_keys = get_stat_key_set()
    normalized = {}
    for stat_key, bonus_value in raw_bonuses.items():
        if stat_key != "all" and stat_key not in stat_keys:
            raise GameDataError(f"{context} содержит неизвестную характеристику в бонусах: {stat_key}")
        try:
            normalized[stat_key] = int(bonus_value)
        except (TypeError, ValueError) as exc:
            raise GameDataError(
                f"Бонус характеристики `{stat_key}` для {context} должен быть числом."
            ) from exc
    return normalized


def _validate_common_entry(raw_item: dict, *, context: str) -> dict:
    key = _normalize_lookup(raw_item.get("key", ""))
    label = str(raw_item.get("label", "")).strip()
    description = str(raw_item.get("description", "")).strip()
    if not key or not label or not description:
        raise GameDataError(f"{context} должен содержать `key`, `label` и `description`.")

    return {
        "key": key,
        "label": label,
        "description": description,
        "aliases": _normalize_aliases(raw_item.get("aliases"), context),
        "source": str(raw_item.get("source", "")).strip(),
        "legacy": bool(raw_item.get("legacy", False)),
        "requires_dm_approval": bool(raw_item.get("requires_dm_approval", False)),
        "bonuses": _normalize_bonuses(raw_item.get("bonuses"), context),
    }


def _build_lookup(items: list[dict]) -> dict[str, str]:
    lookup = {}
    for item in items:
        names = [item["key"], item["label"], *item.get("aliases", [])]
        for name in names:
            normalized = _normalize_lookup(name)
            if normalized:
                lookup[normalized] = item["key"]
    return lookup


@lru_cache(maxsize=1)
def get_stats() -> list[dict]:
    stats = _load_json_array(STATS_FILE)
    validated = []
    seen_keys = set()
    for index, stat in enumerate(stats, start=1):
        key = str(stat.get("key", "")).strip()
        label = str(stat.get("label", "")).strip()
        if not key or not label:
            raise GameDataError(f"Элемент #{index} в stats.json должен содержать `key` и `label`.")
        if key in seen_keys:
            raise GameDataError(f"Повторяющийся ключ характеристики в stats.json: {key}")
        seen_keys.add(key)
        order = stat.get("order", index)
        try:
            order = int(order)
        except (TypeError, ValueError) as exc:
            raise GameDataError(f"Поле `order` для характеристики {key} должно быть числом.") from exc
        validated.append({"key": key, "label": label, "order": order})
    validated.sort(key=lambda item: (item["order"], item["key"]))
    return validated


@lru_cache(maxsize=1)
def get_stat_label_map() -> dict[str, str]:
    return {stat["key"]: stat["label"] for stat in get_stats()}


@lru_cache(maxsize=1)
def get_stat_key_set() -> set[str]:
    return set(get_stat_label_map())


@lru_cache(maxsize=1)
def get_stat_lookup_map() -> dict[str, str]:
    lookup = {}
    for key, label in get_stat_label_map().items():
        lookup[_normalize_lookup(key)] = key
        lookup[_normalize_lookup(label)] = key
    return lookup


def resolve_stat_key(value: str) -> str | None:
    return get_stat_lookup_map().get(_normalize_lookup(value))


def get_stat_label(stat_key: str) -> str:
    resolved_key = resolve_stat_key(stat_key)
    return get_stat_label_map().get(resolved_key or stat_key, stat_key)


def _validate_subraces(race_key: str, raw_subraces) -> list[dict]:
    if raw_subraces is None:
        return []
    if not isinstance(raw_subraces, list):
        raise GameDataError(f"Поле `subraces` для расы {race_key} должно быть массивом объектов.")

    validated = []
    seen_keys = set()
    for index, subrace in enumerate(raw_subraces, start=1):
        if not isinstance(subrace, dict):
            raise GameDataError(f"Подраса #{index} для расы {race_key} должна быть JSON-объектом.")
        normalized = _validate_common_entry(subrace, context=f"Подраса {race_key}/{index}")
        if normalized["key"] in seen_keys:
            raise GameDataError(f"Повторяющийся ключ подрасы для расы {race_key}: {normalized['key']}")
        seen_keys.add(normalized["key"])
        validated.append(normalized)
    return validated


@lru_cache(maxsize=1)
def get_races() -> list[dict]:
    races = _load_json_array(RACES_FILE)
    validated = []
    seen_keys = set()
    for index, race in enumerate(races, start=1):
        normalized = _validate_common_entry(race, context=f"Элемент #{index} в races.json")
        if normalized["key"] in seen_keys:
            raise GameDataError(f"Повторяющийся ключ расы в races.json: {normalized['key']}")
        seen_keys.add(normalized["key"])
        normalized["subraces"] = _validate_subraces(normalized["key"], race.get("subraces", []))
        validated.append(normalized)
    return validated


@lru_cache(maxsize=1)
def get_race_map() -> dict[str, dict]:
    return {race["key"]: race for race in get_races()}


@lru_cache(maxsize=1)
def get_race_lookup_map() -> dict[str, str]:
    return _build_lookup(get_races())


def resolve_race_key(race_value: str) -> str | None:
    return get_race_lookup_map().get(_normalize_lookup(race_value))


def find_race(race_value: str) -> dict | None:
    race_key = resolve_race_key(race_value)
    if not race_key:
        return None
    return get_race_map().get(race_key)


@lru_cache(maxsize=None)
def get_subrace_lookup_map(race_key: str) -> dict[str, str]:
    race = get_race_map().get(race_key)
    if not race:
        return {}
    return _build_lookup(race.get("subraces", []))


def resolve_subrace_key(race_value: str, subrace_value: str) -> str | None:
    race_key = resolve_race_key(race_value)
    if not race_key:
        return None
    return get_subrace_lookup_map(race_key).get(_normalize_lookup(subrace_value))


def find_subrace(race_value: str, subrace_value: str) -> dict | None:
    race = find_race(race_value)
    if not race:
        return None
    subrace_key = resolve_subrace_key(race["key"], subrace_value)
    if not subrace_key:
        return None
    return next((item for item in race.get("subraces", []) if item["key"] == subrace_key), None)


def _validate_subclasses(class_key: str, raw_subclasses) -> list[dict]:
    if raw_subclasses is None:
        return []
    if not isinstance(raw_subclasses, list):
        raise GameDataError(f"Поле `subclasses` для класса {class_key} должно быть массивом объектов.")

    validated = []
    seen_keys = set()
    for index, subclass in enumerate(raw_subclasses, start=1):
        if not isinstance(subclass, dict):
            raise GameDataError(f"Подкласс #{index} для класса {class_key} должен быть JSON-объектом.")
        normalized = _validate_common_entry(subclass, context=f"Подкласс {class_key}/{index}")
        if normalized["key"] in seen_keys:
            raise GameDataError(f"Повторяющийся ключ подкласса для класса {class_key}: {normalized['key']}")
        seen_keys.add(normalized["key"])
        validated.append(normalized)
    return validated


@lru_cache(maxsize=1)
def get_classes() -> list[dict]:
    classes = _load_json_array(CLASSES_FILE)
    stat_keys = get_stat_key_set()
    validated = []
    seen_keys = set()

    for index, cls in enumerate(classes, start=1):
        normalized = _validate_common_entry(cls, context=f"Элемент #{index} в classes.json")
        primary_stat = resolve_stat_key(cls.get("primary_stat", ""))
        if not primary_stat or primary_stat not in stat_keys:
            raise GameDataError(
                f"Класс {normalized['key']} ссылается на неизвестную характеристику: {cls.get('primary_stat')}"
            )
        try:
            base_hp = int(cls.get("base_hp"))
        except (TypeError, ValueError) as exc:
            raise GameDataError(f"Поле `base_hp` для класса {normalized['key']} должно быть числом.") from exc

        try:
            subclass_level = int(cls.get("subclass_level", 3))
        except (TypeError, ValueError) as exc:
            raise GameDataError(
                f"Поле `subclass_level` для класса {normalized['key']} должно быть числом."
            ) from exc

        if normalized["key"] in seen_keys:
            raise GameDataError(f"Повторяющийся ключ класса в classes.json: {normalized['key']}")
        seen_keys.add(normalized["key"])
        normalized["base_hp"] = base_hp
        normalized["primary_stat"] = primary_stat
        normalized["subclass_level"] = subclass_level
        normalized["subclasses"] = _validate_subclasses(normalized["key"], cls.get("subclasses", []))
        validated.append(normalized)
    return validated


@lru_cache(maxsize=1)
def get_class_map() -> dict[str, dict]:
    return {cls["key"]: cls for cls in get_classes()}


@lru_cache(maxsize=1)
def get_class_lookup_map() -> dict[str, str]:
    return _build_lookup(get_classes())


def resolve_class_key(class_value: str) -> str | None:
    return get_class_lookup_map().get(_normalize_lookup(class_value))


def find_class(class_value: str) -> dict | None:
    class_key = resolve_class_key(class_value)
    if not class_key:
        return None
    return get_class_map().get(class_key)


@lru_cache(maxsize=None)
def get_subclass_lookup_map(class_key: str) -> dict[str, str]:
    class_data = get_class_map().get(class_key)
    if not class_data:
        return {}
    return _build_lookup(class_data.get("subclasses", []))


def resolve_subclass_key(class_value: str, subclass_value: str) -> str | None:
    class_key = resolve_class_key(class_value)
    if not class_key:
        return None
    return get_subclass_lookup_map(class_key).get(_normalize_lookup(subclass_value))


def find_subclass(class_value: str, subclass_value: str) -> dict | None:
    class_data = find_class(class_value)
    if not class_data:
        return None
    subclass_key = resolve_subclass_key(class_data["key"], subclass_value)
    if not subclass_key:
        return None
    return next((item for item in class_data.get("subclasses", []) if item["key"] == subclass_key), None)


@lru_cache(maxsize=1)
def get_feats() -> list[dict]:
    feats = _load_json_array(FEATS_FILE)
    validated = []
    seen_keys = set()
    for index, feat in enumerate(feats, start=1):
        normalized = _validate_common_entry(feat, context=f"черта #{index} в feats.json")
        if normalized["key"] in seen_keys:
            raise GameDataError(f"Дублирующийся ключ черты в feats.json: {normalized['key']}")
        seen_keys.add(normalized["key"])
        normalized["category"] = str(feat.get("category", "general")).strip() or "general"
        try:
            normalized["minimum_level"] = int(feat.get("minimum_level", 4))
        except (TypeError, ValueError) as exc:
            raise GameDataError(
                f"Поле `minimum_level` для черты {normalized['key']} должно быть числом."
            ) from exc
        normalized["grants_asi"] = bool(feat.get("grants_asi", False))
        validated.append(normalized)
    return validated


@lru_cache(maxsize=1)
def get_feat_map() -> dict[str, dict]:
    return {feat["key"]: feat for feat in get_feats()}


@lru_cache(maxsize=1)
def get_feat_lookup_map() -> dict[str, str]:
    return _build_lookup(get_feats())


def resolve_feat_key(feat_value: str) -> str | None:
    return get_feat_lookup_map().get(_normalize_lookup(feat_value))


def find_feat(feat_value: str) -> dict | None:
    feat_key = resolve_feat_key(feat_value)
    if not feat_key:
        return None
    return get_feat_map().get(feat_key)


def get_race_label(race_key: str) -> str:
    race = find_race(race_key)
    return race["label"] if race else str(race_key).strip().capitalize()


def get_subrace_label(race_key: str, subrace_key: str | None) -> str:
    if not subrace_key:
        return ""
    subrace = find_subrace(race_key, subrace_key)
    return subrace["label"] if subrace else str(subrace_key).strip().capitalize()


def get_class_label(class_key: str) -> str:
    class_data = find_class(class_key)
    return class_data["label"] if class_data else str(class_key).strip().capitalize()


def get_subclass_label(class_key: str, subclass_key: str | None) -> str:
    if not subclass_key:
        return ""
    subclass = find_subclass(class_key, subclass_key)
    return subclass["label"] if subclass else str(subclass_key).strip().capitalize()


def get_race_display(race_key: str, subrace_key: str | None = None) -> str:
    race_label = get_race_label(race_key)
    subrace_label = get_subrace_label(race_key, subrace_key)
    if subrace_label:
        return f"{subrace_label} {race_label}"
    return race_label


def get_class_display(class_key: str, subclass_key: str | None = None) -> str:
    class_label = get_class_label(class_key)
    subclass_label = get_subclass_label(class_key, subclass_key)
    if subclass_label:
        return f"{class_label} ({subclass_label})"
    return class_label


def get_character_archetype_text(
    race_key: str,
    class_key: str,
    subrace_key: str | None = None,
    subclass_key: str | None = None,
) -> str:
    return f"{get_race_display(race_key, subrace_key)} {get_class_display(class_key, subclass_key)}"


def apply_race_bonuses(stats: dict[str, int], race_key: str) -> dict[str, int]:
    race = find_race(race_key)
    if not race:
        raise GameDataError(f"Неизвестная раса: {race_key}")

    updated = dict(stats)
    bonuses = race.get("bonuses", {})
    for stat_key, bonus_value in bonuses.items():
        if stat_key == "all":
            for key in get_stat_key_set():
                updated[key] = updated.get(key, 0) + bonus_value
            continue
        updated[stat_key] = updated.get(stat_key, 0) + bonus_value
    return updated


def apply_origin_asi(stats: dict[str, int], bonuses: dict[str, int]) -> dict[str, int]:
    updated = dict(stats)
    for stat_key, bonus_value in bonuses.items():
        resolved_key = resolve_stat_key(stat_key)
        if not resolved_key:
            raise GameDataError(f"Неизвестная характеристика для ASI: {stat_key}")
        updated[resolved_key] = updated.get(resolved_key, 0) + int(bonus_value)
    return updated
