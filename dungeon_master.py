import json
import re

import adventure_library
import ai_provider_runtime as ai_provider
import database as db
import game_data

from pathlib import Path
import asyncio
import logging
import threading
import time

PROMPTS_DIR = Path(__file__).resolve().parent / "data" / "prompts"
logger = logging.getLogger("dnd_bot.dm")


def _load_prompt(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()

CLAUDE_SYSTEM_PROMPT = _load_prompt("claude_system_prompt_ru_v_1.md")
OPENAI_SYSTEM_PROMPT = _load_prompt("openai_system_prompt_ru_v_1.md")
READY_ADVENTURE_SYSTEM_PROMPT = _load_prompt("ready_adventure_system_prompt_ru_v_1.md")
READY_ADVENTURE_ADAPTATION_SYSTEM_PROMPT = _load_prompt("ready_adventure_adaptation_system_prompt_ru_v_1.md")
ADVENTURE_GM_BRIEF_PROMPT = _load_prompt("adventure_gm_brief_prompt_ru_v_1.md")
LOCAL_SYSTEM_PROMPT = """Ты — DnD-Bot, ведущий текстовой приключенческой игры.

Главное:
- веди сцену, мир, NPC и последствия;
- не принимай решения за игрока;
- не объявляй новые локации, NPC, предметы и удобные ресурсы установленным фактом только со слов игрока;
- если исход неочевиден и есть риск, допускай проверку; если успех очевиден, не требуй бросок;
- провалы должны менять ситуацию, а не просто останавливать игру;
- держи в фокусе главную сюжетную линию и 1-2 поддерживающих;
- соблюдай ограничения игрока и не добавляй неуместный чувствительный контент.

Формат ответа:
- пиши по-русски;
- отвечай как ведущий сцены, а не как отчёт;
- предпочитай 2-3 плотных абзаца живой сцены;
- показывай последствия через повествование, а не сухую механику;
- в конце почти каждого игрового ответа предлагай 3-5 вариантов действий нумерованным списком;
- в бою держи позицию, угрозы, HP и важные состояния последовательно.
"""

ANALYZER_PROMPT = """Ты анализируешь последнее сообщение ведущего текстовой D&D-сцены для служебной логики Discord-бота.
Верни только JSON без markdown и пояснений.

Формат ответа:
{
  "should_open_round": true,
  "wait_mode": "all_alive_except_out_of_scene",
  "options": ["вариант 1", "вариант 2"],
  "out_of_scene_characters": [
    {"name": "Имя", "reason": "почему персонаж вне сцены"}
  ]
}

Правила:
- should_open_round=true только если в сообщении ведущего действительно есть варианты действий для игроков.
- options должны содержать только короткие тексты самих вариантов, без нумерации.
- По умолчанию нужно ждать всех живых персонажей.
- Исключай персонажа в out_of_scene_characters только если по контексту сцены явно видно, что он находится в другом месте и не может повлиять на текущий эпизод.
- Если нет явных исключений, верни пустой список out_of_scene_characters.
- Используй только имена персонажей из предоставленного списка.
- wait_mode всегда должен быть "all_alive_except_out_of_scene".
"""

ROLL_ANALYZER_PROMPT = """Ты определяешь, требует ли действие игрока бросок кубика для Discord D&D-бота.
Верни только JSON без markdown и пояснений.

Формат ответа:
{
  "needs_roll": true,
  "dice_count": 1,
  "dice_sides": 20,
  "modifier_stat": "dexterity",
  "reason": "короткое пояснение"
}

Правила:
- needs_roll=true только если исход действия действительно неопределён, рискован и имеет заметные последствия при успехе или провале.
- Если бросок не нужен, верни needs_roll=false и reason.
- По умолчанию считай, что бросок НЕ нужен, пока нет явной причины запросить проверку.
- Для обычных проверок и атак чаще всего используй 1d20.
- dice_count и dice_sides должны быть положительными числами.
- modifier_stat должен быть одним из: strength, dexterity, constitution, intelligence, wisdom, charisma, либо null.
- Не требуй бросок для чисто разговорных, очевидных, подготовительных или автоматически успешных действий.
- Обычно не нужен бросок для: начала разговора, уточняющих вопросов, осмотра очевидной обстановки, выбора направления, взятия обычной подработки, перемещения без опасности, покупки обычных товаров, бытовых действий и заявок без немедленного сопротивления.
- Нужен бросок, если есть сопротивление, скрытая информация, риск провала, давление времени, опасность, физическая сложность, социальное противодействие или цена ошибки.
- Если игрок просто заявляет намерение, а не пытается прямо сейчас преодолеть препятствие, чаще возвращай needs_roll=false.
- Учитывай недавний контекст сцены, но не выдумывай скрытых правил.
"""

XP_ANALYZER_PROMPT = """Ты анализируешь, нужно ли начислить XP за последнее сообщение ведущего в D&D-кампании.
Верни только JSON без markdown и пояснений.

Формат ответа:
{
  "award_xp": true,
  "amount": 40,
  "reason": "короткая причина значимой вехи",
  "recipient_mode": "all_alive_except_out_of_scene",
  "excluded_characters": [
    {"name": "Имя", "reason": "почему персонаж вне сцены"}
  ]
}

Правила:
- award_xp=true только если в сцене завершилась действительно значимая веха: закрыта важная цель, разрешена заметная угроза, выигран законченный бой, завершён весомый этап квеста или добыта крупная сюжетная зацепка, которая реально двигает кампанию.
- Не выдавай XP за отдельные проверки, локальные тактические успехи, частичный прогресс, удачные реплики, бытовые сцены, мелкие находки, подготовку, разведку без развязки и незавершённые ситуации.
- Если это просто хороший ход внутри продолжающейся сцены, верни award_xp=false.
- Если в той же сцене уже был значимый успех, не выдавай новую награду за его развитие или закрепление.
- amount должен быть небольшим и консервативным. Для обычной значимой вехи предпочитай диапазон 20-50. Значения выше 50 не используй.
- reason должен быть коротким и конкретным, без канцелярита.
- recipient_mode всегда должен быть all_alive_except_out_of_scene.
- excluded_characters должны содержать только имена из списка партии и только если персонаж явно отсутствовал в сцене.
"""

BLOCKED_PHRASES = [
    "игнорируй инструкции",
    "ignore instructions",
    "ignore your",
    "забудь роль",
    "забудь кто ты",
    "ты не мастер",
    "выйди из роли",
    "forget your instructions",
    "you are not",
    "ты не персонаж",
    "отвечай как chatgpt",
    "отвечай как ии",
    "притворись что ты",
    "jailbreak",
    "dan mode",
    "developer mode",
]

VALID_STATS = game_data.get_stat_key_set()
EMBEDDED_PARTY_CONTEXT_RE = re.compile(
    r"^\[\s*АКТИВНЫЕ ПЕРСОНАЖИ В ПАРТИИ:.*?\]\s*",
    re.DOTALL,
)
LOCAL_MESSAGE_LIMIT = 10
LOCAL_MESSAGE_CHAR_LIMIT = 700
LOCAL_HISTORY_SUMMARY_LIMIT = 14
LOCAL_HISTORY_SUMMARY_CHAR_LIMIT = 2800
LOCAL_CHARACTERS_CONTEXT_CHAR_LIMIT = 600
ROLL_REQUEST_PATTERN = re.compile(
    r"\[ROLL_REQUEST\]\s*(\{.*?\})\s*\[/ROLL_REQUEST\]",
    re.DOTALL | re.IGNORECASE,
)

ROLL_MODE_CONTEXT = {
    "bot_auto": (
        "РЕЖИМ БРОСКОВ: bot_auto. Решение о необходимости броска принимаешь только ты как ведущий. "
        "Если бросок не нужен, просто продолжай сцену обычным ответом. "
        "Если бросок нужен, не разрешай исход действия до результата броска и закончи ответ точным служебным блоком:\n"
        "[ROLL_REQUEST]\n"
        "{\"target_name\": null, \"dice_count\": 1, \"dice_sides\": 20, \"modifier_stat\": \"dexterity\", \"reason\": \"короткая причина\"}\n"
        "[/ROLL_REQUEST]\n"
        "Запрашивай не больше одного такого блока за ответ. "
        "Если в сцене несколько действующих персонажей и бросок нужен конкретному из них, укажи его имя в target_name точно как в контексте. "
        "Перед этим блоком можно дать 1-3 короткие фразы художественной подводки, но не описывай окончательный успех или провал. "
        "Бот сам бросит кубики и пришлёт тебе результат. Не проси игроков кидать вручную."
    ),
    "player_manual": (
        "РЕЖИМ БРОСКОВ: player_manual. Решение о необходимости броска принимаешь только ты как ведущий. "
        "Если бросок не нужен, просто продолжай сцену обычным ответом. "
        "Если бросок нужен, не разрешай исход действия до результата броска и закончи ответ точным служебным блоком:\n"
        "[ROLL_REQUEST]\n"
        "{\"target_name\": null, \"dice_count\": 1, \"dice_sides\": 20, \"modifier_stat\": \"dexterity\", \"reason\": \"короткая причина\"}\n"
        "[/ROLL_REQUEST]\n"
        "Запрашивай не больше одного такого блока за ответ. "
        "Если в сцене несколько действующих персонажей и бросок нужен конкретному из них, укажи его имя в target_name точно как в контексте. "
        "Перед этим блоком можно дать 1-3 короткие фразы художественной подводки, но не описывай окончательный успех или провал. "
        "Бот сам попросит игрока о броске и позже пришлёт тебе результат. Не требуй команду `!бросок` внутри ответа."
    ),
}

MAIN_QUEST_PRESSURE_CONTEXT = {
    "rare": (
        "РЕЖИМ ДАВЛЕНИЯ ГЛАВНОГО СЮЖЕТА: rare. "
        "Всегда держи в фокусе 1 главный квест и при необходимости ещё 1-2 поддерживающих, но не больше 3 ведущих линий одновременно. "
        "Побочные сцены допустимы, однако серьёзные последствия за уход в сторону включай только после повторного или длительного игнора главной линии. "
        "Если герои надолго отвлекаются, мир движется без них: следы стынут, антагонисты укрепляются, окна возможностей закрываются."
    ),
    "soft": (
        "РЕЖИМ ДАВЛЕНИЯ ГЛАВНОГО СЮЖЕТА: soft. "
        "Всегда держи в фокусе 1 главный квест и при необходимости ещё 1-2 поддерживающих, но не больше 3 ведущих линий одновременно. "
        "Почти каждое заметное отклонение от главной линии должно иметь мягкую, но ощутимую цену: потерю времени, ресурса, позиции, свежести следов, выгодной цены, доступа к NPC или окна возможности. "
        "Не ломай кампанию сразу, но регулярно напоминай через мир, что промедление чего-то стоит."
    ),
    "hard": (
        "РЕЖИМ ДАВЛЕНИЯ ГЛАВНОГО СЮЖЕТА: hard. "
        "Всегда держи в фокусе 1 главный квест и при необходимости ещё 1-2 поддерживающих, но не больше 3 ведущих линий одновременно. "
        "Заметное отклонение от главной линии должно быстро вызывать серьёзные сюжетные потери: враги уходят вперёд, следы исчезают, союзники меняют позицию, цены растут, угрозы усиливаются, а упущенные возможности реально закрываются. "
        "Наказывай миром и временем, а не произволом, и всё равно сохраняй логичность последствий."
    ),
}

PRESET_RUNTIME_CONTEXT = (
    "РЕЖИМ КАМПАНИИ: готовое приключение. "
    "Следуй скрытой служебной сводке приключения и не ломай опорную структуру модуля без веской причины."
)
PRESET_BASED_RUNTIME_CONTEXT = (
    "РЕЖИМ КАМПАНИИ: свободная адаптация по мотивам готового приключения. "
    "Сохраняй сильные стороны оригинала, но развивай уже новую кампанию."
)


def _adventure_mode_system_prompt(campaign_mode: str) -> str:
    if campaign_mode == "preset_based":
        return READY_ADVENTURE_ADAPTATION_SYSTEM_PROMPT
    return READY_ADVENTURE_SYSTEM_PROMPT


def _adventure_document_system_block(adventure: dict) -> str:
    if _is_local_provider():
        source_text = adventure_library.build_local_adventure_context(adventure)
        return (
            f"{source_text}\n\n"
            "[ПРИМЕЧАНИЕ]\n"
            "Это компактная сводка источника для локальной модели. "
            "Держись структуры и тональности модуля, но не выдумывай детали, которых нет в сводке."
        )

    return (
        "[ПОЛНЫЙ ТЕКСТ ГОТОВОГО ПРИКЛЮЧЕНИЯ]\n"
        f"slug: {adventure['slug']}\n"
        f"title: {adventure['title']}\n\n"
        f"{adventure['content']}"
    )


def build_adventure_bootstrap_system_blocks(campaign_id: int, adventure: dict, campaign_mode: str) -> list[str]:
    return [
        *get_system_prompt_blocks(campaign_id, include_adventure_brief=False),
        _adventure_mode_system_prompt(campaign_mode),
        _adventure_document_system_block(adventure),
    ]


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _is_local_provider() -> bool:
    return ai_provider.get_provider() == "local"


def _strip_embedded_party_context(text: str) -> str:
    cleaned = EMBEDDED_PARTY_CONTEXT_RE.sub("", text or "").strip()
    return cleaned or (text or "").strip()


def _truncate_text(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return text


def _extract_json(text: str):
    cleaned = _strip_code_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def extract_roll_request(text: str) -> dict:
    match = ROLL_REQUEST_PATTERN.search(text or "")
    cleaned_text = ROLL_REQUEST_PATTERN.sub("", text or "").strip()
    fallback = {"text": cleaned_text, "roll_request": None}
    if not match:
        return fallback

    parsed = _extract_json(match.group(1))
    if not isinstance(parsed, dict):
        return fallback

    try:
        dice_count = max(1, min(int(parsed.get("dice_count", 1)), 20))
        dice_sides = max(2, min(int(parsed.get("dice_sides", 20)), 100))
    except (TypeError, ValueError):
        return fallback

    modifier_stat = parsed.get("modifier_stat")
    if modifier_stat is not None:
        modifier_stat = str(modifier_stat).strip().lower() or None
    if modifier_stat not in VALID_STATS:
        modifier_stat = None

    reason = str(parsed.get("reason") or "Нужна проверка исхода действия.").strip()
    if not reason:
        reason = "Нужна проверка исхода действия."
    target_name = parsed.get("target_name")
    if target_name is not None:
        target_name = str(target_name).strip() or None

    return {
        "text": cleaned_text,
        "roll_request": {
            "needs_roll": True,
            "target_name": target_name,
            "dice_count": dice_count,
            "dice_sides": dice_sides,
            "modifier_stat": modifier_stat,
            "reason": reason,
        },
    }


def _extract_numbered_options(text: str) -> list[str]:
    cleaned = re.sub(r"\*\*", "", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []

    matches = list(re.finditer(r"(?<!\w)(\d+)\s*[\.)]\s*", cleaned))
    if not matches:
        return []

    options = []
    for index, match in enumerate(matches):
        number = int(match.group(1))
        if number != index + 1:
            continue

        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        option = cleaned[start:end].strip(" -\n\t\r")
        option = re.sub(r"\s+", " ", option).strip(" .")
        if option:
            options.append(option)

    return options[:5]


def _coerce_out_of_scene(raw_out_of_scene, allowed_names: dict) -> list[dict]:
    result = []
    if not isinstance(raw_out_of_scene, list):
        return result

    for item in raw_out_of_scene:
        if isinstance(item, str):
            normalized = _normalize_name(item)
            if normalized in allowed_names:
                result.append({"name": allowed_names[normalized], "reason": "Персонаж находится вне текущей сцены."})
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            normalized = _normalize_name(name)
            if normalized in allowed_names:
                reason = str(item.get("reason", "")).strip() or "Персонаж находится вне текущей сцены."
                result.append({"name": allowed_names[normalized], "reason": reason})
    unique = {}
    for item in result:
        unique[_normalize_name(item["name"])] = item
    return list(unique.values())


def _campaign_roll_mode(campaign_id: int) -> str:
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        return "bot_auto"
    return campaign.get("roll_mode") or "bot_auto"


def _campaign_main_quest_pressure(campaign_id: int) -> str:
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        return "soft"
    return campaign.get("main_quest_pressure") or "soft"


def _campaign_mode(campaign_id: int) -> str:
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        return "generated"
    return campaign.get("campaign_mode") or "generated"


def _campaign_adventure_gm_brief(campaign_id: int) -> str | None:
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        return None
    brief = str(campaign.get("adventure_gm_brief") or "").strip()
    return brief or None


def _campaign_adventure(campaign_id: int) -> dict | None:
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        return None
    adventure_slug = str(campaign.get("adventure_slug") or "").strip()
    if not adventure_slug:
        return None
    return adventure_library.get_adventure(adventure_slug)


def _relevant_adventure_runtime_block(campaign_id: int, retrieval_query: str | None) -> str | None:
    if not _is_local_provider():
        return None
    if _campaign_mode(campaign_id) not in {"preset", "preset_based"}:
        return None
    if not retrieval_query:
        return None

    adventure = _campaign_adventure(campaign_id)
    if not adventure:
        return None

    block = adventure_library.build_relevant_local_adventure_context(adventure, retrieval_query)
    return block or None


def get_system_prompt_blocks(
    campaign_id: int,
    include_adventure_brief: bool = True,
    retrieval_query: str | None = None,
) -> list[str]:
    roll_mode = _campaign_roll_mode(campaign_id)
    quest_pressure = _campaign_main_quest_pressure(campaign_id)
    dynamic_context = "\n\n".join(
        [
            ROLL_MODE_CONTEXT.get(roll_mode, ROLL_MODE_CONTEXT["bot_auto"]),
            MAIN_QUEST_PRESSURE_CONTEXT.get(quest_pressure, MAIN_QUEST_PRESSURE_CONTEXT["soft"]),
        ]
    )
    base_system_prompt = (
        LOCAL_SYSTEM_PROMPT
        if _is_local_provider()
        else OPENAI_SYSTEM_PROMPT
        if ai_provider.get_provider() == "gpt"
        else CLAUDE_SYSTEM_PROMPT
    )
    system_blocks = [base_system_prompt, dynamic_context]

    campaign_mode = _campaign_mode(campaign_id)
    if campaign_mode == "preset":
        system_blocks.append(PRESET_RUNTIME_CONTEXT)
    elif campaign_mode == "preset_based":
        system_blocks.append(PRESET_BASED_RUNTIME_CONTEXT)

    if include_adventure_brief:
        adventure_gm_brief = _campaign_adventure_gm_brief(campaign_id)
        if adventure_gm_brief:
            system_blocks.append(f"[СЛУЖЕБНАЯ СВОДКА ПРИКЛЮЧЕНИЯ]\n{adventure_gm_brief}")
        relevant_source = _relevant_adventure_runtime_block(campaign_id, retrieval_query)
        if relevant_source:
            system_blocks.append(relevant_source)
    return system_blocks


def get_system_prompt(campaign_id: int, retrieval_query: str | None = None) -> str:
    return "\n\n---\n\n".join(get_system_prompt_blocks(campaign_id, retrieval_query=retrieval_query))


def is_role_break_attempt(text: str) -> bool:
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in BLOCKED_PHRASES)


def build_messages(campaign_id: int) -> list:
    history = db.get_history(campaign_id, limit=LOCAL_MESSAGE_LIMIT if _is_local_provider() else 60)
    party_context = compact_characters_context(campaign_id) if _is_local_provider() else characters_context(campaign_id)
    messages = [{"role": "user", "content": f"[Текущий состав партии]\n{party_context}"}]
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        content = _strip_embedded_party_context(msg["content"])
        if _is_local_provider():
            content = _truncate_text(content, LOCAL_MESSAGE_CHAR_LIMIT)
        messages.append({"role": role, "content": content})
    return messages


def characters_context(campaign_id: int) -> str:
    chars = db.get_all_characters(campaign_id)
    if not chars:
        return "Персонажи ещё не созданы."

    lines = ["АКТИВНЫЕ ПЕРСОНАЖИ В ПАРТИИ:"]
    for char in chars:
        inventory = ", ".join(char["inventory"]) if char["inventory"] else "пусто"
        lines.append(
            f"- {char['name']} ({game_data.get_character_archetype_text(char['race'], char['class'], subrace_key=char.get('subrace'), subclass_key=char.get('subclass'))}, ур.{char['level']}) | "
            f"HP: {char['hp']}/{char['max_hp']} | "
            f"СИЛ:{char['strength']} ЛОВ:{char['dexterity']} ТЕЛ:{char['constitution']} "
            f"ИНТ:{char['intelligence']} МДР:{char['wisdom']} ХАР:{char['charisma']} | "
            f"Золото: {char['gold']} | Инвентарь: {inventory}"
        )
    return "\n".join(lines)


def compact_characters_context(campaign_id: int) -> str:
    chars = db.get_all_characters(campaign_id)
    if not chars:
        return "Партия ещё не собрана."

    lines = ["ПАРТИЯ:"]
    for char in chars:
        lines.append(
            f"- {char['name']}: "
            f"{game_data.get_character_archetype_text(char['race'], char['class'], subrace_key=char.get('subrace'), subclass_key=char.get('subclass'))}, "
            f"ур.{char['level']}, HP {char['hp']}/{char['max_hp']}, золото {char['gold']}"
        )

    return _truncate_text("\n".join(lines), LOCAL_CHARACTERS_CONTEXT_CHAR_LIMIT)


def recent_history_summary(campaign_id: int) -> str:
    history = db.get_history(campaign_id, limit=LOCAL_HISTORY_SUMMARY_LIMIT if _is_local_provider() else 50)
    lines = []
    total_chars = 0
    char_limit = LOCAL_HISTORY_SUMMARY_CHAR_LIMIT if _is_local_provider() else 12000

    for msg in history:
        content = _strip_embedded_party_context(msg["content"])
        if _is_local_provider():
            content = _truncate_text(content, 260 if msg["role"] == "assistant" else 220)
        line = f"[{msg['username']}]: {content}" if msg["role"] == "user" else f"[Мастер]: {content}"
        if total_chars + len(line) > char_limit:
            break
        lines.append(line)
        total_chars += len(line) + 1

    return "\n".join(lines)


def analyze_scene_response(campaign_id: int, assistant_text: str) -> dict:
    extracted_options = _extract_numbered_options(assistant_text)
    chars = db.get_all_characters(campaign_id)
    allowed_names = {_normalize_name(char["name"]): char["name"] for char in chars}

    fallback = {
        "should_open_round": bool(extracted_options),
        "wait_mode": "all_alive_except_out_of_scene",
        "options": extracted_options,
        "out_of_scene_characters": [],
    }

    if not chars:
        return fallback

    if _is_local_provider():
        logger.info(
            "Using heuristic-only local round analysis. campaign_id=%s options=%s",
            campaign_id,
            len(extracted_options),
        )
        return fallback

    analysis_request = (
        f"Список живых персонажей: {', '.join(char['name'] for char in chars)}\n\n"
        f"Последнее сообщение ведущего:\n{assistant_text}\n\n"
        f"Недавний контекст сцены:\n{recent_history_summary(campaign_id)}"
    )

    try:
        raw = ai_provider.call_api(
            ANALYZER_PROMPT,
            [{"role": "user", "content": analysis_request}],
            max_tokens=700,
        )
    except RuntimeError:
        return fallback

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return fallback

    raw_options = parsed.get("options")
    options = [str(item).strip() for item in raw_options if str(item).strip()] if isinstance(raw_options, list) else []
    if not options:
        options = extracted_options

    out_of_scene = _coerce_out_of_scene(parsed.get("out_of_scene_characters"), allowed_names)
    should_open_round = bool(parsed.get("should_open_round")) if "should_open_round" in parsed else bool(options)
    should_open_round = should_open_round and bool(options)

    return {
        "should_open_round": should_open_round,
        "wait_mode": "all_alive_except_out_of_scene",
        "options": options[:5],
        "out_of_scene_characters": out_of_scene,
    }


def analyze_xp_award(campaign_id: int, assistant_text: str) -> dict:
    chars = db.get_all_characters(campaign_id)
    allowed_names = {_normalize_name(char["name"]): char["name"] for char in chars}
    fallback = {
        "award_xp": False,
        "amount": 0,
        "reason": "No major milestone worthy of XP.",
        "recipient_mode": "all_alive_except_out_of_scene",
        "excluded_characters": [],
    }
    if not chars:
        return fallback

    request = (
        f"Party characters: {', '.join(char['name'] for char in chars)}\n\n"
        f"Latest DM response:\n{assistant_text}\n\n"
        f"Recent campaign context:\n{recent_history_summary(campaign_id)}"
    )

    try:
        raw = ai_provider.call_api(
            XP_ANALYZER_PROMPT,
            [{"role": "user", "content": request}],
            max_tokens=350,
        )
    except RuntimeError:
        return fallback

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return fallback

    try:
        amount = max(0, int(parsed.get("amount", 0)))
    except (TypeError, ValueError):
        amount = 0
    excluded = _coerce_out_of_scene(parsed.get("excluded_characters"), allowed_names)
    if not parsed.get("award_xp") or amount <= 0:
        return fallback

    return {
        "award_xp": True,
        "amount": amount,
        "reason": str(parsed.get("reason") or fallback["reason"]).strip(),
        "recipient_mode": "all_alive_except_out_of_scene",
        "excluded_characters": excluded,
    }


def analyze_action_roll(campaign_id: int, user_id: str, username: str, action_text: str) -> dict:
    char = db.get_character(user_id, campaign_id)
    roll_mode = _campaign_roll_mode(campaign_id)
    char_name = char["name"] if char else username
    char_context = (
        f"{char['name']} | {game_data.get_character_archetype_text(char['race'], char['class'], subrace_key=char.get('subrace'), subclass_key=char.get('subclass'))} | "
        f"СИЛ:{char['strength']} ЛОВ:{char['dexterity']} ТЕЛ:{char['constitution']} "
        f"ИНТ:{char['intelligence']} МДР:{char['wisdom']} ХАР:{char['charisma']}"
        if char else "Персонаж не найден"
    )
    fallback = {
        "needs_roll": False,
        "dice_count": 1,
        "dice_sides": 20,
        "modifier_stat": None,
        "reason": "Бросок не требуется.",
    }

    request = (
        f"Режим бросков кампании: {roll_mode}\n"
        f"Игрок: {username}\n"
        f"Персонаж: {char_name}\n"
        f"Лист персонажа: {char_context}\n\n"
        f"Действие игрока:\n{action_text}\n\n"
        f"Недавний контекст:\n{recent_history_summary(campaign_id)}"
    )

    try:
        raw = ai_provider.call_api(
            ROLL_ANALYZER_PROMPT,
            [{"role": "user", "content": request}],
            max_tokens=400,
        )
    except RuntimeError:
        return fallback

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return fallback

    needs_roll = bool(parsed.get("needs_roll"))
    if not needs_roll:
        fallback["reason"] = str(parsed.get("reason") or fallback["reason"])
        return fallback

    try:
        dice_count = max(1, min(int(parsed.get("dice_count", 1)), 20))
        dice_sides = max(2, min(int(parsed.get("dice_sides", 20)), 100))
    except (TypeError, ValueError):
        return fallback

    modifier_stat = parsed.get("modifier_stat")
    if modifier_stat is not None:
        modifier_stat = str(modifier_stat).strip().lower() or None
    if modifier_stat not in VALID_STATS:
        modifier_stat = None

    return {
        "needs_roll": True,
        "dice_count": dice_count,
        "dice_sides": dice_sides,
        "modifier_stat": modifier_stat,
        "reason": str(parsed.get("reason") or "Нужна проверка исхода действия.").strip(),
    }


def _build_generated_start_user_message(campaign_id: int, title: str, intro_answers: dict | None = None) -> str:
    chars_ctx = compact_characters_context(campaign_id) if _is_local_provider() else characters_context(campaign_id)
    roll_mode = _campaign_roll_mode(campaign_id)
    intro_answers = intro_answers or {}
    intro_setup_lines = [
        f"Жанр: {intro_answers.get('genre', 'героическое приключенческое фэнтези')}.",
        f"Тональность: {intro_answers.get('tone', 'умеренно серьёзная с местами юмора')}.",
        f"Темы, которые хотим включить: {intro_answers.get('include_themes', 'исследования, приключения, тайны')}.",
        f"Темы/границы, которых избегаем: {intro_answers.get('exclude_themes', 'откровенная эротика и явно нежелательные темы')}.",
        f"Летальность и последствия: {intro_answers.get('lethality', 'умеренные последствия и традиционный риск D&D')}.",
        f"Режим правил: {intro_answers.get('rules_mode', 'правила в основе, но с упрощением')}.",
        f"Сеттинг/мир: {intro_answers.get('setting', 'оригинальный фэнтезийный мир')}.",
    ]
    return (
        f"Начинается новая кампания под названием «{title}».\n"
        f"Режим бросков кампании: {roll_mode}.\n\n"
        f"{chars_ctx}\n\n"
        "Подготовительные ответы партии уже собраны. Не задавай дополнительных организационных вопросов "
        "и не повторяй мастер настройки.\n"
        "Сразу начни игру: коротко зафиксируй выбранный стиль мира и открой первую сцену приключения in-character.\n\n"
        "Настройки партии:\n"
        + "\n".join(f"- {line}" for line in intro_setup_lines)
    )


def _build_adventure_start_user_message(campaign_id: int, title: str, adventure: dict, campaign_mode: str) -> str:
    chars_ctx = compact_characters_context(campaign_id) if _is_local_provider() else characters_context(campaign_id)
    roll_mode = _campaign_roll_mode(campaign_id)
    mode_instruction = (
        "Проведи выбранное приключение максимально близко к исходному модулю, сохраняя его структуру, секреты, напряжение и ключевые сцены."
        if campaign_mode == "preset"
        else "Открой новую кампанию по мотивам модуля: свободно адаптируй его темы, узлы, антагонистов и атмосферу под эту партию, не пересказывая модуль дословно."
    )
    return (
        f"Запускается кампания «{title}».\n"
        f"Выбранное приключение: {adventure['title']} ({adventure['slug']}).\n"
        f"Режим бросков кампании: {roll_mode}.\n\n"
        f"{chars_ctx}\n\n"
        f"{mode_instruction}\n"
        "Не задавай организационных вопросов и не пересказывай служебные инструкции.\n"
        "Сразу открой первую игровую сцену, дай игрокам контекст старта и предложи конкретные действия."
    )


def _build_adventure_gm_brief_user_message(title: str, adventure: dict, campaign_mode: str) -> str:
    mode_label = "faithful_preset" if campaign_mode == "preset" else "free_adaptation"
    return (
        f"{ADVENTURE_GM_BRIEF_PROMPT}\n\n"
        f"Название кампании: {title}\n"
        f"Режим: {mode_label}\n"
        f"Источник: {adventure['title']} ({adventure['slug']})"
    )


def _store_campaign_opening(campaign_id: int, user_msg: str, answer: str):
    db.add_message(campaign_id, "user", user_msg)
    db.add_message(campaign_id, "assistant", answer)


async def _prepare_adventure_gm_brief(campaign_id: int, title: str, campaign_mode: str, adventure: dict) -> str:
    system_blocks = build_adventure_bootstrap_system_blocks(campaign_id, adventure, campaign_mode)
    gm_brief_user_msg = _build_adventure_gm_brief_user_message(title, adventure, campaign_mode)

    logger.info(
        "Starting adventure bootstrap. campaign_id=%s title=%s mode=%s adventure=%s system_chars=%s local=%s",
        campaign_id,
        title,
        campaign_mode,
        adventure["slug"],
        sum(len(block) for block in system_blocks),
        _is_local_provider(),
    )
    gm_brief = await asyncio.to_thread(
        ai_provider.call_bootstrap_api,
        system_blocks,
        [{"role": "user", "content": gm_brief_user_msg}],
        1600,
    )
    db.set_campaign_adventure_gm_brief(campaign_id, gm_brief)
    return gm_brief


async def _start_generated_campaign(campaign_id: int, title: str, intro_answers: dict | None = None) -> str:
    user_msg = _build_generated_start_user_message(campaign_id, title, intro_answers)
    logger.info("Starting generated campaign intro. campaign_id=%s title=%s", campaign_id, title)
    answer = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id),
        [{"role": "user", "content": user_msg}],
        1800,
    )
    _store_campaign_opening(campaign_id, user_msg, answer)
    logger.info("Generated campaign intro completed. campaign_id=%s", campaign_id)
    return answer


