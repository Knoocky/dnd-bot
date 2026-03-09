import json
import asyncio
import logging
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord.ext import commands

import ai_provider_runtime as ai_provider
import database as db
import game_data
import leveling

logger = logging.getLogger("dnd_bot.character")

_ASI_ALLOWED_PATTERNS = {(2, 1), (1, 1, 1)}
_CREATION_TIMEOUT = timedelta(minutes=15)


def _display_error(error: Exception) -> str:
    return ai_provider.normalize_user_facing_text(str(error))
_creation_sessions: dict[int, dict] = {}

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "data" / "prompts"
_CHARACTER_GENERATOR_PROMPT = (_PROMPTS_DIR / "character_generator_prompt_ru_v_1.md").read_text(
    encoding="utf-8"
).strip()

_MODE_CHOICES = {
    "1": "manual",
    "ручной": "manual",
    "вручную": "manual",
    "manual": "manual",
    "2": "ai_random",
    "случайный": "ai_random",
    "рандом": "ai_random",
    "random": "ai_random",
    "ai_random": "ai_random",
    "3": "ai_concept",
    "концепт": "ai_concept",
    "идея": "ai_concept",
    "concept": "ai_concept",
    "ai_concept": "ai_concept",
}
_REVIEW_ACTIONS = {
    "подтвердить": "confirm",
    "создать": "confirm",
    "принять": "confirm",
    "accept": "confirm",
    "заново": "reroll",
    "перегенерировать": "reroll",
    "reroll": "reroll",
    "ручной": "manual",
    "вручную": "manual",
    "manual": "manual",
}


def roll_stat() -> int:
    rolls = [random.randint(1, 6) for _ in range(4)]
    return sum(sorted(rolls)[1:])


def generate_stats() -> dict[str, int]:
    return {stat["key"]: roll_stat() for stat in game_data.get_stats()}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned


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


class CharacterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def has_active_creation_session(self, channel_id: str, user_id: str | None = None) -> bool:
        campaign = db.get_active_campaign(channel_id)
        if not campaign:
            return False
        session = _creation_sessions.get(campaign["id"])
        if not session:
            return False
        if user_id is None:
            return True
        return str(session.get("user_id")) == str(user_id)

    @commands.command(name="создать_персонажа", aliases=["новый_перс", "сп", "нп"])
    async def create_character(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ В этом канале нет активной кампании. Используй `!новая_кампания`.")
            return

        user_id = str(ctx.author.id)
        logger.info("Character creation started. campaign_id=%s user_id=%s", campaign["id"], user_id)
        existing = db.get_character(user_id, campaign["id"])
        if existing:
            await ctx.send(
                f"⚠️ У тебя уже есть персонаж **{existing['name']}** в этой кампании. "
                "Используй `!персонаж` или `!лп`, чтобы посмотреть лист."
            )
            return

        expired = self._expire_stale_session(campaign["id"])
        session = _creation_sessions.get(campaign["id"])

        if session:
            if session["user_id"] != user_id:
                await ctx.send(
                    f"⏳ Сейчас персонажа создаёт <@{session['user_id']}>. "
                    "Дождись завершения, `!отмена_создания` владельцем или таймаута 15 минут."
                )
                return

            self._touch_session(session)
            await self._send_progress_update(
                ctx,
                session,
                note=None if expired else "Продолжаем текущее создание персонажа.",
            )
            await self._send_prompt_for_step(ctx, session)
            return

        session = {
            "campaign_id": campaign["id"],
            "channel_id": str(ctx.channel.id),
            "user_id": user_id,
            "started_at": _utcnow(),
            "last_activity_at": _utcnow(),
            "current_step": "mode_select",
            "mode": None,
            "draft": {},
            "ai_mode": None,
            "ai_concept": None,
            "ai_draft": None,
        }
        _creation_sessions[campaign["id"]] = session
        logger.info("Character creation session created. campaign_id=%s user_id=%s", campaign["id"], user_id)
        await self._send_progress_update(
            ctx,
            session,
            note=(
                "Создание персонажа запущено. Пока ты не завершишь мастер, другие игроки этого канала "
                "начать создание не смогут."
            ),
        )
        await self._send_prompt_for_step(ctx, session)

    @commands.command(name="подтвердить_персонажа")
    async def confirm_ai_character(self, ctx):
        session, campaign = await self._require_owned_session(ctx)
        if not session:
            return
        if self._get_session_step(session) != "ai_review" or not session.get("ai_draft"):
            await ctx.send("❌ Сейчас нет AI-черновика, который можно подтвердить.")
            return
        await self._finalize_session_draft(ctx, campaign, session, session["ai_draft"])

    @commands.command(name="перегенерировать")
    async def regenerate_ai_character(self, ctx):
        session, campaign = await self._require_owned_session(ctx)
        if not session:
            return
        if session.get("ai_mode") not in {"ai_random", "ai_concept"}:
            await ctx.send("❌ Перегенерация доступна только для AI-режимов создания.")
            return
        if session["ai_mode"] == "ai_concept" and not session.get("ai_concept"):
            await ctx.send("❌ Сначала пришли концепт персонажа обычным сообщением в чат.")
            return
        await self._generate_and_show_ai_draft(
            ctx,
            campaign,
            session,
            concept=session.get("ai_concept"),
        )

    @commands.command(name="отмена_создания", aliases=["ос"])
    async def cancel_character_creation(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        self._expire_stale_session(campaign["id"])
        session = _creation_sessions.get(campaign["id"])
        if not session:
            await ctx.send("ℹ️ Сейчас в этом канале нет активного создания персонажа.")
            return

        user_id = str(ctx.author.id)
        if session["user_id"] != user_id:
            await ctx.send(
                f"❌ Сейчас создание ведёт <@{session['user_id']}>. Отменить его может только владелец этой сессии."
            )
            return

        _creation_sessions.pop(campaign["id"], None)
        await ctx.send("🧹 Черновик создания удалён. Канал снова свободен для нового персонажа.")

    @commands.command(name="раса", aliases=["р"])
    async def choose_race(self, ctx, *, race: str):
        session, _ = await self._require_owned_session(ctx)
        if not session:
            return
        if not await self._ensure_manual_mode(ctx, session):
            return

        race_data = game_data.find_race(race)
        if not race_data:
            await ctx.send(f"❌ Неизвестная раса. Доступны: {self._format_labels(game_data.get_races())}")
            return

        draft = session["draft"]
        draft["race"] = race_data["key"]
        draft.pop("subrace", None)
        draft.pop("class", None)
        draft.pop("base_stats", None)
        draft.pop("origin_asi", None)
        self._touch_session(session)

        note = "Раса выбрана. Подраса, класс, ASI и имя ниже по цепочке сброшены и будут выбраны заново."
        if not race_data.get("subraces"):
            note = "Раса выбрана. У этой расы нет подтверждённых подрас, поэтому мастер сразу переходит к выбору класса."

        await self._send_progress_update(ctx, session, note=note)
        await self._send_prompt_for_step(ctx, session)

    @commands.command(name="подраса", aliases=["пр"])
    async def choose_subrace(self, ctx, *, subrace: str):
        session, _ = await self._require_owned_session(ctx)
        if not session:
            return
        if not await self._ensure_manual_mode(ctx, session):
            return

        draft = session["draft"]
        if "race" not in draft:
            await ctx.send("❌ Сначала выбери расу: `!раса <название>`.")
            return

        race_data = game_data.find_race(draft["race"])
        if not race_data or not race_data.get("subraces"):
            await ctx.send("❌ У выбранной расы нет подрас. Следующий обязательный шаг: `!класс <название>`.")
            return

        subrace_data = game_data.find_subrace(race_data["key"], subrace)
        if not subrace_data:
            await ctx.send(
                f"❌ Неизвестная подраса для **{race_data['label']}**. "
                f"Доступны: {self._format_labels(race_data['subraces'])}"
            )
            return

        draft["subrace"] = subrace_data["key"]
        draft.pop("class", None)
        draft.pop("base_stats", None)
        draft.pop("origin_asi", None)
        self._touch_session(session)

        await self._send_progress_update(
            ctx,
            session,
            note="Подраса обновлена. Класс, ASI и имя сброшены и будут выбраны заново.",
        )
        await self._send_prompt_for_step(ctx, session)

    @commands.command(name="класс", aliases=["кл", "к"])
    async def choose_class(self, ctx, *, cls: str):
        session, campaign = await self._require_owned_session(ctx)
        if not session:
            return
        if not await self._ensure_manual_mode(ctx, session):
            return

        draft = session["draft"]
        if "race" not in draft:
            await ctx.send("❌ Сначала выбери расу: `!раса <название>`.")
            return
        if self._race_requires_subrace(draft) and not draft.get("subrace"):
            await ctx.send("❌ Сначала выбери подрасу: `!подраса <название>`.")
            return

        class_data = game_data.find_class(cls)
        available_classes = leveling.get_available_classes(campaign.get("ruleset", "dnd_2024_phb"))
        if not class_data:
            await ctx.send(f"❌ Неизвестный класс. Доступны: {self._format_labels(available_classes)}")
            return
        if not leveling.is_supported_class_for_campaign(class_data["key"], campaign.get("ruleset", "dnd_2024_phb")):
            await ctx.send(
                "❌ В кампании по правилам D&D 2024 этот класс сейчас не поддерживается для создания и повышения уровня."
            )
            return

        draft["class"] = class_data["key"]
        draft["base_stats"] = generate_stats()
        draft.pop("origin_asi", None)
        self._touch_session(session)

        await self._send_progress_update(
            ctx,
            session,
            note="Класс выбран. Базовые характеристики сгенерированы заново; теперь нужно задать ASI происхождения и имя.",
        )
        await self._send_prompt_for_step(ctx, session)

    @commands.command(name="бонусы", aliases=["аси", "бн"])
    async def choose_origin_asi(self, ctx, *, bonuses: str):
        session, _ = await self._require_owned_session(ctx)
        if not session:
            return
        if not await self._ensure_manual_mode(ctx, session):
            return

        draft = session["draft"]
        if "class" not in draft or "base_stats" not in draft:
            await ctx.send("❌ Сначала выбери класс: `!класс <название>`.")
            return

        try:
            parsed_asi = self._parse_asi_input(bonuses)
            self._validate_asi_pattern(parsed_asi, draft["base_stats"])
        except ValueError as error:
            await ctx.send(_display_error(error))
            return

        draft["origin_asi"] = parsed_asi
        self._touch_session(session)

        await self._send_progress_update(
            ctx,
            session,
            note="ASI происхождения сохранены. Остался последний обязательный шаг: имя персонажа.",
        )
        await self._send_prompt_for_step(ctx, session)

    @commands.command(name="имя", aliases=["им", "и"])
    async def choose_name(self, ctx, *, name: str):
        session, campaign = await self._require_owned_session(ctx)
        if not session:
            return

        cleaned_name = name.strip()[:30]
        if not cleaned_name:
            await ctx.send("❌ Имя персонажа не должно быть пустым.")
            return

        if self._get_session_step(session) == "ai_review" and session.get("ai_draft"):
            session["ai_draft"]["name"] = cleaned_name
            self._touch_session(session)
            await ctx.send(f"✏️ Имя AI-черновика обновлено: **{cleaned_name}**.")
            await self._send_ai_review_prompt(ctx, session["ai_draft"])
            return

        if not await self._ensure_manual_mode(ctx, session):
            return

        draft = session["draft"]
        if self._get_manual_next_step(draft) != "name":
            next_step = self._get_manual_next_step(draft)
            await ctx.send(f"❌ Сначала заверши обязательный шаг: **{self._step_label(next_step)}**.")
            await self._send_prompt_for_step(ctx, session)
            return

        draft["name"] = cleaned_name
        await self._finalize_session_draft(ctx, campaign, session, draft)

    @commands.command(name="подкласс", aliases=["пк"])
    async def choose_subclass(self, ctx, *, subclass: str):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"])
        if not char:
            await ctx.send("❌ У тебя нет персонажа. Используй `!создать_персонажа` или `!сп`.")
            return

        char = await self._ensure_character_build(campaign, char)
        class_data = game_data.find_class(char["class"])
        if not class_data:
            await ctx.send("❌ Класс персонажа не найден в текущем справочнике.")
            return

        if char.get("subclass"):
            await ctx.send(
                f"❌ У персонажа уже выбран подкласс: **{game_data.get_subclass_label(char['class'], char['subclass'])}**."
            )
            return

        required_level = class_data.get("subclass_level", 3)
        if int(char.get("level", 1)) < required_level:
            await ctx.send(
                f"❌ Подкласс для **{class_data['label']}** выбирается только с {required_level} уровня. "
                f"Сейчас у тебя уровень {char['level']}."
            )
            return

        leveling_cog = self.bot.get_cog("LevelingCog")
        if not leveling_cog:
            await ctx.send("❌ Мастер повышения уровня сейчас недоступен.")
            return

        session = db.get_active_levelup_session(campaign["id"])
        if not session or session.get("user_id") != str(ctx.author.id):
            await ctx.send(
                "ℹ️ Выбор подкласса теперь проходит внутри мастера повышения уровня. Используй `!левелап`, а затем выбери вариант через `!выбрать <название>` или этой же командой."
            )
            return

        steps = session.get("state", {}).get("steps", [])
        step_index = int(session.get("state", {}).get("step_index", 0))
        if step_index >= len(steps) or steps[step_index].get("type") != "subclass":
            await ctx.send(
                "ℹ️ Сейчас активный мастер левелапа находится на другом шаге. Используй `!выбрать <опция>` для текущего шага или `!левелап`, чтобы увидеть состояние."
            )
            return

        try:
            await leveling_cog._apply_step_choice(ctx, campaign, char, session, steps[step_index], subclass)
        except ValueError as error:
            await ctx.send(_display_error(error))
            return

        session = db.get_active_levelup_session(campaign["id"])
        if session:
            updated_char = db.get_character(str(ctx.author.id), campaign["id"])
            await leveling_cog._send_levelup_prompt(ctx, campaign, updated_char, session)

    @commands.command(name="персонаж", aliases=["перс", "лп", "пс"])
    async def show_character(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"])
        if not char:
            await ctx.send("❌ У тебя нет персонажа. Используй `!создать_персонажа` или `!сп`.")
            return

        char = await self._ensure_character_build(campaign, char)
        progress = leveling.get_character_progress(char)
        queue_entry = db.get_user_levelup_queue_entry(campaign["id"], str(ctx.author.id))
        inventory = ", ".join(char["inventory"]) if char["inventory"] else "пусто"
        hp_bar = _hp_bar(char["hp"], char["max_hp"])
        archetype = game_data.get_character_archetype_text(
            char["race"],
            char["class"],
            subrace_key=char.get("subrace"),
            subclass_key=char.get("subclass"),
        )
        next_xp = progress["next"] if progress["next"] is not None else "максимум"

        embed = discord.Embed(
            title=f"📜 {char['name']}",
            description=f"*{archetype}, уровень {char['level']}*",
            color=0xFFD700,
        )
        embed.add_field(name=f"❤️ HP {hp_bar}", value=f"{char['hp']}/{char['max_hp']}", inline=True)
        embed.add_field(name="💰 Золото", value=str(char["gold"]), inline=True)
        embed.add_field(name="⭐ Опыт", value=f"{char['exp']} / {next_xp}", inline=True)
        embed.add_field(name="⬆️ Статус", value=self._levelup_status_label(progress, queue_entry), inline=True)
        embed.add_field(name="🎯 Следующий порог", value=str(next_xp), inline=True)
        embed.add_field(name="📈 Осталось XP", value=str(progress["remaining"]), inline=True)
        stats_text = "\n".join(
            f"{_stat_emoji(stat['key'])} {game_data.get_stat_label(stat['key'])}: **{char[stat['key']]}**"
            for stat in game_data.get_stats()
        )
        embed.add_field(name="📊 Характеристики", value=stats_text, inline=False)
        if char.get("origin_asi"):
            embed.add_field(name="🧬 ASI", value=self._format_origin_asi(char["origin_asi"]), inline=False)
        if char.get("feats"):
            feats_text = ", ".join(item.get("label", item.get("key", "feat")) for item in char["feats"][-4:])
            embed.add_field(name="🏅 Фиты", value=feats_text, inline=False)
        embed.add_field(name="🎒 Инвентарь", value=inventory, inline=False)
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        if self._looks_like_command(message.content):
            return

        campaign = db.get_active_campaign(str(message.channel.id))
        if not campaign:
            return

        self._expire_stale_session(campaign["id"])
        session = _creation_sessions.get(campaign["id"])
        if not session or session["user_id"] != str(message.author.id):
            return
        if db.get_character(str(message.author.id), campaign["id"]):
            return

        step = self._get_session_step(session)
        if step == "mode_select":
            await self._handle_mode_selection_message(message, campaign, session)
        elif step == "ai_concept":
            await self._handle_concept_message(message, campaign, session)
        elif step == "ai_review":
            await self._handle_ai_review_message(message, campaign, session)

    async def _handle_mode_selection_message(self, message, campaign: dict, session: dict):
        normalized = re.sub(r"\s+", " ", message.content.strip().lower())
        mode = _MODE_CHOICES.get(normalized)
        if not mode:
            await message.reply(
                "❌ Выбери режим: `1` ручной, `2` случайный AI-персонаж, `3` AI-персонаж по концепции.",
                mention_author=False,
            )
            return

        if mode == "manual":
            session["mode"] = "manual"
            session["ai_mode"] = None
            session["ai_concept"] = None
            session["ai_draft"] = None
            session["draft"] = {}
            self._touch_session(session)
            logger.info("Character creation mode set. campaign_id=%s user_id=%s mode=manual", campaign["id"], session["user_id"])
            await message.channel.send("🧰 Переключаемся на ручное создание персонажа.")
            await self._send_progress_update(message.channel, session)
            await self._send_prompt_for_step(message.channel, session)
            return

        session["mode"] = mode
        session["ai_mode"] = mode
        session["draft"] = {}
        session["ai_draft"] = None
        self._touch_session(session)
        logger.info("Character creation mode set. campaign_id=%s user_id=%s mode=%s", campaign["id"], session["user_id"], mode)

        if mode == "ai_random":
            await self._generate_and_show_ai_draft(message.channel, campaign, session)
            return

        session["ai_concept"] = None
        await message.channel.send(
            "🧠 Опиши концепт персонажа обычным сообщением в чат: роль, стиль, характер, желаемые архетипы или вайб."
        )

    async def _handle_concept_message(self, message, campaign: dict, session: dict):
        concept = message.content.strip()
        if not concept:
            return

        session["ai_concept"] = concept[:700]
        self._touch_session(session)
        logger.info(
            "AI concept received. campaign_id=%s user_id=%s concept_length=%s",
            campaign["id"],
            session["user_id"],
            len(session["ai_concept"]),
        )
        await self._generate_and_show_ai_draft(message.channel, campaign, session, concept=session["ai_concept"])

    async def _handle_ai_review_message(self, message, campaign: dict, session: dict):
        normalized = re.sub(r"\s+", " ", message.content.strip().lower())
        action = _REVIEW_ACTIONS.get(normalized)
        if not action:
            await message.reply(
                "ℹ️ Для AI-черновика напиши `подтвердить`, `заново` или `ручной`. "
                "Имя можно заменить командой `!имя <имя>`.",
                mention_author=False,
            )
            return

        if action == "confirm":
            await self._finalize_session_draft(message.channel, campaign, session, session["ai_draft"])
            return
        if action == "reroll":
            await self._generate_and_show_ai_draft(
                message.channel,
                campaign,
                session,
                concept=session.get("ai_concept"),
            )
            return

        self._activate_manual_from_ai_draft(session)
        logger.info("AI review switched to manual mode. campaign_id=%s user_id=%s", campaign["id"], session["user_id"])
        await message.channel.send(
            "✍️ Переключаемся в ручной режим на основе AI-черновика. Можешь менять расу, класс, бонусы или завершить создание через `!имя <имя>`."
        )
        await self._send_progress_update(message.channel, session)
        await self._send_prompt_for_step(message.channel, session)

    async def _generate_and_show_ai_draft(self, target, campaign: dict, session: dict, concept: str | None = None):
        self._touch_session(session)
        logger.info(
            "AI draft generation started. campaign_id=%s user_id=%s mode=%s concept_length=%s",
            campaign["id"],
            session["user_id"],
            session.get("ai_mode"),
            len(concept or ""),
        )
        async with target.typing():
            try:
                draft = await asyncio.to_thread(self._generate_ai_draft, campaign, session["ai_mode"], concept)
            except RuntimeError as error:
                logger.warning(
                    "AI draft generation failed. campaign_id=%s user_id=%s mode=%s error=%s",
                    campaign["id"],
                    session["user_id"],
                    session.get("ai_mode"),
                    error,
                )
                logger.info(
                    "AI draft session remains active after controlled failure. campaign_id=%s user_id=%s next_step=%s",
                    campaign["id"],
                    session["user_id"],
                    self._get_session_step(session),
                )
                await target.send(_display_error(error))
                return
            except Exception:
                logger.exception(
                    "Unexpected AI draft generation failure. campaign_id=%s user_id=%s mode=%s",
                    campaign["id"],
                    session["user_id"],
                    session.get("ai_mode"),
                )
                await target.send(
                    "⚠️ Во время AI-генерации произошла неожиданная ошибка. Попробуй `!перегенерировать`, перейти в ручной режим или отправить концепт ещё раз."
                )
                return

        session["ai_draft"] = draft
        self._touch_session(session)
        logger.info(
            "AI draft generation completed. campaign_id=%s user_id=%s mode=%s",
            campaign["id"],
            session["user_id"],
            session.get("ai_mode"),
        )
        await target.send("🎲 AI собрал черновик персонажа. Проверь его перед созданием.")
        await self._send_ai_review_prompt(target, draft)

    def _generate_ai_draft(self, campaign: dict, mode: str | None, concept: str | None = None) -> dict:
        if mode not in {"ai_random", "ai_concept"}:
            raise RuntimeError("AI-генерация сейчас недоступна для этого режима.")

        base_stats = generate_stats()
        request = self._build_ai_generation_request(campaign, mode, base_stats, concept=concept)
        last_error = None

        for _ in range(2):
            user_content = request
            if last_error:
                logger.warning(
                    "Retrying AI draft generation after invalid response. campaign_id=%s mode=%s error=%s",
                    campaign["id"],
                    mode,
                    last_error,
                )
                user_content += f"\n\nПредыдущий ответ был невалиден: {last_error}\nВерни новый JSON, исправив ошибку."
            raw = ai_provider.call_api(
                _CHARACTER_GENERATOR_PROMPT,
                [{"role": "user", "content": user_content}],
                max_tokens=1800,
            )
            try:
                draft = self._normalize_ai_generation_response(
                    raw,
                    campaign,
                    base_stats,
                    concept=concept,
                    mode=mode,
                )
                logger.info(
                    "AI draft normalized. campaign_id=%s mode=%s race=%s subrace=%s class=%s asi_pattern=%s",
                    campaign["id"],
                    mode,
                    draft["race"],
                    draft.get("subrace"),
                    draft["class"],
                    tuple(sorted(draft["origin_asi"].values(), reverse=True)),
                )
                return draft
            except ValueError as error:
                logger.warning(
                    "AI draft normalization failed. campaign_id=%s mode=%s error=%s",
                    campaign["id"],
                    mode,
                    error,
                )
            last_error = _display_error(error)

        raise RuntimeError(
            "⚠️ Не удалось собрать валидный AI-черновик персонажа. Попробуй `!перегенерировать` или перейди в ручной режим."
        )

    def _build_ai_generation_request(
        self,
        campaign: dict,
        mode: str,
        base_stats: dict[str, int],
        concept: str | None = None,
    ) -> str:
        races_payload = []
        for race in game_data.get_races():
            races_payload.append(
                {
                    "key": race["key"],
                    "label": race["label"],
                    "description": race["description"],
                    "subraces": [
                        {
                            "key": subrace["key"],
                            "label": subrace["label"],
                            "description": subrace["description"],
                        }
                        for subrace in race.get("subraces", [])
                    ],
                }
            )

        classes_payload = []
        for cls in leveling.get_available_classes(campaign.get("ruleset", "dnd_2024_phb")):
            classes_payload.append(
                {
                    "key": cls["key"],
                    "label": cls["label"],
                    "description": cls["description"],
                    "primary_stat": cls["primary_stat"],
                }
            )

        mode_text = "случайный герой" if mode == "ai_random" else "герой по концепции"
        lines = [
            f"Режим генерации: {mode_text}.",
            f"Правила кампании: {campaign.get('ruleset', 'dnd_2024_phb')}.",
            "Подкласс на 1 уровне не выбирать.",
            "Имя можно сгенерировать, если игрок его не дал.",
            f"Базовые характеристики уже брошены и менять их нельзя: {json.dumps(base_stats, ensure_ascii=False)}",
            "Доступные расы и подрасы:",
            json.dumps(races_payload, ensure_ascii=False),
            "Доступные классы:",
            json.dumps(classes_payload, ensure_ascii=False),
            "Допустимые схемы ASI происхождения: либо {2,1} по двум разным характеристикам, либо {1,1,1} по трём разным.",
        ]
        if concept:
            lines.append(f"Концепт игрока: {concept}")
        return "\n\n".join(lines)

    def _normalize_ai_generation_response(
        self,
        raw_text: str,
        campaign: dict,
        base_stats: dict[str, int],
        *,
        concept: str | None,
        mode: str,
    ) -> dict:
        parsed = _extract_json(raw_text)
        if not isinstance(parsed, dict):
            raise ValueError("Ответ AI не является JSON-объектом.")

        race_value = str(parsed.get("race", "")).strip()
        race = game_data.find_race(race_value)
        if not race:
            raise ValueError(f"Не удалось распознать расу: {race_value or 'пусто'}.")

        subrace_value = str(parsed.get("subrace", "")).strip()
        if race.get("subraces"):
            if not subrace_value:
                raise ValueError(f"Для расы {race['label']} нужно выбрать подрасу.")
            subrace = game_data.find_subrace(race["key"], subrace_value)
            if not subrace:
                raise ValueError(f"Не удалось распознать подрасу: {subrace_value}.")
            subrace_key = subrace["key"]
        else:
            subrace_key = None

        class_value = str(parsed.get("class", "")).strip()
        class_data = game_data.find_class(class_value)
        if not class_data:
            raise ValueError(f"Не удалось распознать класс: {class_value or 'пусто'}.")
        if not leveling.is_supported_class_for_campaign(class_data["key"], campaign.get("ruleset", "dnd_2024_phb")):
            raise ValueError(f"Класс {class_data['label']} не поддерживается в текущей кампании.")

        origin_asi = self._normalize_ai_asi(parsed.get("origin_asi"), base_stats)
        final_stats = game_data.apply_origin_asi(base_stats, origin_asi)

        name = str(parsed.get("name", "")).strip()[:30]
        if not name:
            raise ValueError("AI не вернул имя персонажа.")

        concept_summary = str(parsed.get("concept_summary") or parsed.get("reason") or "").strip()
        if not concept_summary:
            concept_summary = (
                f"AI собрал случайный архетип {game_data.get_character_archetype_text(race['key'], class_data['key'], subrace_key=subrace_key)}."
                if mode == "ai_random"
                else f"AI собрал билд по концепции игрока: {concept or 'без подробностей'}"
            )

        hp = class_data["base_hp"] + max(0, (final_stats["constitution"] - 10) // 2)
        logger.info(
            "AI draft validation success. campaign_id=%s mode=%s race=%s subrace=%s class=%s",
            campaign["id"],
            mode,
            race["key"],
            subrace_key,
            class_data["key"],
        )
        return {
            "name": name,
            "race": race["key"],
            "subrace": subrace_key,
            "class": class_data["key"],
            "origin_asi": origin_asi,
            "base_stats": dict(base_stats),
            "final_stats": final_stats,
            "hp": hp,
            "concept_summary": concept_summary,
            "ai_mode": mode,
            "concept_input": concept,
        }

    def _normalize_ai_asi(self, raw_asi, base_stats: dict[str, int]) -> dict[str, int]:
        if not isinstance(raw_asi, dict):
            raise ValueError("AI не вернул корректный объект origin_asi.")

        normalized = {}
        for raw_key, raw_value in raw_asi.items():
            stat_key = game_data.resolve_stat_key(str(raw_key))
            if not stat_key:
                raise ValueError(f"Неизвестная характеристика в ASI: {raw_key}")
            try:
                bonus_value = int(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"ASI для {raw_key} должен быть числом.") from exc
            normalized[stat_key] = bonus_value

        self._validate_asi_pattern(normalized, base_stats)
        return normalized

    async def _finalize_session_draft(self, target, campaign: dict, session: dict, draft: dict):
        user_id = str(session["user_id"])
        if not draft.get("name"):
            await target.send("❌ У персонажа должно быть имя. Используй `!имя <имя>`.")
            return

        char_data, meta = self._build_character_payload(draft)
        success = db.create_character(user_id, campaign["id"], char_data)
        if not success:
            logger.warning("Character creation finalization failed: already exists. campaign_id=%s user_id=%s", campaign["id"], user_id)
            await target.send("⚠️ Персонаж уже существует в этой кампании.")
            _creation_sessions.pop(campaign["id"], None)
            return

        _creation_sessions.pop(campaign["id"], None)
        logger.info(
            "Character creation finalized. campaign_id=%s user_id=%s mode=%s name=%s",
            campaign["id"],
            user_id,
            draft.get("ai_mode", "manual"),
            char_data["name"],
        )
        await self._send_created_character_embed(target, meta, ai_summary=draft.get("concept_summary"))

    def _build_character_payload(self, draft: dict) -> tuple[dict, dict]:
        class_data = game_data.find_class(draft["class"])
        if not class_data:
            raise ValueError("Не удалось найти класс для финализации персонажа.")

        base_stats = draft["base_stats"]
        final_stats = game_data.apply_origin_asi(base_stats, draft["origin_asi"])
        hp = class_data["base_hp"] + max(0, (final_stats["constitution"] - 10) // 2)
        build_state = leveling.initialize_build_state(draft["class"], 1, final_stats)
        char_data = {
            "name": draft["name"].strip()[:30],
            "race": draft["race"],
            "subrace": draft.get("subrace"),
            "class": draft["class"],
            "subclass": None,
            "origin_asi": draft["origin_asi"],
            "hp": hp,
            "max_hp": hp,
            **build_state,
            **final_stats,
        }
        meta = {
            "name": char_data["name"],
            "class_data": class_data,
            "hp": hp,
            "final_stats": final_stats,
            "archetype": game_data.get_character_archetype_text(
                draft["race"],
                draft["class"],
                subrace_key=draft.get("subrace"),
                subclass_key=None,
            ),
        }
        return char_data, meta

    async def _send_created_character_embed(self, target, meta: dict, ai_summary: str | None = None):
        stat_lines = "\n".join(
            f"**{game_data.get_stat_label(stat['key'])}**: {meta['final_stats'][stat['key']]}"
            for stat in game_data.get_stats()
        )
        embed = discord.Embed(
            title=f"✅ {meta['name']} создан!",
            description=f"**{meta['archetype']}**, уровень 1",
            color=0x00FF99,
        )
        if ai_summary:
            embed.add_field(name="🧠 Концепт AI", value=ai_summary, inline=False)
        embed.add_field(name="❤️ HP", value=f"{meta['hp']}/{meta['hp']}", inline=True)
        embed.add_field(name="💰 Золото", value="10", inline=True)
        embed.add_field(name="📊 Характеристики", value=stat_lines, inline=False)
        embed.add_field(
            name="🛡️ Подкласс",
            value=(
                f"Подкласс для **{meta['class_data']['label']}** откроется на {meta['class_data']['subclass_level']} уровне. "
                "Когда персонаж достигнет нужного уровня, используй `!подкласс <название>`."
            ),
            inline=False,
        )
        embed.set_footer(text="Создание завершено, блокировка канала снята.")
        await target.send(embed=embed)

    async def _ensure_character_build(self, campaign: dict, char: dict) -> dict:
        normalized, updates = leveling.ensure_character_build(char)
        if updates:
            db.update_character(char["user_id"], campaign["id"], updates)
            normalized = db.get_character(char["user_id"], campaign["id"])
        return normalized

    def _levelup_status_label(self, progress: dict, queue_entry: dict | None) -> str:
        if queue_entry:
            if queue_entry["status"] == "active":
                return "идёт мастер повышения"
            if queue_entry["status"] == "pending":
                return "в очереди на повышение"
        if progress["ready"]:
            return "готов к повышению"
        return "ещё набирает XP"

    async def _require_owned_session(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return None, None

        self._expire_stale_session(campaign["id"])
        session = _creation_sessions.get(campaign["id"])
        if not session:
            await ctx.send("❌ Сначала начни создание персонажа: `!создать_персонажа` или `!сп`.")
            return None, campaign

        user_id = str(ctx.author.id)
        if session["user_id"] != user_id:
            await ctx.send(
                f"⏳ Сейчас персонажа создаёт <@{session['user_id']}>. "
                "Дождись завершения, `!отмена_создания` владельцем или таймаута 15 минут."
            )
            return None, campaign

        self._touch_session(session)
        return session, campaign

    def _expire_stale_session(self, campaign_id: int) -> dict | None:
        session = _creation_sessions.get(campaign_id)
        if not session:
            return None
        if _utcnow() - session["last_activity_at"] <= _CREATION_TIMEOUT:
            return None
        return _creation_sessions.pop(campaign_id, None)

    def _touch_session(self, session: dict):
        session["last_activity_at"] = _utcnow()
        session["current_step"] = self._get_session_step(session)

    def _get_session_step(self, session: dict) -> str:
        mode = session.get("mode")
        if mode is None:
            return "mode_select"
        if mode == "manual":
            return self._get_manual_next_step(session["draft"])
        if mode == "ai_concept" and not session.get("ai_draft") and not session.get("ai_concept"):
            return "ai_concept"
        if session.get("ai_draft"):
            return "ai_review"
        return "ai_concept" if mode == "ai_concept" else "ai_review"

    def _get_manual_next_step(self, draft: dict) -> str:
        if not draft.get("race"):
            return "race"
        if self._race_requires_subrace(draft) and not draft.get("subrace"):
            return "subrace"
        if not draft.get("class"):
            return "class"
        if not draft.get("origin_asi"):
            return "origin_asi"
        return "name"

    async def _ensure_manual_mode(self, ctx, session: dict) -> bool:
        if session.get("mode") == "manual":
            return True
        if session.get("ai_draft"):
            self._activate_manual_from_ai_draft(session)
            await ctx.send(
                "✍️ Переключаемся в ручной режим на основе AI-черновика. Продолжай менять шаги через команды."
            )
            return True

        await ctx.send("❌ Сначала выбери режим создания: ручной, случайный AI или AI по концепции.")
        await self._send_prompt_for_step(ctx, session)
        return False

    def _activate_manual_from_ai_draft(self, session: dict):
        ai_draft = session.get("ai_draft") or {}
        session["mode"] = "manual"
        session["draft"] = {
            "race": ai_draft.get("race"),
            "subrace": ai_draft.get("subrace"),
            "class": ai_draft.get("class"),
            "base_stats": dict(ai_draft.get("base_stats") or {}),
            "origin_asi": dict(ai_draft.get("origin_asi") or {}),
        }
        session["ai_draft"] = None
        session["ai_mode"] = None
        session["ai_concept"] = None
        self._touch_session(session)

    def _race_requires_subrace(self, draft: dict) -> bool:
        race_key = draft.get("race")
        if not race_key:
            return False
        race_data = game_data.find_race(race_key)
        return bool(race_data and race_data.get("subraces"))

    async def _send_progress_update(self, target, session: dict, note: str | None = None):
        embed = discord.Embed(title="🧭 Мастер создания персонажа", color=0x7B68EE)
        if note:
            embed.description = note
        embed.add_field(name="Игрок", value=f"<@{session['user_id']}>", inline=True)
        embed.add_field(name="Следующий шаг", value=self._step_label(self._get_session_step(session)), inline=True)
        embed.add_field(name="Таймаут", value="15 минут бездействия", inline=True)

        mode = session.get("mode")
        mode_label = {
            None: "не выбран",
            "manual": "ручной",
            "ai_random": "AI случайный",
            "ai_concept": "AI по концепции",
        }.get(mode, str(mode))
        embed.add_field(name="Режим", value=mode_label, inline=True)

        if mode == "manual":
            draft = session["draft"]
            race_data = game_data.find_race(draft["race"]) if draft.get("race") else None
            embed.add_field(
                name="Раса",
                value=game_data.get_race_label(draft["race"]) if draft.get("race") else "—",
                inline=True,
            )
            if race_data and race_data.get("subraces"):
                subrace_value = game_data.get_subrace_label(draft["race"], draft.get("subrace")) if draft.get("subrace") else "—"
            else:
                subrace_value = "не требуется" if draft.get("race") else "—"
            embed.add_field(name="Подраса", value=subrace_value, inline=True)
            embed.add_field(
                name="Класс",
                value=game_data.get_class_label(draft["class"]) if draft.get("class") else "—",
                inline=True,
            )
            embed.add_field(
                name="ASI",
                value=self._format_origin_asi(draft["origin_asi"]) if draft.get("origin_asi") else "—",
                inline=True,
            )
        elif session.get("ai_draft"):
            ai_draft = session["ai_draft"]
            embed.add_field(name="Черновик", value=ai_draft["name"], inline=True)
            embed.add_field(
                name="Архетип",
                value=game_data.get_character_archetype_text(
                    ai_draft["race"],
                    ai_draft["class"],
                    subrace_key=ai_draft.get("subrace"),
                ),
                inline=False,
            )

        embed.add_field(name="Отмена", value="`!отмена_создания`", inline=True)
        await target.send(embed=embed)

    async def _send_prompt_for_step(self, target, session: dict):
        step = self._get_session_step(session)
        if step == "mode_select":
            await self._send_mode_prompt(target)
            return
        if step == "ai_concept":
            await self._send_concept_prompt(target)
            return
        if step == "ai_review":
            await self._send_ai_review_prompt(target, session["ai_draft"])
            return

        draft = session["draft"]
        if step == "race":
            await self._send_race_prompt(target)
        elif step == "subrace":
            race_data = game_data.find_race(draft["race"])
            await self._send_subrace_prompt(target, race_data)
        elif step == "class":
            campaign = db.get_campaign(session["campaign_id"])
            await self._send_class_prompt(target, campaign)
        elif step == "origin_asi":
            class_data = game_data.find_class(draft["class"])
            await self._send_asi_prompt(target, class_data, draft["base_stats"])
        else:
            await target.send("✏️ Последний обязательный шаг: введи имя персонажа через `!имя <имя>`.")

    async def _send_mode_prompt(self, target):
        embed = discord.Embed(title="🧭 Выбери режим создания", color=0x7B68EE)
        embed.description = (
            "Напиши в чат номер или название режима.\n\n"
            "**1. Ручной** - выбрать расу, класс и бонусы шаг за шагом.\n"
            "**2. Случайный AI** - бот сгенерирует готовый черновик персонажа.\n"
            "**3. AI по концепции** - сначала ты опишешь идею, затем бот соберёт билд."
        )
        embed.set_footer(text="Примеры: `1`, `ручной`, `2`, `случайный`, `3`, `концепт`.")
        await target.send(embed=embed)

    async def _send_concept_prompt(self, target):
        await target.send(
            "🧠 Опиши персонажа обычным сообщением в чат. Например: "
            "`хочу хитрого городского следопыта, который выглядит как добряк, но любит авантюры`."
        )

    async def _send_ai_review_prompt(self, target, ai_draft: dict):
        archetype = game_data.get_character_archetype_text(
            ai_draft["race"],
            ai_draft["class"],
            subrace_key=ai_draft.get("subrace"),
        )
        base_stats = self._format_stats_block(ai_draft["base_stats"])
        final_stats = self._format_stats_block(ai_draft["final_stats"])
        embed = discord.Embed(
            title=f"🎲 AI-черновик: {ai_draft['name']}",
            description=f"**{archetype}**, уровень 1",
            color=0x40A9FF,
        )
        embed.add_field(name="🧠 Концепт", value=ai_draft["concept_summary"], inline=False)
        embed.add_field(name="📦 Базовые характеристики", value=base_stats, inline=False)
        embed.add_field(name="🧬 ASI происхождения", value=self._format_origin_asi(ai_draft["origin_asi"]), inline=False)
        embed.add_field(name="📊 Итоговые характеристики", value=final_stats, inline=False)
        embed.add_field(name="❤️ HP", value=f"{ai_draft['hp']}/{ai_draft['hp']}", inline=True)
        embed.add_field(name="💰 Золото", value="10", inline=True)
        embed.add_field(
            name="Дальше",
            value=(
                "Напиши `подтвердить`, чтобы создать персонажа.\n"
                "Напиши `заново`, чтобы сгенерировать новый черновик.\n"
                "Напиши `ручной`, чтобы перейти к ручной настройке.\n"
                "Или используй `!имя <имя>`, чтобы заменить имя перед подтверждением."
            ),
            inline=False,
        )
        await target.send(embed=embed)

    async def _send_race_prompt(self, target):
        embed = discord.Embed(title="🧝 Шаг 1: Выбор расы", color=0x7B68EE)
        for race in game_data.get_races():
            embed.add_field(name=race["label"], value=self._option_text(race), inline=False)
        embed.set_footer(text="Введи: !раса <название>. Повторная команда изменит выбор и сбросит зависимые шаги ниже.")
        await target.send(embed=embed)

    async def _send_subrace_prompt(self, target, race_data: dict):
        embed = discord.Embed(title=f"🧬 Шаг 2: Подраса для {race_data['label']}", color=0x7B68EE)
        for subrace in race_data.get("subraces", []):
            embed.add_field(name=subrace["label"], value=self._option_text(subrace), inline=False)
        embed.set_footer(text="Введи: !подраса <название>. Изменение подрасы сбросит класс, ASI и имя.")
        await target.send(embed=embed)

    async def _send_class_prompt(self, target, campaign: dict):
        embed = discord.Embed(title="⚔️ Шаг 3: Выбор класса", color=0x7B68EE)
        for cls in leveling.get_available_classes(campaign.get("ruleset", "dnd_2024_phb")):
            value = f"{cls['description']}\nПодтверждённых подклассов: {len(cls.get('subclasses', []))}"
            if cls.get("source"):
                value += f"\nИсточник: {cls['source']}"
            embed.add_field(name=cls["label"], value=value, inline=False)
        embed.set_footer(text="Введи: !класс <название>. Смена класса сгенерирует новые базовые характеристики.")
        await target.send(embed=embed)

    async def _send_asi_prompt(self, target, class_data: dict, base_stats: dict[str, int]):
        embed = discord.Embed(title="✨ Шаг 4: Бонусы происхождения", color=0x7B68EE)
        embed.description = (
            f"Класс **{class_data['label']}** выбран. Подкласс откроется на **{class_data['subclass_level']} уровне**.\n"
            "Теперь выбери бонусы характеристик по правилам 2024: `+2/+1` в разные характеристики или `+1/+1/+1` в три разные."
        )
        embed.add_field(name="Базовые характеристики", value=self._format_stats_block(base_stats), inline=False)
        embed.add_field(
            name="Примеры",
            value=(
                "`!бонусы strength +2 wisdom +1`\n"
                "`!бонусы сила 2 мудрость 1`\n"
                "`!бонусы ловкость 1 мудрость 1 харизма 1`"
            ),
            inline=False,
        )
        embed.set_footer(text="Повторная команда !бонусы заменит текущий ASI.")
        await target.send(embed=embed)

    def _parse_asi_input(self, raw_value: str) -> dict[str, int]:
        cleaned = re.sub(r"[,;]", " ", raw_value)
        tokens = cleaned.split()
        if len(tokens) < 4 or len(tokens) % 2 != 0:
            raise ValueError(
                "❌ Формат бонусов неверный. Пример: `!бонусы сила +2 мудрость +1` или `!бонусы ловкость 1 мудрость 1 харизма 1`."
            )

        parsed = {}
        for index in range(0, len(tokens), 2):
            stat_token = tokens[index]
            value_token = tokens[index + 1]
            stat_key = game_data.resolve_stat_key(stat_token)
            if not stat_key:
                raise ValueError(f"❌ Неизвестная характеристика: `{stat_token}`.")
            try:
                bonus_value = int(value_token.replace("+", ""))
            except ValueError as exc:
                raise ValueError(f"❌ Бонус для `{stat_token}` должен быть числом.") from exc
            if bonus_value <= 0:
                raise ValueError("❌ Бонусы должны быть положительными числами.")
            if stat_key in parsed:
                raise ValueError("❌ Нельзя выбрать одну и ту же характеристику дважды.")
            parsed[stat_key] = bonus_value
        return parsed

    def _validate_asi_pattern(self, bonuses: dict[str, int], base_stats: dict[str, int]):
        pattern = tuple(sorted(bonuses.values(), reverse=True))
        if pattern not in _ASI_ALLOWED_PATTERNS:
            raise ValueError("❌ Разрешены только схемы `+2/+1` или `+1/+1/+1`.")
        for stat_key, bonus_value in bonuses.items():
            final_value = base_stats.get(stat_key, 0) + bonus_value
            if final_value > 20:
                raise ValueError(
                    f"❌ После бонуса характеристика **{game_data.get_stat_label(stat_key)}** станет {final_value}. Потолок - 20."
                )

    def _step_label(self, step: str) -> str:
        return {
            "mode_select": "Выбор режима",
            "ai_concept": "Описание концепта",
            "ai_review": "Проверка AI-черновика",
            "race": "Раса (`!раса ...`)",
            "subrace": "Подраса (`!подраса ...`)",
            "class": "Класс (`!класс ...`)",
            "origin_asi": "ASI (`!бонусы ...`)",
            "name": "Имя (`!имя ...`)",
        }.get(step, step)

    def _format_labels(self, items: list[dict]) -> str:
        return ", ".join(item["label"] for item in items)

    def _format_stats_block(self, stats: dict[str, int]) -> str:
        return "\n".join(
            f"**{game_data.get_stat_label(stat['key'])}**: {stats[stat['key']]}"
            for stat in game_data.get_stats()
        )

    def _format_origin_asi(self, bonuses: dict[str, int]) -> str:
        return ", ".join(
            f"{game_data.get_stat_label(stat_key)} +{bonus_value}"
            for stat_key, bonus_value in bonuses.items()
        )

    def _option_text(self, item: dict) -> str:
        lines = [item["description"]]
        if item.get("source"):
            lines.append(f"Источник: {item['source']}")
        if item.get("legacy"):
            lines.append("Статус: Legacy")
        if item.get("requires_dm_approval"):
            lines.append("Требует одобрения DM")
        return "\n".join(lines)

    def _looks_like_command(self, content: str) -> bool:
        stripped = content.strip()
        if not stripped:
            return False
        if stripped.startswith("!"):
            return True
        if self.bot.user:
            return stripped.startswith(f"<@{self.bot.user.id}>") or stripped.startswith(f"<@!{self.bot.user.id}>")
        return False


def _hp_bar(hp: int, max_hp: int) -> str:
    filled = round((hp / max_hp) * 10) if max_hp > 0 else 0
    return "█" * filled + "░" * (10 - filled)


def _stat_emoji(stat_key: str) -> str:
    return {
        "strength": "💪",
        "dexterity": "🏃",
        "constitution": "🛡️",
        "intelligence": "🧠",
        "wisdom": "👁️",
        "charisma": "✨",
    }.get(stat_key, "•")


async def setup(bot):
    await bot.add_cog(CharacterCog(bot))
