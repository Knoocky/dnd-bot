import random
import re
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
import asyncio

import database as db
import dungeon_master as dm
import game_data
import leveling

ROUND_TIMEOUT_MINUTES = 5
XP_AWARD_MAX = 50
XP_AWARD_COOLDOWN_MINUTES = 20
SETUP_ROLL_MODE_CHOICES = {
    "1": "bot_auto",
    "бот": "bot_auto",
    "авто": "bot_auto",
    "auto": "bot_auto",
    "сам": "bot_auto",
    "ботсам": "bot_auto",
    "бот_сам": "bot_auto",
    "2": "player_manual",
    "игрок": "player_manual",
    "игроки": "player_manual",
    "manual": "player_manual",
    "ручной": "player_manual",
    "сами": "player_manual",
}
SETUP_HP_GAIN_MODE_CHOICES = {
    "1": "fixed",
    "fixed": "fixed",
    "fix": "fixed",
    "2": "roll",
    "roll": "roll",
    "3": "choose_each_level",
    "choose": "choose_each_level",
    "choice": "choose_each_level",
}
MAIN_QUEST_PRESSURE_CHOICES = {
    "редко": "rare",
    "редкий": "rare",
    "rare": "rare",
    "мягко": "soft",
    "мягкий": "soft",
    "soft": "soft",
    "жестко": "hard",
    "жёстко": "hard",
    "жесткий": "hard",
    "жёсткий": "hard",
    "hard": "hard",
}
MAIN_QUEST_PRESSURE_LABELS = {
    "rare": "редко",
    "soft": "мягко",
    "hard": "жёстко",
}
MAIN_QUEST_PRESSURE_DESCRIPTIONS = {
    "rare": "побочные отклонения обычно допустимы, а ощутимые последствия приходят только после долгого игнора главной линии.",
    "soft": "почти каждое заметное отклонение имеет цену по времени, ресурсу или позиции, но не ломает сюжет сразу.",
    "hard": "главная линия давит сильно: враги двигаются быстрее, окна возможностей закрываются, а промедление дорого стоит.",
}
INTRO_SETUP_STEPS = [
    {
        "status": "awaiting_intro_genre",
        "key": "genre",
        "title": "🎭 Шаг 1: Жанр",
        "prompt": (
            "Какой жанр хотите для кампании? Ответь одним сообщением.\n\n"
            "Примеры: `героическое фэнтези`, `тёмное фэнтези`, `авантюрное фэнтези с юмором`, "
            "`городское расследование с руинами и мистикой`."
        ),
    },
    {
        "status": "awaiting_intro_tone",
        "key": "tone",
        "title": "🎚️ Шаг 2: Тональность",
        "prompt": (
            "Какой тон игры вам нужен?\n\n"
            "Примеры: `лёгкий и приключенческий`, `умеренно серьёзный с юмором`, "
            "`мрачный и опасный`, `эпичный и кинематографичный`."
        ),
    },
    {
        "status": "awaiting_intro_include_themes",
        "key": "include_themes",
        "title": "🧭 Шаг 3: Темы",
        "prompt": (
            "Какие темы и элементы хотите явно видеть в кампании?\n\n"
            "Примеры: `исследование руин, торговля, магические загадки`, "
            "`политические интриги`, `морские путешествия`, `охота на монстров`."
        ),
    },
    {
        "status": "awaiting_intro_exclude_themes",
        "key": "exclude_themes",
        "title": "🛡️ Шаг 4: Границы",
        "prompt": (
            "Что стоит исключить или сильно приглушить?\n\n"
            "Примеры: `без откровенной эротики`, `без жестоких пыток`, "
            "`без религиозных провокаций`, `ничего дополнительно не исключать`."
        ),
    },
    {
        "status": "awaiting_intro_lethality",
        "key": "lethality",
        "title": "⚔️ Шаг 5: Опасность",
        "prompt": (
            "Насколько суровыми должны быть последствия и летальность?\n\n"
            "Примеры: `низкая`, `умеренная`, `высокая`, "
            "`умеренная, но смерть возможна при плохих решениях`."
        ),
    },
    {
        "status": "awaiting_intro_rules_mode",
        "key": "rules_mode",
        "title": "📜 Шаг 6: Режим Правил",
        "prompt": (
            "Как обращаться с правилами?\n\n"
            "Примеры: `строго по правилам`, `правила в основе, но с упрощением`, "
            "`кинематографично и свободно`."
        ),
    },
    {
        "status": "awaiting_intro_setting",
        "key": "setting",
        "title": "🌍 Шаг 7: Сеттинг",
        "prompt": (
            "Какой мир или сеттинг хотите?\n\n"
            "Примеры: `оригинальный фэнтезийный мир`, `что-то в духе Forgotten Realms`, "
            "`приграничный торговый город рядом с древними руинами`."
        ),
    },
]
INTRO_STEP_BY_STATUS = {step["status"]: step for step in INTRO_SETUP_STEPS}
INTRO_NEXT_STATUS = {
    step["status"]: (
        INTRO_SETUP_STEPS[index + 1]["status"] if index + 1 < len(INTRO_SETUP_STEPS) else None
    )
    for index, step in enumerate(INTRO_SETUP_STEPS)
}
INLINE_ROLL_RE = re.compile(r"(?:бросок|roll)\s*[:=-]?\s*(\d{1,3})", re.IGNORECASE)
MANUAL_TOTAL_RE = re.compile(r"^\s*(-?\d+)(?:\s+[A-Za-zА-Яа-яёЁ]+)?\s*$")
DICE_NOTATION_RE = re.compile(r"^\s*(\d*)\s*[dдк](\d+)\s*$", re.IGNORECASE)
AUTO_ROLL_REJECTION_REACTIONS = [
    "🎲 Твой кубик бодро кувыркнулся в пустоту. В этой кампании судьбу официально подписывает только бот.",
    "🧙 Местная магия недействительна для частных бросков. Здесь кубики игроков не имеют юридической силы.",
    "🎪 Красивый бросок. Жаль, что в этой кампании он имеет примерно ту же власть, что чайная ложка на суде богов.",
    "🏰 Кубик игрока стучится в ворота сюжета, но стража уже предупредила: вход только по броскам от бота.",
    "📜 Летописец записал твой бросок в раздел 'уважительно проигнорировано'. На сюжет он не влияет.",
    "⚖️ Судьба посмотрела на твой кубик, вздохнула и сказала: 'В этой кампании я работаю только через бота'.",
    "🐉 Дракон впечатлён, но правила мира непреклонны: личные броски игроков здесь не двигают реальность.",
    "🌫️ Кубик был брошен, услышан и торжественно не признан действительным на территории этой кампании.",
    "🎭 Отличная импровизация с кубиком. Но сцена принимает только те броски, которые вызывает бот по ходу действия.",
    "🔮 Твой кубик попытался вмешаться в судьбу героев, но пророчество уже оформлено через auto-режим бота.",
]
MANUAL_NO_PENDING_REACTIONS = [
    "🎲 Кубик уже приготовился к прыжку, но бот шепчет: 'Сейчас бросок вообще не требуется'.",
    "🧾 Заявка на бросок не найдена. Кубик может пока посидеть в зоне ожидания и подумать о вечном.",
    "🧙 Бот не запрашивал бросок, так что этот кубик сейчас безработный, но очень перспективный.",
    "🎯 Меткий бросок, но мимо бюрократии. Сначала нужен запрос от бота, потом уже судьба и математика.",
    "📬 Для этого кубика сейчас нет официального приглашения. Бросок отклонён как слишком инициативный.",
    "🎪 Кубик ворвался на сцену раньше своей реплики. Бот просит дождаться, когда бросок действительно понадобится.",
    "🔔 Сейчас не тот момент, когда мир ждёт от тебя числа. Бот сначала должен попросить конкретный бросок.",
    "🕰️ Твой кубик пришёл слишком рано. Здесь его принимают только по предварительной записи от бота.",
    "📎 Броску не к чему прикрепиться: активного запроса нет. Попробуй сначала совершить действие.",
    "🏹 Кубик натянул тетиву, но стрелять пока некуда. Бот ещё не просил никакой проверки.",
]


class GameCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._resolving_rounds = set()
        self.round_watcher.start()

    def cog_unload(self):
        self.round_watcher.cancel()

    @tasks.loop(seconds=15)
    async def round_watcher(self):
        due_rounds = db.get_due_scene_rounds(db.utcnow_iso())
        for round_data in due_rounds:
            campaign = db.get_campaign(round_data["campaign_id"])
            channel = await self._get_channel(campaign["channel_id"]) if campaign else None
            await self._resolve_round(round_data["id"], channel, timed_out=True)

    @round_watcher.before_loop
    async def before_round_watcher(self):
        await self.bot.wait_until_ready()

    # Campaigns

    @commands.command(name="новая_кампания", aliases=["старт", "нк"])
    @commands.has_permissions(manage_channels=True)
    async def new_campaign(self, ctx, *, title: str = "Путь к Неизведанному"):
        existing = db.get_active_campaign(str(ctx.channel.id))
        if existing:
            await ctx.send(
                f"⚠️ В этом канале уже идёт кампания **{existing['title']}**.\n"
                "Заверши её командой `!завершить_кампанию` перед началом новой."
            )
            return

        campaign_id = db.create_campaign(
            str(ctx.guild.id),
            str(ctx.channel.id),
            title,
            setup_owner_user_id=str(ctx.author.id),
        )
        embed = discord.Embed(
            title=f"⚔️ Кампания «{title}» создана!",
            description=(
                "Перед стартом выбери режим бросков для этой кампании.\n\n"
                "**1. Бот бросает сам** - бот сам решает, когда нужен бросок, и сразу показывает результат.\n"
                "**2. Игроки бросают сами** - бот будет запрашивать нужный бросок и ждать результат от игрока.\n\n"
                "Ответь в чат `1` или `2`"
            ),
            color=0xFF6B35,
        )
        embed.set_footer(text=f"ID кампании: {campaign_id}")
        await ctx.send(embed=embed)

    @commands.command(name="начать_игру", aliases=["ни"])
    async def start_game(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании. Используй `!новая_кампания`.")
            return
        if campaign.get("setup_status") != "ready":
            if not await self._ensure_campaign_ready(ctx, campaign):
                return

        chars = db.get_all_characters(campaign["id"])
        if not chars:
            await ctx.send("❌ Никто ещё не создал персонажа! Используй `!создать_персонажа`.")
            return

        if campaign.get("intro_status") != "completed":
            await self._start_or_resume_intro_setup(ctx.channel, campaign)
            return

        if not await self._ensure_campaign_ready(ctx, campaign):
            return

        db.resume_campaign_auto_wait(campaign["id"])

        async with ctx.typing():
            try:
                intro = await dm.start_campaign(campaign["id"], campaign["title"])
            except RuntimeError as error:
                await ctx.send(str(error))
                return

        names = ", ".join(f"**{char['name']}**" for char in chars)
        await ctx.send(f"🎲 Участники: {names}")
        await self._publish_master_response(ctx.channel, campaign["id"], intro)

    @commands.command(name="завершить_кампанию", aliases=["зк"])
    @commands.has_permissions(manage_channels=True)
    async def end_campaign(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return
        db.end_campaign(campaign["id"])
        await ctx.send(f"📕 Кампания **{campaign['title']}** завершена. История сохранена.")

    @commands.command(name="таймер", aliases=["тм"])
    async def set_timer(self, ctx, minutes: int | None = None):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        if minutes is None:
            await ctx.send(
                f"⏳ Таймер хода для кампании **{campaign['title']}**: {self._round_timeout_minutes(campaign)} мин."
            )
            return

        if not self._can_configure_campaign(ctx.author, ctx.channel):
            await ctx.send("❌ Менять таймер может только модератор канала или владелец настройки кампании.")
            return

        if minutes < 1 or minutes > 120:
            await ctx.send("❌ Таймер должен быть от 1 до 120 минут.")
            return

        campaign = db.set_campaign_scene_round_timeout(campaign["id"], minutes)
        await ctx.send(
            f"✅ Таймер хода для кампании **{campaign['title']}** установлен на {campaign['scene_round_timeout_minutes']} мин."
            " Изменение применяется к новым раундам."
        )

    @commands.command(name="давление_сюжета", aliases=["дс"])
    async def set_story_pressure(self, ctx, *, mode: str | None = None):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        current_mode = campaign.get("main_quest_pressure") or "soft"
        if mode is None:
            await ctx.send(
                f"🧭 Давление главного сюжета для кампании **{campaign['title']}**: "
                f"**{MAIN_QUEST_PRESSURE_LABELS.get(current_mode, 'мягко')}**.\n"
                f"{MAIN_QUEST_PRESSURE_DESCRIPTIONS.get(current_mode, MAIN_QUEST_PRESSURE_DESCRIPTIONS['soft'])}"
            )
            return

        if not self._can_configure_campaign(ctx.author, ctx.channel):
            await ctx.send(
                "❌ Менять давление сюжета может только модератор канала или владелец настройки кампании."
            )
            return

        normalized = MAIN_QUEST_PRESSURE_CHOICES.get(
            re.sub(r"\s+", "", mode.strip().lower()).replace("ё", "е")
        )
        if normalized is None:
            await ctx.send("❌ Используй один из режимов: `редко`, `мягко`, `жестко`.")
            return

        campaign = db.set_campaign_main_quest_pressure(campaign["id"], normalized)
        await ctx.send(
            f"✅ Давление главного сюжета для кампании **{campaign['title']}** установлено на "
            f"**{MAIN_QUEST_PRESSURE_LABELS[normalized]}**.\n"
            f"{MAIN_QUEST_PRESSURE_DESCRIPTIONS[normalized]}"
        )

    # Actions

    @commands.command(name="д", aliases=["действие"])
    async def action(self, ctx, *, text: str):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return
        if not await self._ensure_campaign_ready(ctx, campaign):
            return

        active_round = db.get_active_scene_round(campaign["id"])
        if active_round:
            pending = db.get_open_pending_roll_request(campaign["id"], str(ctx.author.id))
            if pending and pending.get("round_id") == active_round["id"]:
                await ctx.send(
                    "🎲 На твой ход уже запрошен бросок. Пришли итог числом через `!бросок 17` "
                    "или ответь реплаем с пометкой вроде `(бросок 17)`."
                )
                return

            target = db.get_scene_round_target(active_round["id"], str(ctx.author.id))
            if not target:
                await ctx.send("❌ Твой персонаж не участвует в текущем раунде выбора.")
                return
            if target["status"] == "out_of_scene":
                await ctx.send(
                    f"🧭 **{target['character_name_snapshot']}** сейчас вне этой сцены, "
                    "поэтому действие не влияет на текущий эпизод."
                )
                return

            char_name, parsed = self._submit_round_response(
                campaign,
                active_round,
                target,
                str(ctx.author.id),
                str(ctx.message.id),
                text,
            )
            if parsed["response_kind"] == "option":
                await ctx.send(
                    f"✅ Ход для **{char_name}** принят как вариант **{parsed['selected_option']}**."
                )
            else:
                await ctx.send(f"✅ Ход для **{char_name}** принят.")
            await self._maybe_resolve_if_complete(active_round["id"], ctx.channel)
            return

        db.resume_campaign_auto_wait(campaign["id"])
        await self._handle_direct_action(
            ctx.channel,
            campaign,
            ctx.author,
            text,
            str(ctx.message.id),
        )

    # Dice

    @commands.command(name="бросок", aliases=["кубик", "бр"])
    async def roll(self, ctx, dice: str = "d20"):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if campaign and not await self._ensure_campaign_ready(ctx, campaign):
            return

        if campaign and campaign.get("roll_mode") == "bot_auto":
            await ctx.send(self._funny_auto_roll_rejection())
            return

        if campaign and campaign.get("roll_mode") == "player_manual":
            pending = db.get_open_pending_roll_request(campaign["id"], str(ctx.author.id))
            if not pending:
                await ctx.send(self._funny_no_pending_roll_reaction())
                return

            total = self._extract_manual_roll_total(dice)
            if total is not None:
                await self._resolve_pending_roll_request(
                    ctx.channel,
                    campaign,
                    ctx.author,
                    pending,
                    total,
                    str(ctx.message.id),
                    performed_by_bot=False,
                )
                return

            dice_spec = self._parse_dice_notation(dice)
            if not dice_spec:
                await ctx.send(
                    "❌ Для запроса от бота используй `!бросок d20`, `!бросок д20`, `!бросок 2д6` "
                    "или передай готовый итог числом: `!бросок 17`."
                )
                return

            if (
                dice_spec["count"] != pending["dice_count"]
                or dice_spec["sides"] != pending["dice_sides"]
            ):
                await ctx.send(
                    f"❌ Сейчас нужен бросок {self._format_roll_notation(pending['dice_count'], pending['dice_sides'], None)}. "
                    "Этот бросок не засчитан."
                )
                return

            char = db.get_character(str(ctx.author.id), campaign["id"])
            char_name = char["name"] if char else ctx.author.display_name
            roll_data = self._perform_bot_requested_roll(char, char_name, pending)
            await ctx.send(embed=self._build_roll_embed(roll_data, char_name))
            await self._resolve_pending_roll_request(
                ctx.channel,
                campaign,
                ctx.author,
                pending,
                roll_data["total"],
                str(ctx.message.id),
                performed_by_bot=True,
            )
            return

        active_round = db.get_active_scene_round(campaign["id"]) if campaign else None
        if active_round:
            await self._explain_round_restriction(ctx, campaign, active_round)
            return

        dice_spec = self._parse_dice_notation(dice)
        if not dice_spec:
            await ctx.send("❌ Формат: `!бросок d20`, `!бросок д20`, `!бросок к20` или `!бросок 2d6`")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"]) if campaign else None
        char_name = char["name"] if char else ctx.author.display_name
        roll_data = self._perform_standard_roll(char, char_name, dice_spec)
        await ctx.send(embed=self._build_roll_embed(roll_data, char_name))

    # Round control

    @commands.command(name="статус_хода", aliases=["сх"])
    async def turn_status(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        runtime = db.get_campaign_runtime_state(campaign["id"])
        active_round = db.get_active_scene_round(campaign["id"])
        if not active_round:
            if runtime["auto_wait_enabled"]:
                await ctx.send("🕊️ Сейчас нет активного окна выбора. Можно продолжать игру обычными действиями.")
            else:
                await ctx.send(
                    "⏸️ Автоматическое ожидание остановлено после двух пустых раундов подряд. "
                    "Используй `!продолжить_игру`, чтобы снова включить авто-ожидание."
                )
            return

        pending_by_user = {
            item["user_id"]: item for item in db.get_open_pending_roll_requests(campaign["id"])
            if item.get("round_id") == active_round["id"]
        }
        lines = [
            "⏳ **Активный раунд выбора**",
            f"Дедлайн: {self._format_deadline(active_round['deadline_at'])}",
            f"Таймер хода: {self._round_timeout_minutes(campaign)} мин.",
            f"Авто-ожидание: {'включено' if runtime['auto_wait_enabled'] else 'остановлено'}",
            f"Режим бросков: {'бот бросает сам' if campaign.get('roll_mode') == 'bot_auto' else 'игроки бросают сами'}",
            "",
            "Статусы персонажей:",
        ]
        responses_by_user = {response["user_id"]: response for response in active_round["responses"]}
        for target in active_round["targets"]:
            status = target["status"]
            if status == "answered":
                response = responses_by_user.get(target["user_id"])
                if response and response["response_kind"] == "option" and response.get("selected_option"):
                    detail = f"ответил: вариант {response['selected_option']}"
                elif response:
                    detail = "ответил своим действием"
                else:
                    detail = "ответил"
            elif status == "expected":
                pending = pending_by_user.get(target["user_id"])
                if pending:
                    detail = f"ждём бросок {self._format_roll_notation(pending['dice_count'], pending['dice_sides'], pending.get('modifier_stat'))}"
                else:
                    detail = "ждём ответ"
            elif status == "timed_out":
                detail = "таймаут"
            else:
                detail = f"вне сцены ({target.get('reason') or 'не влияет на эпизод'})"
            lines.append(f"- **{target['character_name_snapshot']}** - {detail}")

        await ctx.send("\n".join(lines))

    @commands.command(name="пропустить", aliases=["пх"])
    async def skip_turn(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return
        if not await self._ensure_campaign_ready(ctx, campaign):
            return

        active_round = db.get_active_scene_round(campaign["id"])
        if not active_round:
            await ctx.send("❌ Сейчас нет активного окна выбора.")
            return

        target = db.get_scene_round_target(active_round["id"], str(ctx.author.id))
        if not target:
            await ctx.send("❌ Твой персонаж не участвует в текущем раунде.")
            return
        if target["status"] == "out_of_scene":
            await ctx.send("ℹ️ Твой персонаж сейчас вне сцены и не влияет на этот эпизод.")
            return

        db.cancel_open_pending_roll_requests(campaign["id"], str(ctx.author.id))
        db.upsert_scene_round_response(
            active_round["id"],
            str(ctx.author.id),
            str(ctx.message.id),
            "free_text",
            "[Пропускает ход и не вмешивается]",
        )
        await ctx.send(f"⏭️ Ход для **{target['character_name_snapshot']}** помечен как пропуск.")
        await self._maybe_resolve_if_complete(active_round["id"], ctx.channel)

    @commands.command(name="закрыть_ход", aliases=["зх"])
    @commands.has_permissions(manage_channels=True)
    async def close_turn(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return
        if not await self._ensure_campaign_ready(ctx, campaign):
            return

        active_round = db.get_active_scene_round(campaign["id"])
        if not active_round:
            await ctx.send("❌ Сейчас нет активного окна выбора.")
            return

        await self._resolve_round(active_round["id"], ctx.channel, timed_out=True)

    @commands.command(name="продолжить_игру", aliases=["пг"])
    async def continue_game(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return
        if not await self._ensure_campaign_ready(ctx, campaign):
            return

        runtime = db.resume_campaign_auto_wait(campaign["id"])
        if runtime["auto_wait_enabled"]:
            await ctx.send(
                "▶️ Автоматическое ожидание снова включено. "
                "Следующий ответ Мастера с вариантами откроет новый раунд."
            )
        else:
            await ctx.send("⚠️ Не удалось включить автоматическое ожидание.")

    # History

    @commands.command(name="история", aliases=["лор", "ис"])
    async def story_summary(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        await ctx.send("📖 Составляю хронику приключений...")
        async with ctx.typing():
            try:
                summary = await dm.get_summary(campaign["id"])
            except RuntimeError as error:
                await ctx.send(str(error))
                return

        embed = discord.Embed(
            title=f"📜 История кампании «{campaign['title']}»",
            description=summary,
            color=0x8B4513,
        )
        await ctx.send(embed=embed)

    # Players

    @commands.command(name="игроки", aliases=["партия", "иг"])
    async def show_players(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        chars = db.get_all_characters(campaign["id"])
        if not chars:
            await ctx.send("😶 В кампании пока нет персонажей.")
            return

        embed = discord.Embed(title=f"⚔️ Партия - {campaign['title']}", color=0x7B68EE)
        for char in chars:
            hp_bar = "█" * round((char["hp"] / char["max_hp"]) * 10) + "░" * (10 - round((char["hp"] / char["max_hp"]) * 10))
            embed.add_field(
                name=f"{char['name']} ({game_data.get_character_archetype_text(char['race'], char['class'], subrace_key=char.get('subrace'), subclass_key=char.get('subclass'))})",
                value=f"❤️ `{hp_bar}` {char['hp']}/{char['max_hp']} HP  |  ⭐ Ур.{char['level']}  |  💰 {char['gold']}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="помощь", aliases=["п", "помощь_днд", "команды", "х"])
    async def dnd_help(self, ctx):
        # Keep this help text in sync with public bot commands and aliases.
        embed = discord.Embed(
            title="📚 Команды D&D бота",
            description="Можно использовать префикс `!` или упоминание бота: `@бот команда`.",
            color=0x5865F2,
        )
        embed.add_field(
            name="🗺️ Кампания",
            value=(
                "`!новая_кампания [название]` / `!нк` / `!старт` - начать кампанию, выбрать режим бросков и режим прироста HP\n"
                "`!начать_игру` / `!ни` - запустить вступление\n"
                "`!таймер [минуты]` / `!тм` - показать или изменить таймер на ход для новых раундов\n"
                "`!давление_сюжета [редко|мягко|жестко]` / `!дс` - показать или изменить, как сильно мир давит главным квестом\n"
                "`!завершить_кампанию` / `!зк` - завершить кампанию\n"
                "`!история` / `!ис` / `!лор` - резюме приключения\n"
                "`!игроки` / `!иг` / `!партия` - состав партии"
            ),
            inline=False,
        )
        embed.add_field(
            name="🧝 Персонаж",
            value=(
                "`!создать_персонажа` / `!новый_перс` / `!сп` / `!нп` - начать мастер создания: ручной, случайный AI или AI по концепции\n"
                "`!раса <название>` / `!р` - выбрать или изменить расу\n"
                "`!подраса <название>` / `!пр` - выбрать или изменить подрасу, если она есть\n"
                "`!класс <название>` / `!к` / `!кл` - выбрать или изменить класс\n"
                "`!бонусы <...>` / `!бн` / `!аси` - выбрать стартовый ASI по правилам 2024\n"
                "`!имя <имя>` / `!и` / `!им` - задать имя вручную или заменить имя AI-черновика\n"
                "`!подтвердить_персонажа` - принять AI-черновик и создать персонажа\n"
                "`!перегенерировать` - собрать новый AI-черновик\n"
                "`!отмена_создания` / `!ос` - отменить текущий черновик\n"
                "`!подкласс <название>` / `!пк` - shortcut для шага подкласса во время левелапа\n"
                "`!персонаж` / `!перс` / `!лп` / `!пс` - лист персонажа\n"
                "`!уровень` / `!лвл` / `!опыт` - прогресс XP и готовность к уровню\n"
                "`!левелап` / `!лу` / `!уровень_ап` - начать или продолжить повышение уровня\n"
                "`!выбрать <опция>` / `!выб` - выбрать вариант на текущем шаге левелапа\n"
                "`!отмена_левелапа` / `!ол` - выйти из мастера левелапа"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚔️ Игра",
            value=(
                "`!д <текст|номер>` / `!действие` - совершить своё действие или выбрать вариант по номеру; работает и в открытом раунде выбора\n"
                "`!бросок [кубик/итог]` / `!бр` / `!кубик` - в auto-режиме вернёт шутливый отказ, в manual-режиме примет `d20/д20/к20`, `2d6/2д6/2к6` или итог броска по запросу бота\n"
                "`!статус_хода` / `!сх` - статус текущего раунда\n"
                "`!пропустить` / `!пх` - пропустить свой ход\n"
                "`!закрыть_ход` / `!зх` - закрыть раунд досрочно (модератор)\n"
                "`!продолжить_игру` / `!пг` - снять паузу после пустых раундов"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎒 Инвентарь и помощь",
            value=(
                "`!инвентарь` / `!ин` / `!с` / `!сумка` - показать инвентарь и золото\n"
                "`!взять <предмет>` / `!вз` / `!добавить_предмет` - добавить предмет\n"
                "`!выбросить <предмет>` / `!вб` / `!убрать` - выбросить предмет\n"
                "`!золото` / `!зл` / `!монеты` - показать или изменить золото\n"
                "`!помощь` / `!помощь_днд` / `!п` / `!команды` / `!х` - показать эту справку"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    # Message listener

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        command_ctx = await self.bot.get_context(message)
        if command_ctx.valid:
            return

        campaign = db.get_active_campaign(str(message.channel.id))
        if campaign and campaign.get("setup_status") != "ready":
            if campaign.get("setup_status") == "awaiting_roll_mode":
                if await self._handle_roll_mode_setup_message(message, campaign):
                    return
            elif campaign.get("setup_status") == "awaiting_hp_gain_mode":
                if await self._handle_hp_gain_mode_setup_message(message, campaign):
                    return

        if (
            campaign
            and campaign.get("setup_status") == "ready"
            and campaign.get("intro_status") in INTRO_STEP_BY_STATUS
            and not self._is_command_message(message.content)
        ):
            if await self._handle_intro_setup_message(message, campaign):
                return

        if self._is_command_message(message.content):
            return

        if not campaign:
            return
        if campaign.get("setup_status") != "ready":
            await message.reply(
                "⚙️ Сначала заверши настройку кампании: выбери режим бросков, а затем режим прироста HP.",
                mention_author=False,
            )
            return
        if campaign.get("intro_status") != "completed":
            if self._is_character_creation_message(message):
                return
            await message.reply(
                "🎬 Сначала заверши пошаговую настройку вступления через текущий вопрос мастера.",
                mention_author=False,
            )
            return

        if not message.reference or not message.reference.message_id:
            return

        active_round = db.get_active_scene_round(campaign["id"])
        if not active_round or str(message.reference.message_id) != str(active_round.get("bot_message_id")):
            return

        target = db.get_scene_round_target(active_round["id"], str(message.author.id))
        if not target:
            return

        if target["status"] == "out_of_scene":
            db.add_message(
                campaign["id"],
                "user",
                f"{target['character_name_snapshot']} пытается вмешаться из другой сцены: {message.content.strip()}",
                str(message.author.id),
                message.author.display_name,
            )
            await message.reply(
                f"🧭 **{target['character_name_snapshot']}** сейчас вне этой сцены, "
                "поэтому действие не влияет на текущий эпизод.",
                mention_author=False,
            )
            return

        content = message.content.strip()
        if not content:
            return

        await self._handle_round_reply(message, campaign, active_round, target, content)

    # Internals

    async def _ensure_campaign_ready(self, ctx, campaign: dict) -> bool:
        if campaign.get("setup_status") == "ready" and campaign.get("intro_status") == "completed":
            return True
        if campaign.get("setup_status") == "awaiting_roll_mode":
            await ctx.send(
                "⚙️ Кампания ещё не настроена. Ответь `1` или `2` на сообщение о выборе режима бросков."
            )
            return False
        if campaign.get("setup_status") != "ready":
            await ctx.send(
                "⚙️ Кампания ещё не настроена. Ответь `1`, `2` или `3` на сообщение о выборе режима прироста HP."
            )
            return False
        if campaign.get("intro_status") == "generating_intro":
            await ctx.send("🎬 Вступление уже генерируется. Подожди ещё немного.")
            return False
        await ctx.send(
            "🎬 Перед началом игры нужно пройти пошаговую настройку вступления. Используй `!начать_игру` и ответь на вопросы по одному."
        )
        return False

    async def _start_or_resume_intro_setup(self, channel, campaign: dict):
        intro_status = campaign.get("intro_status") or "not_started"
        if intro_status == "completed":
            return

        if intro_status == "not_started":
            campaign = db.set_campaign_intro_state(campaign["id"], INTRO_SETUP_STEPS[0]["status"], {})
            await channel.send(
                "🎬 Перед вступлением настроим кампанию по шагам. Я задам несколько коротких вопросов по одному сообщению."
            )
            await self._send_intro_setup_prompt(channel, campaign)
            return

        if intro_status == "intro_ready_to_generate":
            await self._generate_intro_from_answers(channel, campaign)
            return

        if intro_status == "generating_intro":
            await channel.send("🎬 Вступление уже генерируется. Подожди ещё немного.")
            return

        await channel.send("🎬 Продолжаем пошаговую настройку вступления.")
        await self._send_intro_setup_prompt(channel, campaign)

    async def _send_intro_setup_prompt(self, channel, campaign: dict):
        step = INTRO_STEP_BY_STATUS.get(campaign.get("intro_status"))
        if not step:
            return
        embed = discord.Embed(title=step["title"], description=step["prompt"], color=0xFF6B35)
        embed.set_footer(text="Ответь одним обычным сообщением в этот канал.")
        await channel.send(embed=embed)

    async def _handle_intro_setup_message(self, message, campaign: dict) -> bool:
        step = INTRO_STEP_BY_STATUS.get(campaign.get("intro_status"))
        if not step:
            return False

        content = message.content.strip()
        if not content:
            return True

        answers = dict(campaign.get("intro_answers") or {})
        answers[step["key"]] = content
        next_status = INTRO_NEXT_STATUS.get(step["status"])

        await message.channel.send(f"✅ {step['title'].split(':', 1)[-1].strip()} сохранён.")
        if next_status:
            campaign = db.set_campaign_intro_state(campaign["id"], next_status, answers)
            await self._send_intro_setup_prompt(message.channel, campaign)
            return True

        campaign = db.set_campaign_intro_state(campaign["id"], "intro_ready_to_generate", answers)
        await self._generate_intro_from_answers(message.channel, campaign)
        return True

    async def _generate_intro_from_answers(self, channel, campaign: dict):
        answers = dict(campaign.get("intro_answers") or {})
        campaign = db.set_campaign_intro_state(campaign["id"], "generating_intro", answers)
        await channel.send("🎬 Настройки сохранены. Начинаю вступление кампании.")

        chars = db.get_all_characters(campaign["id"])
        if chars:
            names = ", ".join(f"**{char['name']}**" for char in chars)
            await channel.send(f"🎲 Участники: {names}")

        async with channel.typing():
            try:
                intro = await dm.start_campaign(campaign["id"], campaign["title"], answers)
            except RuntimeError as error:
                db.set_campaign_intro_state(campaign["id"], "intro_ready_to_generate", answers)
                await channel.send(str(error))
                return

        db.set_campaign_intro_state(campaign["id"], "completed", answers)
        await self._publish_master_response(channel, campaign["id"], intro)

    async def _handle_roll_mode_setup_message(self, message, campaign: dict) -> bool:
        normalized = self._normalize_setup_choice(message.content)
        if normalized is None:
            return False

        if not self._can_configure_campaign(message.author, message.channel):
            return False

        campaign = db.set_campaign_roll_mode(campaign["id"], normalized)
        mode_text = (
            "Бот будет сам вызывать нужные проверки, кидать кубы и показывать результат в ответе Мастера."
            if normalized == "bot_auto"
            else "Игроки будут сами присылать результаты бросков, когда бот явно запросит проверку."
        )
        await message.channel.send(
            f"✅ Режим бросков для кампании **{campaign['title']}** сохранён.\n{mode_text}\n\n"
            "Теперь выбери режим прироста HP при повышении уровня:\n"
            "**1. Fixed** - всегда брать фиксированное значение Hit Die\n"
            "**2. Roll** - всегда бросать Hit Die\n"
            "**3. Choose Each Level** - выбирать fixed или roll на каждом повышении\n\n"
            "Ответь `1`, `2` или `3`."
        )
        return True

    async def _handle_hp_gain_mode_setup_message(self, message, campaign: dict) -> bool:
        normalized = SETUP_HP_GAIN_MODE_CHOICES.get(re.sub(r"\s+", "", message.content.strip().lower()))
        if normalized is None:
            return False
        if not self._can_configure_campaign(message.author, message.channel):
            return False

        campaign = db.set_campaign_hp_gain_mode(campaign["id"], normalized)
        hp_text = {
            "fixed": "На каждом уровне бот будет использовать фиксированный прирост HP.",
            "roll": "На каждом уровне бот будет использовать бросок Hit Die для прироста HP.",
            "choose_each_level": "На каждом уровне игрок сможет сам выбрать fixed или roll для прироста HP.",
        }[normalized]
        await message.channel.send(
            f"✅ Настройка кампании **{campaign['title']}** завершена.\n{hp_text}\n\n"
            "Теперь игроки могут создавать персонажей, а затем запускай приключение через `!начать_игру`."
        )
        return True

    async def _handle_direct_action(self, channel, campaign: dict, author, text: str, source_message_id: str):
        user_id = str(author.id)
        username = author.display_name
        char = db.get_character(user_id, campaign["id"])
        char_name = char["name"] if char else author.display_name
        async with channel.typing():
            try:
                response = await dm.process_action(
                    campaign["id"],
                    user_id,
                    username,
                    text,
                )
            except RuntimeError as error:
                await channel.send(str(error))
                return

        parsed_response = dm.extract_roll_request(response)
        roll_request = parsed_response["roll_request"]
        visible_text = parsed_response["text"]
        if roll_request:
            if visible_text:
                await _send_long(channel, visible_text)
            pending = db.create_pending_roll_request(
                campaign["id"],
                user_id,
                char_name,
                text,
                source_message_id,
                roll_request["dice_count"],
                roll_request["dice_sides"],
                roll_request.get("modifier_stat"),
                roll_request.get("reason"),
            )
            if campaign.get("roll_mode") == "bot_auto":
                roll_data = self._perform_auto_roll(char, char_name, roll_request)
                await channel.send(roll_data["summary"])
                await self._resolve_pending_roll_request(
                    channel,
                    campaign,
                    author,
                    pending,
                    roll_data["total"],
                    source_message_id,
                    performed_by_bot=True,
                )
                return

            await channel.send(self._format_pending_roll_prompt(pending))
            return

        await self._publish_master_response(channel, campaign["id"], visible_text or response)

    async def _handle_round_reply(self, message, campaign: dict, active_round: dict, target: dict, content: str):
        char_name, _ = self._submit_round_response(
            campaign,
            active_round,
            target,
            str(message.author.id),
            str(message.id),
            content,
        )
        await message.reply(f"✅ Ход для **{char_name}** принят.", mention_author=False)
        await self._maybe_resolve_if_complete(active_round["id"], message.channel)

    async def _resolve_pending_roll_request(
        self,
        channel,
        campaign: dict,
        author,
        pending: dict,
        total: int,
        source_message_id: str,
        performed_by_bot: bool = False,
    ):
        user_id = str(author.id)
        char = db.get_character(user_id, campaign["id"])
        char_name = char["name"] if char else author.display_name
        summary = self._format_manual_roll_summary(
            char_name,
            {
                "dice_count": pending["dice_count"],
                "dice_sides": pending["dice_sides"],
                "modifier_stat": pending.get("modifier_stat"),
                "reason": pending.get("reason"),
            },
            total,
            performed_by_bot=performed_by_bot,
        )
        enriched_action = self._build_manual_action_text(
            pending["action_text"],
            {
                "dice_count": pending["dice_count"],
                "dice_sides": pending["dice_sides"],
                "modifier_stat": pending.get("modifier_stat"),
                "reason": pending.get("reason"),
            },
            total,
        )

        if pending.get("round_id"):
            round_data = db.get_scene_round(pending["round_id"])
            if not round_data or round_data["status"] != "open":
                db.resolve_pending_roll_request(pending["id"])
                await channel.send("⌛ Этот запрос на бросок уже устарел - раунд завершён.")
                return

            selected_option = self._selected_option_for_action(round_data["options"], pending["action_text"])
            response_kind = "option" if selected_option else "free_text"
            db.resolve_pending_roll_request(pending["id"])
            db.upsert_scene_round_response(
                round_data["id"],
                user_id,
                source_message_id,
                response_kind,
                enriched_action,
                selected_option=selected_option,
            )
            await channel.send(f"{summary}\n✅ Ход для **{char_name}** дополнен броском и принят.")
            await self._maybe_resolve_if_complete(round_data["id"], channel)
            return

        db.resolve_pending_roll_request(pending["id"])
        db.resume_campaign_auto_wait(campaign["id"])
        await channel.send(summary)
        async with channel.typing():
            try:
                response = await dm.process_action(
                    campaign["id"],
                    user_id,
                    author.display_name,
                    enriched_action,
                )
            except RuntimeError as error:
                await channel.send(str(error))
                return
        await self._publish_master_response(channel, campaign["id"], response)

    async def _publish_master_response(self, channel, campaign_id: int, text: str):
        sent_messages = await _send_long(channel, text)
        source_message_id = str(sent_messages[0].id) if sent_messages else None
        await self._maybe_award_xp(channel, campaign_id, text, source_message_id)
        await self._maybe_open_round(channel, campaign_id, text)

    async def _maybe_award_xp(self, channel, campaign_id: int, assistant_text: str, source_message_id: str | None):
        if not source_message_id:
            return
        campaign = db.get_campaign(campaign_id)
        if not campaign or campaign.get("leveling_mode") != "xp_auto_ai":
            return
        if db.get_xp_award(campaign_id, source_message_id):
            return
        latest_award = db.get_latest_xp_award(campaign_id)
        if latest_award:
            created_at_raw = latest_award.get("created_at")
            try:
                latest_created_at = datetime.fromisoformat(created_at_raw)
            except (TypeError, ValueError):
                latest_created_at = None
            if latest_created_at and datetime.utcnow() - latest_created_at < timedelta(minutes=XP_AWARD_COOLDOWN_MINUTES):
                return

        analysis = await asyncio.to_thread(dm.analyze_xp_award, campaign_id, assistant_text)
        if not analysis["award_xp"] or analysis["amount"] <= 0:
            return
        analysis["amount"] = min(int(analysis["amount"]), XP_AWARD_MAX)
        if analysis["amount"] <= 0:
            return

        excluded = {item["name"].strip().lower() for item in analysis.get("excluded_characters", [])}
        chars = [char for char in db.get_all_characters(campaign_id) if char["name"].strip().lower() not in excluded]
        if not chars:
            return

        awarded = []
        notices = []
        for char in chars:
            updated = db.adjust_character_xp(char["user_id"], campaign_id, analysis["amount"])
            if not updated:
                continue
            awarded.append({"user_id": char["user_id"], "name": updated["name"], "amount": analysis["amount"]})
            progress = leveling.get_character_progress(updated)
            if progress["eligible_level"] > int(updated["level"]):
                queue_entry = db.enqueue_levelup(campaign_id, updated["user_id"], updated["name"], progress["eligible_level"])
                notices.append(
                    f"⬆️ **{updated['name']}** готов к повышению и добавлен в очередь до уровня {queue_entry['target_level']}."
                )

        if not awarded:
            return

        db.record_xp_award(
            campaign_id,
            source_message_id,
            source_message_id,
            analysis["amount"],
            analysis["reason"],
            awarded,
        )
        summary = ", ".join(f"**{item['name']}** +{item['amount']} XP" for item in awarded)
        message = f"✨ XP: {summary}."
        if analysis.get("reason"):
            message += f" {analysis['reason']}"
        if notices:
            message += "\n" + "\n".join(notices)
        await channel.send(message)

    async def _maybe_open_round(self, channel, campaign_id: int, assistant_text: str):
        if db.get_active_scene_round(campaign_id):
            return

        analysis = await asyncio.to_thread(dm.analyze_scene_response, campaign_id, assistant_text)
        if not analysis["should_open_round"] or not analysis["options"]:
            return

        runtime = db.get_campaign_runtime_state(campaign_id)
        if not runtime["auto_wait_enabled"]:
            await channel.send(
                "⏸️ Автоматическое ожидание остановлено после двух пустых раундов подряд. "
                "Используй `!продолжить_игру`, чтобы снова включить авто-ожидание."
            )
            return

        chars = db.get_all_characters(campaign_id)
        out_of_scene = {
            target["name"].strip().lower(): target.get("reason") or "Персонаж находится вне текущей сцены."
            for target in analysis["out_of_scene_characters"]
        }
        targets = []
        expected_count = 0
        for char in chars:
            normalized_name = char["name"].strip().lower()
            if normalized_name in out_of_scene:
                targets.append(
                    {
                        "user_id": char["user_id"],
                        "character_name_snapshot": char["name"],
                        "status": "out_of_scene",
                        "reason": out_of_scene[normalized_name],
                    }
                )
            else:
                expected_count += 1
                targets.append(
                    {
                        "user_id": char["user_id"],
                        "character_name_snapshot": char["name"],
                        "status": "expected",
                        "reason": None,
                    }
                )

        if expected_count == 0:
            await channel.send(
                "🎭 Сейчас в этой сцене нет персонажей, от которых нужен обязательный ответ. "
                "Продолжайте игру новым явным действием."
            )
            return

        campaign = db.get_campaign(campaign_id)
        timeout_minutes = self._round_timeout_minutes(campaign)
        deadline_at = (datetime.utcnow() + timedelta(minutes=timeout_minutes)).isoformat()
        round_id = db.create_scene_round(campaign_id, analysis["options"], deadline_at, targets)
        round_data = db.get_scene_round(round_id)
        prompt_message = await channel.send(self._format_round_prompt(round_data))
        db.set_scene_round_message_id(round_id, str(prompt_message.id))

    async def _maybe_resolve_if_complete(self, round_id: int, channel):
        if not db.get_scene_round_pending_targets(round_id):
            await self._resolve_round(round_id, channel, timed_out=False)

    async def _resolve_round(self, round_id: int, channel, timed_out: bool):
        if round_id in self._resolving_rounds:
            return

        self._resolving_rounds.add(round_id)
        try:
            round_data = db.get_scene_round(round_id)
            if not round_data or round_data["status"] != "open":
                return

            if timed_out:
                db.expire_pending_roll_requests_for_round(round_id)
                db.mark_scene_round_timeouts(round_id)
                round_data = db.get_scene_round(round_id)

            if db.get_scene_round_pending_targets(round_id):
                return

            has_answers = any(target["status"] == "answered" for target in round_data["targets"])
            if round_data["answered_count"] == 0:
                next_status = "resolved" if timed_out else "expired"
                db.finalize_scene_round(round_id, next_status)
                round_data = db.get_scene_round(round_id)
                runtime = db.record_scene_round_activity(
                    round_data["campaign_id"],
                    had_expected_reply=False,
                )
                await self._update_round_prompt_message(
                    channel,
                    round_data,
                    "⌛ Дедлайн истёк. Ответов не было."
                )
                if not runtime["auto_wait_enabled"]:
                    if channel:
                        await channel.send(
                            "⏸️ Два раунда подряд прошли без ответов. "
                            "Автоматическое ожидание остановлено. Используй `!продолжить_игру`, когда будете готовы продолжать."
                        )
                    return
                if channel:
                    async with channel.typing():
                        try:
                            response = await dm.process_scene_round(round_data["campaign_id"], round_id)
                        except RuntimeError as error:
                            await channel.send(str(error))
                            return
                    parsed_response = dm.extract_roll_request(response)
                    await self._publish_master_response(
                        channel,
                        round_data["campaign_id"],
                        parsed_response["text"] or response,
                    )
                return

            if not channel:
                db.finalize_scene_round(round_id, "resolved" if has_answers else "expired")
                db.record_scene_round_activity(
                    round_data["campaign_id"],
                    had_expected_reply=round_data["answered_count"] > 0,
                )
                return

            async with channel.typing():
                try:
                    response = await dm.process_scene_round(round_data["campaign_id"], round_id)
                except RuntimeError as error:
                    await channel.send(str(error))
                    return

            parsed_response = dm.extract_roll_request(response)
            roll_request = parsed_response["roll_request"]
            visible_text = parsed_response["text"]

            if roll_request:
                target_response = self._resolve_round_roll_target(round_data, roll_request)
                if target_response is None:
                    db.finalize_scene_round(round_id, "resolved")
                    await self._update_round_prompt_message(
                        channel,
                        db.get_scene_round(round_id),
                        "🎲 Раунд закрыт. Ожидается отдельный бросок."
                    )
                    db.record_scene_round_activity(
                        round_data["campaign_id"],
                        had_expected_reply=round_data["answered_count"] > 0,
                    )
                    await self._publish_master_response(channel, round_data["campaign_id"], visible_text or response)
                    return

                target_info, stored_response = target_response
                if visible_text:
                    await _send_long(channel, visible_text)

                pending = db.create_pending_roll_request(
                    round_data["campaign_id"],
                    target_info["user_id"],
                    target_info["character_name_snapshot"],
                    stored_response["content"],
                    stored_response["message_id"] or round_data.get("message_id") or str(channel.id),
                    roll_request["dice_count"],
                    roll_request["dice_sides"],
                    roll_request.get("modifier_stat"),
                    roll_request.get("reason"),
                    round_id=round_id,
                )
                await self._update_round_prompt_message(
                    channel,
                    db.get_scene_round(round_id),
                    "🎲 Раунд закрыт. Ожидается отдельный бросок."
                )
                if db.get_campaign(round_data["campaign_id"]).get("roll_mode") == "bot_auto":
                    author = channel.guild.get_member(int(target_info["user_id"])) if getattr(channel, "guild", None) else None
                    if author is None:
                        try:
                            author = await self.bot.fetch_user(int(target_info["user_id"]))
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            author = None
                    if author is None:
                        await channel.send(self._format_pending_roll_prompt(pending))
                        return

                    char = db.get_character(target_info["user_id"], round_data["campaign_id"])
                    char_name = char["name"] if char else target_info["character_name_snapshot"]
                    roll_data = self._perform_auto_roll(char, char_name, roll_request)
                    await channel.send(roll_data["summary"])
                    await self._resolve_pending_roll_request(
                        channel,
                        db.get_campaign(round_data["campaign_id"]),
                        author,
                        pending,
                        roll_data["total"],
                        stored_response["message_id"] or round_data.get("message_id") or str(channel.id),
                        performed_by_bot=True,
                    )
                    return

                await channel.send(self._format_pending_roll_prompt(pending))
                return

            db.finalize_scene_round(round_id, "resolved" if has_answers else "expired")
            await self._update_round_prompt_message(
                channel,
                db.get_scene_round(round_id),
                "✅ Раунд закрыт."
            )
            db.record_scene_round_activity(
                round_data["campaign_id"],
                had_expected_reply=round_data["answered_count"] > 0,
            )
            await self._publish_master_response(channel, round_data["campaign_id"], visible_text or response)
        finally:
            self._resolving_rounds.discard(round_id)

    def _resolve_round_roll_target(self, round_data: dict, roll_request: dict):
        responses_by_user = {response["user_id"]: response for response in round_data["responses"]}
        answered_targets = [
            target
            for target in round_data["targets"]
            if target["status"] == "answered" and target["user_id"] in responses_by_user
        ]
        requested_name = (roll_request.get("target_name") or "").strip().lower()
        if requested_name:
            for target in answered_targets:
                if target["character_name_snapshot"].strip().lower() == requested_name:
                    return target, responses_by_user[target["user_id"]]
        if len(answered_targets) == 1:
            target = answered_targets[0]
            return target, responses_by_user[target["user_id"]]
        return None

    async def _explain_round_restriction(self, ctx, campaign: dict, active_round: dict):
        target = db.get_scene_round_target(active_round["id"], str(ctx.author.id))
        pending = db.get_open_pending_roll_request(campaign["id"], str(ctx.author.id))
        if target and target["status"] == "out_of_scene":
            await ctx.send(
                f"🧭 **{target['character_name_snapshot']}** сейчас вне этой сцены, "
                "поэтому действие не повлияет на текущий эпизод."
            )
            return

        if pending and pending.get("round_id") == active_round["id"]:
            await ctx.send(
                "🎲 На твой ход уже запрошен бросок. Пришли итог числом через `!бросок 17` "
                "или ответь реплаем с пометкой вроде `(бросок 17)`."
            )
            return

        if target and target["status"] in {"expected", "answered"}:
            await ctx.send(
                "⏳ Сейчас открыт общий раунд выбора. Ответь **реплаем на сообщение бота с вариантами** "
                "или используй `!д <номер>` / `!д <своё действие>`, чтобы твой ход попал в общий резолв."
            )
            return

        await ctx.send("ℹ️ Сейчас открыт групповой раунд. Дождись завершения текущего выбора.")

    def _is_character_creation_message(self, message) -> bool:
        character_cog = self.bot.get_cog("CharacterCog")
        if not character_cog or not hasattr(character_cog, "has_active_creation_session"):
            return False
        return bool(character_cog.has_active_creation_session(str(message.channel.id), str(message.author.id)))

    async def _get_channel(self, channel_id: str):
        channel = self.bot.get_channel(int(channel_id))
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _update_round_prompt_message(self, channel, round_data: dict | None, status_text: str):
        if not channel or not round_data or not round_data.get("bot_message_id"):
            return
        try:
            message = await channel.fetch_message(int(round_data["bot_message_id"]))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            return
        try:
            await message.edit(content=self._format_round_prompt(round_data, status_text=status_text))
        except discord.HTTPException:
            return

    def _format_round_prompt(self, round_data: dict, status_text: str | None = None) -> str:
        expected_targets = [target for target in round_data["targets"] if target["status"] == "expected"]
        out_of_scene_targets = [target for target in round_data["targets"] if target["status"] == "out_of_scene"]
        mentions = " ".join(f"<@{target['user_id']}>" for target in expected_targets)

        if status_text:
            first_line = "⌛ **Раунд выбора закрыт**"
            second_line = status_text
        else:
            first_line = "⏳ **Раунд выбора открыт**"
            second_line = f"Жду ответы от: {mentions}" if mentions else "Жду ответы от участников сцены."

        lines = [first_line, second_line]
        if not status_text:
            lines.extend(
                [
                    f"Дедлайн: {self._format_deadline(round_data['deadline_at'])}",
                    "Ответьте **реплаем на это сообщение**: числом `1-5` или свободным текстом.",
                    "",
                    "**Варианты действий:**",
                ]
            )
        else:
            lines.extend(
                [
                    "Новые ответы на этот раунд уже не принимаются.",
                    "",
                    "**Последние варианты действий:**",
                ]
            )
        lines.extend(f"{index}. {option}" for index, option in enumerate(round_data["options"], start=1))
        if out_of_scene_targets:
            lines.append("")
            lines.append("**Сейчас вне сцены:**")
            for target in out_of_scene_targets:
                reason = target.get("reason") or "Находится слишком далеко от текущего эпизода."
                lines.append(f"- {target['character_name_snapshot']} - {reason}")
        return "\n".join(lines)

    def _format_deadline(self, deadline_at: str) -> str:
        deadline = datetime.fromisoformat(deadline_at).replace(tzinfo=timezone.utc)
        return f"<t:{int(deadline.timestamp())}:R>"

    def _round_timeout_minutes(self, campaign: dict | None) -> int:
        if not campaign:
            return ROUND_TIMEOUT_MINUTES
        try:
            value = int(campaign.get("scene_round_timeout_minutes") or ROUND_TIMEOUT_MINUTES)
        except (TypeError, ValueError):
            return ROUND_TIMEOUT_MINUTES
        return min(max(value, 1), 120)

    def _is_command_message(self, text: str) -> bool:
        prefix = self.bot.command_prefix
        if isinstance(prefix, str):
            return text.startswith(prefix)
        if isinstance(prefix, (list, tuple)):
            return any(text.startswith(item) for item in prefix if isinstance(item, str))
        return text.startswith("!")

    def _can_configure_campaign(self, author, channel) -> bool:
        if getattr(author.guild_permissions, "manage_channels", False):
            return True
        campaign = db.get_active_campaign(str(channel.id))
        return bool(campaign and str(author.id) == str(campaign.get("setup_owner_user_id")))

    def _normalize_setup_choice(self, text: str) -> str | None:
        normalized = re.sub(r"\s+", "", text.strip().lower())
        return SETUP_ROLL_MODE_CHOICES.get(normalized)

    def _parse_round_reply(self, content: str, options: list[str]) -> dict:
        stripped = content.strip()
        match = re.match(r"^(\d+)\b(.*)$", stripped)
        if match:
            index = int(match.group(1))
            if 1 <= index <= len(options):
                return {
                    "selected_option": index,
                    "response_kind": "option",
                    "action_text": options[index - 1],
                }
        return {
            "selected_option": None,
            "response_kind": "free_text",
            "action_text": stripped,
        }

    def _submit_round_response(
        self,
        campaign: dict,
        active_round: dict,
        target: dict,
        user_id: str,
        source_message_id: str,
        content: str,
    ) -> tuple[str, dict]:
        char_name = target.get("character_name_snapshot") or user_id
        parsed = self._parse_round_reply(content, active_round["options"])
        db.cancel_open_pending_roll_requests(campaign["id"], user_id)
        db.upsert_scene_round_response(
            active_round["id"],
            user_id,
            source_message_id,
            parsed["response_kind"],
            parsed["action_text"],
            selected_option=parsed["selected_option"],
        )
        return char_name, parsed

    def _extract_inline_roll_total(self, text: str) -> int | None:
        match = INLINE_ROLL_RE.search(text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _extract_manual_roll_total(self, text: str) -> int | None:
        match = MANUAL_TOTAL_RE.match(text.strip())
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _parse_dice_notation(self, text: str) -> dict | None:
        normalized = text.strip().lower().replace("д", "d").replace("к", "d")
        modifier_name = None
        for stat_name, stat_key in game_data.get_stat_lookup_map().items():
            if f"+{stat_name}" in normalized:
                normalized = normalized.split("+")[0].strip()
                modifier_name = stat_key
                break

        match = DICE_NOTATION_RE.match(normalized)
        if not match:
            return None

        try:
            count = int(match.group(1)) if match.group(1) else 1
            sides = int(match.group(2))
        except ValueError:
            return None

        return {
            "count": min(max(count, 1), 20),
            "sides": min(max(sides, 2), 100),
            "modifier_name": modifier_name,
            "notation": f"{count if count else 1}d{sides}",
        }

    def _perform_standard_roll(self, char: dict | None, char_name: str, dice_spec: dict) -> dict:
        return self._perform_roll(
            char,
            char_name,
            dice_spec["count"],
            dice_spec["sides"],
            dice_spec.get("modifier_name"),
        )

    def _perform_bot_requested_roll(self, char: dict | None, char_name: str, pending: dict) -> dict:
        return self._perform_roll(
            char,
            char_name,
            pending["dice_count"],
            pending["dice_sides"],
            pending.get("modifier_stat"),
        )

    def _perform_roll(self, char: dict | None, char_name: str, count: int, sides: int, modifier_name: str | None) -> dict:
        rolls = [random.randint(1, sides) for _ in range(count)]
        raw_total = sum(rolls)
        modifier_value = self._character_modifier(char, modifier_name) if modifier_name else 0
        total = raw_total + modifier_value
        stat_label = game_data.get_stat_label(modifier_name) if modifier_name else None
        roll_str = " + ".join(str(roll) for roll in rolls)
        crit_text = ""
        if sides == 20 and count == 1:
            if rolls[0] == 20:
                crit_text = "\n🌟 **КРИТИЧЕСКИЙ УСПЕХ!**"
            elif rolls[0] == 1:
                crit_text = "\n💀 **КРИТИЧЕСКИЙ ПРОВАЛ!**"
        title_suffix = f" + {stat_label} ({modifier_value:+d})" if stat_label else ""
        summary = self._format_roll_summary(char_name, count, sides, total, raw_total, modifier_value, stat_label, roll_str)
        return {
            "summary": summary,
            "total": total,
            "raw_total": raw_total,
            "modifier_value": modifier_value,
            "rolls": rolls,
            "stat_label": stat_label,
            "count": count,
            "sides": sides,
            "modifier_name": modifier_name,
            "modifier_text": title_suffix,
            "roll_str": roll_str,
            "crit_text": crit_text,
        }

    def _build_roll_embed(self, roll_data: dict, char_name: str | None = None):
        color = 0xFFD700 if not roll_data["crit_text"] else (0x00FF00 if "УСПЕХ" in roll_data["crit_text"] else 0xFF0000)
        embed = discord.Embed(
            title=f"🎲 Бросок {roll_data['count']}d{roll_data['sides']}{roll_data['modifier_text']}",
            color=color,
        )
        embed.add_field(
            name="Результат",
            value=f"{roll_data['roll_str']} = **{roll_data['total']}**{roll_data['crit_text']}",
            inline=False,
        )
        if char_name:
            embed.set_footer(text=f"Персонаж: {char_name}")
        return embed

    def _perform_auto_roll(self, char: dict | None, char_name: str, analysis: dict) -> dict:
        return self._perform_roll(
            char,
            char_name,
            analysis["dice_count"],
            analysis["dice_sides"],
            analysis.get("modifier_stat"),
        )

    def _format_roll_summary(
        self,
        char_name: str,
        count: int,
        sides: int,
        total: int,
        raw_total: int,
        modifier_value: int,
        stat_label: str | None,
        roll_str: str,
    ) -> str:
        if stat_label:
            sign = "+" if modifier_value >= 0 else "-"
            return f"🎲 {char_name}: {count}d{sides} + {stat_label} = {total} ({roll_str} {sign} {abs(modifier_value)})"
        return f"🎲 {char_name}: {count}d{sides} = {total} ({roll_str})"

    def _character_modifier(self, char: dict | None, modifier_stat: str | None) -> int:
        if not char or not modifier_stat or modifier_stat not in game_data.get_stat_key_set():
            return 0
        return (char[modifier_stat] - 10) // 2

    def _format_roll_notation(self, dice_count: int, dice_sides: int, modifier_stat: str | None) -> str:
        notation = f"{dice_count}d{dice_sides}"
        if modifier_stat:
            notation += f" + {game_data.get_stat_label(modifier_stat)}"
        return notation

    def _format_pending_roll_prompt(self, pending: dict) -> str:
        notation = self._format_roll_notation(
            pending["dice_count"],
            pending["dice_sides"],
            pending.get("modifier_stat"),
        )
        dice_only = self._format_roll_notation(pending["dice_count"], pending["dice_sides"], None)
        reason = pending.get("reason") or "\u041d\u0443\u0436\u043d\u0430 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0438\u0441\u0445\u043e\u0434\u0430 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044f."
        return (
            f"\U0001f3b2 \u0414\u043b\u044f **{pending['character_name_snapshot']}** \u043d\u0443\u0436\u0435\u043d \u0431\u0440\u043e\u0441\u043e\u043a **{notation}**.\\n"
            f"\u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason}\\n"
            f"\u041c\u043e\u0436\u0435\u0448\u044c \u0431\u0440\u043e\u0441\u0438\u0442\u044c \u0441\u0430\u043c \u0438 \u043f\u0440\u0438\u0441\u043b\u0430\u0442\u044c \u0438\u0442\u043e\u0433 \u0447\u0438\u0441\u043b\u043e\u043c \u0447\u0435\u0440\u0435\u0437 `!\u0431\u0440\u043e\u0441\u043e\u043a 17` \u0438\u043b\u0438 reply \u0441 `(\u0431\u0440\u043e\u0441\u043e\u043a 17)`.\\n"
            f"\u0418\u043b\u0438 \u043f\u043e\u043f\u0440\u043e\u0441\u0438\u0442\u044c \u0431\u043e\u0442\u0430 \u0431\u0440\u043e\u0441\u0438\u0442\u044c \u0437\u0430 \u0442\u0435\u0431\u044f: `!\u0431\u0440\u043e\u0441\u043e\u043a {dice_only}` / `!\u0431\u0440\u043e\u0441\u043e\u043a {dice_only.replace('d', '\u0434')}`."
        )

    def _format_manual_roll_summary(self, char_name: str, analysis: dict, total: int, performed_by_bot: bool = False) -> str:
        notation = self._format_roll_notation(
            analysis["dice_count"],
            analysis["dice_sides"],
            analysis.get("modifier_stat"),
        )
        prefix = "\u0431\u043e\u0442 \u0431\u0440\u043e\u0441\u0438\u043b" if performed_by_bot else "\u0440\u0443\u0447\u043d\u043e\u0439 \u0431\u0440\u043e\u0441\u043e\u043a"
        return f"\U0001f3b2 {char_name}: {prefix} {notation} = {total}"

    def _build_auto_action_text(self, action_text: str, analysis: dict, roll_data: dict) -> str:
        notation = self._format_roll_notation(
            analysis["dice_count"],
            analysis["dice_sides"],
            analysis.get("modifier_stat"),
        )
        details = f"{notation} = {roll_data['total']}"
        if roll_data["stat_label"]:
            details += f" ({roll_data['raw_total']} и модификатор {roll_data['modifier_value']:+d})"
        return (
            f"{action_text}\n"
            f"[Автоматический бросок бота: {details}. Причина: {analysis.get('reason') or 'Нужна проверка исхода действия.'}]"
        )

    def _build_manual_action_text(self, action_text: str, analysis: dict, total: int) -> str:
        notation = self._format_roll_notation(
            analysis["dice_count"],
            analysis["dice_sides"],
            analysis.get("modifier_stat"),
        )
        return (
            f"{action_text}\n"
            f"[Ручной бросок игрока: {notation} = {total}. Причина: {analysis.get('reason') or 'Нужна проверка исхода действия.'}]"
        )

    def _selected_option_for_action(self, options: list[str], action_text: str) -> int | None:
        normalized = action_text.strip().lower()
        for index, option in enumerate(options, start=1):
            if option.strip().lower() == normalized:
                return index
        return None

    def _funny_auto_roll_rejection(self) -> str:
        return random.choice(AUTO_ROLL_REJECTION_REACTIONS)

    def _funny_no_pending_roll_reaction(self) -> str:
        return random.choice(MANUAL_NO_PENDING_REACTIONS)


async def _send_long(target, text: str):
    messages = []
    if len(text) <= 2000:
        messages.append(await target.send(text))
        return messages

    remaining = text
    while remaining:
        if len(remaining) <= 1900:
            chunk = remaining
            remaining = ""
        else:
            split_at = remaining.rfind("\n\n", 0, 1900)
            if split_at < 1200:
                split_at = remaining.rfind("\n", 0, 1900)
            if split_at < 800:
                split_at = remaining.rfind(" ", 0, 1900)
            if split_at < 1:
                split_at = 1900
            chunk = remaining[:split_at].rstrip()
            remaining = remaining[split_at:].lstrip()
        if chunk:
            messages.append(await target.send(chunk))
    return messages


async def setup(bot):
    await bot.add_cog(GameCog(bot))
