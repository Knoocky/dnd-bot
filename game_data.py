import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
STATS_FILE = DATA_DIR / "stats.json"
RACES_FILE = DATA_DIR / "races.json"
CLASSES_FILE = DATA_DIR / "classes.json"


class GameDataError(RuntimeError):
    pass


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
        lookup[key.lower()] = key
        lookup[label.lower()] = key
    return lookup


def get_stat_label(stat_key: str) -> str:
    return get_stat_label_map().get(stat_key, stat_key)


@lru_cache(maxsize=1)
def get_races() -> list[dict]:
    races = _load_json_array(RACES_FILE)
    stat_keys = get_stat_key_set()
    validated = []
    seen_keys = set()
    for index, race in enumerate(races, start=1):
        key = str(race.get("key", "")).strip().lower()
        label = str(race.get("label", "")).strip()
        description = str(race.get("description", "")).strip()
        bonuses = race.get("bonuses", {})
        if not key or not label or not description:
            raise GameDataError(
                f"Элемент #{index} в races.json должен содержать `key`, `label` и `description`."
            )
        if key in seen_keys:
            raise GameDataError(f"Повторяющийся ключ расы в races.json: {key}")
        if not isinstance(bonuses, dict):
            raise GameDataError(f"Поле `bonuses` для расы {key} должно быть объектом.")
        normalized_bonuses = {}
        for bonus_key, bonus_value in bonuses.items():
            if bonus_key != "all" and bonus_key not in stat_keys:
                raise GameDataError(
                    f"Раса {key} содержит неизвестную характеристику в бонусах: {bonus_key}"
                )
            try:
                normalized_bonuses[bonus_key] = int(bonus_value)
            except (TypeError, ValueError) as exc:
                raise GameDataError(
                    f"Бонус характеристики `{bonus_key}` для расы {key} должен быть числом."
                ) from exc
        seen_keys.add(key)
        validated.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "bonuses": normalized_bonuses,
            }
        )
    return validated


@lru_cache(maxsize=1)
def get_race_map() -> dict[str, dict]:
    return {race["key"]: race for race in get_races()}


@lru_cache(maxsize=1)
def get_classes() -> list[dict]:
    classes = _load_json_array(CLASSES_FILE)
    stat_keys = get_stat_key_set()
    validated = []
    seen_keys = set()
    for index, cls in enumerate(classes, start=1):
        key = str(cls.get("key", "")).strip().lower()
        label = str(cls.get("label", "")).strip()
        description = str(cls.get("description", "")).strip()
        primary_stat = str(cls.get("primary_stat", "")).strip()
        if not key or not label or not description or not primary_stat:
            raise GameDataError(
                f"Элемент #{index} в classes.json должен содержать `key`, `label`, `description` и `primary_stat`."
            )
        if key in seen_keys:
            raise GameDataError(f"Повторяющийся ключ класса в classes.json: {key}")
        if primary_stat not in stat_keys:
            raise GameDataError(f"Класс {key} ссылается на неизвестную характеристику: {primary_stat}")
        try:
            base_hp = int(cls.get("base_hp"))
        except (TypeError, ValueError) as exc:
            raise GameDataError(f"Поле `base_hp` для класса {key} должно быть числом.") from exc
        seen_keys.add(key)
        validated.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "base_hp": base_hp,
                "primary_stat": primary_stat,
            }
        )
    return validated


@lru_cache(maxsize=1)
def get_class_map() -> dict[str, dict]:
    return {cls["key"]: cls for cls in get_classes()}


def get_race_label(race_key: str) -> str:
    return get_race_map().get(race_key, {}).get("label", race_key.capitalize())


def get_class_label(class_key: str) -> str:
    return get_class_map().get(class_key, {}).get("label", class_key.capitalize())


def apply_race_bonuses(stats: dict[str, int], race_key: str) -> dict[str, int]:
    race = get_race_map().get(race_key)
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
