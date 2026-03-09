import json
import re

import ai_provider_runtime as ai_provider
import database as db
import game_data

from pathlib import Path
import asyncio
import logging

PROMPTS_DIR = Path(__file__).resolve().parent / "data" / "prompts"
logger = logging.getLogger("dnd_bot.dm")


def _load_prompt(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()

CLAUDE_SYSTEM_PROMPT = _load_prompt("claude_system_prompt_ru_v_1.md")
OPENAI_SYSTEM_PROMPT = _load_prompt("openai_system_prompt_ru_v_1.md")

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


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


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
    options = []
    for match in re.finditer(r"^\s*(\d+)[\.)]\s+(.+)$", text, flags=re.MULTILINE):
        option = match.group(2).strip()
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


def get_system_prompt(campaign_id: int) -> str:
    roll_mode = _campaign_roll_mode(campaign_id)
    quest_pressure = _campaign_main_quest_pressure(campaign_id)
    dynamic_context = "\n\n".join(
        [
            ROLL_MODE_CONTEXT.get(roll_mode, ROLL_MODE_CONTEXT["bot_auto"]),
            MAIN_QUEST_PRESSURE_CONTEXT.get(quest_pressure, MAIN_QUEST_PRESSURE_CONTEXT["soft"]),
        ]
    )
    system_prompt = (
        OPENAI_SYSTEM_PROMPT
        if ai_provider.get_provider() in {"gpt", "local"}
        else CLAUDE_SYSTEM_PROMPT
    )
    return f"{system_prompt}\n\n---\n\n{dynamic_context}"


def is_role_break_attempt(text: str) -> bool:
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in BLOCKED_PHRASES)


def build_messages(campaign_id: int) -> list:
    history = db.get_history(campaign_id, limit=60)
    messages = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
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

    analysis_request = (
        f"Список живых персонажей: {', '.join(char['name'] for char in chars)}\n\n"
        f"Последнее сообщение ведущего:\n{assistant_text}\n\n"
        f"Недавний контекст сцены:\n{db.get_history_summary(campaign_id)}"
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
        f"Recent campaign context:\n{db.get_history_summary(campaign_id)}"
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
        f"Недавний контекст:\n{db.get_history_summary(campaign_id)}"
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


async def start_campaign(campaign_id: int, title: str, intro_answers: dict | None = None) -> str:
    chars_ctx = characters_context(campaign_id)
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
    user_msg = (
        f"Начинается новая кампания под названием «{title}».\n"
        f"Режим бросков кампании: {roll_mode}.\n\n"
        f"{chars_ctx}\n\n"
        "Подготовительные ответы партии уже собраны. Не задавай дополнительных организационных вопросов "
        "и не повторяй мастер настройки.\n"
        "Сразу начни игру: коротко зафиксируй выбранный стиль мира и открой первую сцену приключения in-character.\n\n"
        "Настройки партии:\n"
        + "\n".join(f"- {line}" for line in intro_setup_lines)
    )

    logger.info("Starting campaign intro generation. campaign_id=%s title=%s", campaign_id, title)
    answer = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id),
        [{"role": "user", "content": user_msg}],
        1800,
    )
    db.add_message(campaign_id, "user", user_msg)
    db.add_message(campaign_id, "assistant", answer)
    logger.info("Campaign intro generated. campaign_id=%s", campaign_id)
    return answer


async def process_action(campaign_id: int, user_id: str, username: str, action: str) -> str:
    if is_role_break_attempt(action):
        return (
            "🌫️ *Таинственная сила поглощает слова героя... Голоса из ниоткуда "
            "растворяются в воздухе. Мастер лишь загадочно усмехается.* "
            "Что ты на самом деле делаешь?"
        )

    char = db.get_character(user_id, campaign_id)
    chars_ctx = characters_context(campaign_id)

    if char:
        user_content = (
            f"[{chars_ctx}]\n\n"
            f"{char['name']} (игрок {username}) делает: {action}"
        )
    else:
        user_content = (
            f"[{chars_ctx}]\n\n"
            f"{username} (без персонажа) говорит/делает: {action}"
        )

    db.add_message(campaign_id, "user", user_content, user_id, username)

    messages = build_messages(campaign_id)
    logger.info("Processing DM action. campaign_id=%s user_id=%s", campaign_id, user_id)
    answer = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id),
        messages,
        2200,
    )
    db.add_message(campaign_id, "assistant", answer)
    logger.info("DM action processed. campaign_id=%s user_id=%s", campaign_id, user_id)
    return answer


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
    logger.info(
        "Processing roll narration. campaign_id=%s user_id=%s roll_type=%s",
        campaign_id,
        user_id,
        roll_type,
    )
    answer = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id),
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

    summary_message = f"[{characters_context(campaign_id)}]\n\n" + "\n".join(round_lines)
    db.add_message(campaign_id, "user", summary_message)

    messages = build_messages(campaign_id)
    logger.info("Processing scene round. campaign_id=%s round_id=%s", campaign_id, round_id)
    answer = await asyncio.to_thread(
        ai_provider.call_api,
        get_system_prompt(campaign_id),
        messages,
        2200,
    )
    db.add_message(campaign_id, "assistant", answer)
    logger.info("Scene round processed. campaign_id=%s round_id=%s", campaign_id, round_id)
    return answer


async def get_summary(campaign_id: int) -> str:
    history_text = db.get_history_summary(campaign_id)
    chars_ctx = characters_context(campaign_id)

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