async def _start_adventure_campaign(campaign_id: int, title: str, campaign_mode: str, adventure: dict) -> str:
    gm_brief = await _prepare_adventure_gm_brief(campaign_id, title, campaign_mode, adventure)
    user_msg = _build_adventure_start_user_message(campaign_id, title, adventure, campaign_mode)
    answer = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id),
        [{"role": "user", "content": user_msg}],
        1800,
    )
    _store_campaign_opening(campaign_id, user_msg, answer)
    logger.info(
        "Adventure bootstrap completed. campaign_id=%s mode=%s adventure=%s brief_chars=%s",
        campaign_id,
        campaign_mode,
        adventure["slug"],
        len(gm_brief),
    )
    return answer


async def _stream_campaign_opening(
    campaign_id: int,
    user_msg: str,
    operation: str,
    log_context: dict,
):
    db.add_message(campaign_id, "user", user_msg)
    messages = [{"role": "user", "content": user_msg}]
    async for event in _stream_response_events(
        campaign_id,
        messages,
        1800,
        operation=operation,
        log_context=log_context,
    ):
        if event["done"]:
            db.add_message(campaign_id, "assistant", event["text"])
        yield event


async def start_campaign(campaign_id: int, title: str, intro_answers: dict | None = None) -> str:
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise RuntimeError("Активная кампания не найдена.")

    campaign_mode = campaign.get("campaign_mode") or "generated"
    if campaign_mode == "generated":
        return await _start_generated_campaign(campaign_id, title, intro_answers)

    adventure_slug = campaign.get("adventure_slug")
    if not adventure_slug:
        raise RuntimeError("Для этой кампании ещё не выбрано готовое приключение.")

    adventure = adventure_library.get_adventure(adventure_slug)
    if not adventure:
        raise RuntimeError(
            f"Не удалось найти файл приключения `{adventure_slug}.md` в каталоге data/adventures."
        )

    return await _start_adventure_campaign(campaign_id, title, campaign_mode, adventure)


async def stream_campaign_start(campaign_id: int, title: str, intro_answers: dict | None = None):
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise RuntimeError("Активная кампания не найдена.")

    campaign_mode = campaign.get("campaign_mode") or "generated"
    if campaign_mode == "generated":
        logger.info("Starting generated campaign intro stream. campaign_id=%s title=%s", campaign_id, title)
        user_msg = _build_generated_start_user_message(campaign_id, title, intro_answers)
        async for event in _stream_campaign_opening(
            campaign_id,
            user_msg,
            operation="campaign_intro",
            log_context={"mode": campaign_mode},
        ):
            if event["done"]:
                logger.info("Generated campaign intro completed. campaign_id=%s", campaign_id)
            yield event
        return

    adventure_slug = campaign.get("adventure_slug")
    if not adventure_slug:
        raise RuntimeError("Для этой кампании ещё не выбрано готовое приключение.")

    adventure = adventure_library.get_adventure(adventure_slug)
    if not adventure:
        raise RuntimeError(
            f"Не удалось найти файл приключения `{adventure_slug}.md` в каталоге data/adventures."
        )

    gm_brief = await _prepare_adventure_gm_brief(campaign_id, title, campaign_mode, adventure)
    user_msg = _build_adventure_start_user_message(campaign_id, title, adventure, campaign_mode)
    async for event in _stream_campaign_opening(
        campaign_id,
        user_msg,
        operation="campaign_intro",
        log_context={"mode": campaign_mode, "adventure": adventure["slug"]},
    ):
        if event["done"]:
            logger.info(
                "Adventure bootstrap completed. campaign_id=%s mode=%s adventure=%s brief_chars=%s",
                campaign_id,
                campaign_mode,
                adventure["slug"],
                len(gm_brief),
            )
        yield event


def _action_user_content(campaign_id: int, user_id: str, username: str, action: str) -> str:
    char = db.get_character(user_id, campaign_id)
    if char:
        return f"{char['name']} (игрок {username}) делает: {action}"
    return f"{username} (без персонажа) говорит/делает: {action}"


def _build_scene_round_summary_message(campaign_id: int, round_data: dict) -> str:
    responses_by_user = {response["user_id"]: response for response in round_data["responses"]}
    options_text = "\n".join(
        f"{index}. {option}" for index, option in enumerate(round_data["options"], start=1)
    )

    round_lines = [
        "СЦЕНОВЫЙ РАУНД ПАРТИИ. Разреши все действия одновременно и сводно.",
        "Если часть героев промолчала по таймауту - считай, что они медлят и не вмешиваются.",
        "Если персонажи вне сцены - упоминай, что их действия сейчас не влияют на эпизод.",
        "В конце ответа снова предложи 5 вариантов действий, если сцена продолжается.",
        "",
        "Текущие варианты действий:",
        options_text or "(варианты не были распознаны, ориентируйся на свободные ответы игроков)",
        "",
        "Ответы и статусы персонажей:",
    ]

    for target in round_data["targets"]:
        user_id = target["user_id"]
        if target["status"] == "answered":
            response = responses_by_user.get(user_id)
            if response:
                if response["response_kind"] == "option" and response.get("selected_option"):
                    line = (
                        f"- {target['character_name_snapshot']} выбрал вариант {response['selected_option']}: "
                        f"{response['content']}"
                    )
                else:
                    line = f"- {target['character_name_snapshot']} действует свободно: {response['content']}"
            else:
                line = f"- {target['character_name_snapshot']} совершает действие, но текст ответа утерян."
        elif target["status"] == "timed_out":
            line = f"- {target['character_name_snapshot']} молчит и не вмешивается."
        elif target["status"] == "out_of_scene":
            reason = target.get("reason") or "Персонаж находится вне текущей сцены."
            line = f"- {target['character_name_snapshot']} вне сцены: {reason}"
        else:
            line = f"- {target['character_name_snapshot']} не определён в текущей сцене."
        round_lines.append(line)

    round_context = compact_characters_context(campaign_id) if _is_local_provider() else characters_context(campaign_id)
    return f"[{round_context}]\n\n" + "\n".join(round_lines)


async def _stream_response_events(
    campaign_id: int,
    messages: list,
    max_tokens: int,
    operation: str,
    log_context: dict,
    retrieval_query: str | None = None,
):
    if not ai_provider.supports_streaming():
        answer = await asyncio.to_thread(
            ai_provider.call_api,
            get_system_prompt(campaign_id, retrieval_query=retrieval_query),
            messages,
            max_tokens,
        )
        yield {"text": answer, "done": True, "chunk_count": 1, "chars": len(answer)}
        return

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    system_prompt = get_system_prompt(campaign_id, retrieval_query=retrieval_query)
    started_at = time.perf_counter()
    context_items = " ".join(f"{key}={value}" for key, value in sorted(log_context.items()))

    logger.info(
        "Local stream started. operation=%s campaign_id=%s max_tokens=%s %s",
        operation,
        campaign_id,
        max_tokens,
        context_items,
    )

    def worker():
        chunks = []
        chunk_count = 0
        first_chunk_ms = None

        try:
            for chunk in ai_provider.stream_api(system_prompt, messages, max_tokens):
                if not chunk:
                    continue
                chunks.append(chunk)
                chunk_count += 1
                if first_chunk_ms is None:
                    first_chunk_ms = int((time.perf_counter() - started_at) * 1000)
                    logger.info(
                        "Local stream first chunk. operation=%s campaign_id=%s first_chunk_ms=%s %s",
                        operation,
                        campaign_id,
                        first_chunk_ms,
                        context_items,
                    )
                snapshot = "".join(chunks)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "kind": "chunk",
                        "text": snapshot,
                        "chunk_count": chunk_count,
                        "first_chunk_ms": first_chunk_ms,
                    },
                )

            final_text = "".join(chunks).strip()
            if not final_text:
                raise RuntimeError("Локальная модель не вернула текстовый ответ.")

            loop.call_soon_threadsafe(
                queue.put_nowait,
                {
                    "kind": "done",
                    "text": final_text,
                    "chunk_count": chunk_count,
                    "first_chunk_ms": first_chunk_ms,
                    "total_ms": int((time.perf_counter() - started_at) * 1000),
                    "chars": len(final_text),
                },
            )
        except Exception as error:
            loop.call_soon_threadsafe(queue.put_nowait, {"kind": "error", "error": error})

    threading.Thread(target=worker, daemon=True).start()

    while True:
        event = await queue.get()
        kind = event["kind"]
        if kind == "chunk":
            yield {
                "text": event["text"],
                "done": False,
                "chunk_count": event["chunk_count"],
                "first_chunk_ms": event["first_chunk_ms"],
            }
            continue
        if kind == "done":
            logger.info(
                "Local stream completed. operation=%s campaign_id=%s first_chunk_ms=%s total_ms=%s chars=%s chunks=%s %s",
                operation,
                campaign_id,
                event["first_chunk_ms"] or 0,
                event["total_ms"],
                event["chars"],
                event["chunk_count"],
                context_items,
            )
            yield {
                "text": event["text"],
                "done": True,
                "chunk_count": event["chunk_count"],
                "first_chunk_ms": event["first_chunk_ms"],
                "total_ms": event["total_ms"],
                "chars": event["chars"],
            }
            return
        raise event["error"]


async def process_action(campaign_id: int, user_id: str, username: str, action: str) -> str:
    if is_role_break_attempt(action):
        return (
            "🌫️ *Таинственная сила поглощает слова героя... Голоса из ниоткуда "
            "растворяются в воздухе. Мастер лишь загадочно усмехается.* "
            "Что ты на самом деле делаешь?"
        )

    user_content = _action_user_content(campaign_id, user_id, username, action)

    db.add_message(campaign_id, "user", user_content, user_id, username)

    messages = build_messages(campaign_id)
    retrieval_query = f"{action}\n\n{recent_history_summary(campaign_id)}"
    logger.info("Processing DM action. campaign_id=%s user_id=%s", campaign_id, user_id)
    answer = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id, retrieval_query=retrieval_query),
        messages,
        2200,
    )
    db.add_message(campaign_id, "assistant", answer)
    logger.info("DM action processed. campaign_id=%s user_id=%s", campaign_id, user_id)
    return answer


async def stream_action(campaign_id: int, user_id: str, username: str, action: str):
    if is_role_break_attempt(action):
        yield {
            "text": (
                "🌫️ *Таинственная сила поглощает слова героя... Голоса из ниоткуда "
                "растворяются в воздухе. Мастер лишь загадочно усмехается.* "
                "Что ты на самом деле делаешь?"
            ),
            "done": True,
            "chunk_count": 0,
            "chars": 0,
        }
        return

    user_content = _action_user_content(campaign_id, user_id, username, action)
    db.add_message(campaign_id, "user", user_content, user_id, username)

    messages = build_messages(campaign_id)
    retrieval_query = f"{action}\n\n{recent_history_summary(campaign_id)}"
    async for event in _stream_response_events(
        campaign_id,
        messages,
        2200,
        operation="action",
        log_context={"user_id": user_id},
        retrieval_query=retrieval_query,
    ):
        if event["done"]:
            db.add_message(campaign_id, "assistant", event["text"])
            logger.info("DM action processed. campaign_id=%s user_id=%s", campaign_id, user_id)
        yield event


async def process_roll(
    campaign_id: int,
    user_id: str,
    username: str,
    roll_result: int,
    roll_type: str,
) -> str:
    char = db.get_character(user_id, campaign_id)
    name = char["name"] if char else username

    user_content = (
        f"(бросок {roll_type}): {name} бросает кубик - выпадает **{roll_result}**"
    )
    db.add_message(campaign_id, "user", user_content, user_id, username)

    messages = build_messages(campaign_id)
    retrieval_query = f"{user_content}\n\n{recent_history_summary(campaign_id)}"
    logger.info(
        "Processing roll narration. campaign_id=%s user_id=%s roll_type=%s",
        campaign_id,
        user_id,
        roll_type,
    )
    answer = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id, retrieval_query=retrieval_query),
        messages,
        1400,
    )
    db.add_message(campaign_id, "assistant", answer)
    logger.info("Roll narration processed. campaign_id=%s user_id=%s", campaign_id, user_id)
    return answer


async def process_scene_round(campaign_id: int, round_id: int) -> str:
    round_data = db.get_scene_round(round_id)
    if not round_data:
        raise RuntimeError("Активный раунд сцены не найден.")
    summary_message = _build_scene_round_summary_message(campaign_id, round_data)
    db.add_message(campaign_id, "user", summary_message)

    messages = build_messages(campaign_id)
    retrieval_query = f"{summary_message}\n\n{recent_history_summary(campaign_id)}"
    logger.info("Processing scene round. campaign_id=%s round_id=%s", campaign_id, round_id)
    answer = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id, retrieval_query=retrieval_query),
        messages,
        2200,
    )
    db.add_message(campaign_id, "assistant", answer)
    logger.info("Scene round processed. campaign_id=%s round_id=%s", campaign_id, round_id)
    return answer


async def stream_scene_round(campaign_id: int, round_id: int):
    round_data = db.get_scene_round(round_id)
    if not round_data:
        raise RuntimeError("Активный раунд сцены не найден.")

    summary_message = _build_scene_round_summary_message(campaign_id, round_data)
    db.add_message(campaign_id, "user", summary_message)

    messages = build_messages(campaign_id)
    retrieval_query = f"{summary_message}\n\n{recent_history_summary(campaign_id)}"
    async for event in _stream_response_events(
        campaign_id,
        messages,
        2200,
        operation="scene_round",
        log_context={"round_id": round_id},
        retrieval_query=retrieval_query,
    ):
        if event["done"]:
            db.add_message(campaign_id, "assistant", event["text"])
            logger.info("Scene round processed. campaign_id=%s round_id=%s", campaign_id, round_id)
        yield event


async def get_summary(campaign_id: int) -> str:
    history_text = recent_history_summary(campaign_id)
    chars_ctx = compact_characters_context(campaign_id) if _is_local_provider() else characters_context(campaign_id)

    summary_request = (
        "<лист персонажа> Составь краткую хронику (5-8 предложений): "
        "что произошло, где сейчас находится партия и какие задачи стоят "
        "перед героями.\n\n"
        f"{chars_ctx}\n\nИСТОРИЯ:\n{history_text}"
    )

    logger.info("Generating campaign summary. campaign_id=%s", campaign_id)
    summary = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id),
        [{"role": "user", "content": summary_request}],
        1200,
    )
    logger.info("Campaign summary generated. campaign_id=%s", campaign_id)
    return summary
