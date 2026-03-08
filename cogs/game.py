import random
import re
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

import database as db
import dungeon_master as dm
import game_data

ROUND_TIMEOUT_MINUTES = 5
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

    @commands.command(name="новая_кампания", aliases=["new_campaign", "старт", "нк", "nc"])
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

    @commands.command(name="начать_игру", aliases=["start_game", "play", "ни", "sg"])
    async def start_game(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании. Используй `!новая_кампания`.")
            return
        if not await self._ensure_campaign_ready(ctx, campaign):
            return

        chars = db.get_all_characters(campaign["id"])
        if not chars:
            await ctx.send("❌ Никто ещё не создал персонажа! Используй `!создать_персонажа`.")
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

    @commands.command(name="завершить_кампанию", aliases=["end_campaign", "зк", "ec"])
    @commands.has_permissions(manage_channels=True)
    async def end_campaign(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return
        db.end_campaign(campaign["id"])
        await ctx.send(f"📕 Кампания **{campaign['title']}** завершена. История сохранена.")

    # Actions

    @commands.command(name="д", aliases=["действие", "action", "do"])
    async def action(self, ctx, *, text: str):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return
        if not await self._ensure_campaign_ready(ctx, campaign):
            return

        active_round = db.get_active_scene_round(campaign["id"])
        if active_round:
            await self._explain_round_restriction(ctx, campaign, active_round)
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

    @commands.command(name="бросок", aliases=["roll", "кубик", "бр", "r"])
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

    @commands.command(name="статус_хода", aliases=["turn_status", "сх", "ts"])
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

    @commands.command(name="пропустить", aliases=["skip_turn", "пх", "sk"])
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

    @commands.command(name="закрыть_ход", aliases=["close_turn", "зх", "ct"])
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

    @commands.command(name="продолжить_игру", aliases=["resume_game", "пг", "rg"])
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

    @commands.command(name="история", aliases=["summary", "лор", "ис", "sy"])
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

    @commands.command(name="игроки", aliases=["players", "партия", "иг", "pl"])
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
                name=f"{char['name']} ({game_data.get_race_label(char['race'])} {game_data.get_class_label(char['class'])})",
                value=f"❤️ `{hp_bar}` {char['hp']}/{char['max_hp']} HP  |  ⭐ Ур.{char['level']}  |  💰 {char['gold']}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="помощь_днд", aliases=["dnd_help", "команды", "х", "hd"])
    async def dnd_help(self, ctx):
        embed = discord.Embed(
            title="📚 Команды D&D бота",
            description="Можно использовать префикс `!` или упоминание бота: `@бот команда`.",
            color=0x5865F2,
        )
        embed.add_field(
            name="🗺️ Кампания",
            value=(
                "`!новая_кампания [название]` - начать кампанию и выбрать режим бросков\n"
                "`!начать_игру` - запустить вступление\n"
                "`!завершить_кампанию` - завершить кампанию\n"
                "`!история` - резюме приключения"
            ),
            inline=False,
        )
        embed.add_field(
            name="🧝 Персонаж",
            value=(
                "`!создать_персонажа` - создать персонажа\n"
                "`!персонаж` - лист персонажа\n"
                "`!инвентарь` - посмотреть инвентарь\n"
                "`!взять <предмет>` - добавить предмет\n"
                "`!выбросить <предмет>` - удалить предмет"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚔️ Игра",
            value=(
                "`!д <текст>` - совершить действие вне активного окна выбора\n"
                "`!бросок [кубик/итог]` - в auto-режиме вернёт шутливый отказ, в manual-режиме примет `d20/д20/к20`, `2d6/2д6/2к6` или итог броска по запросу бота\n"
                "`!статус_хода` - статус текущего раунда\n"
                "`!пропустить` - пропустить свой ход\n"
                "`!закрыть_ход` - закрыть раунд досрочно (модератор)\n"
                "`!продолжить_игру` - снять паузу после пустых раундов"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    # Message listener

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        campaign = db.get_active_campaign(str(message.channel.id))
        if campaign and campaign.get("setup_status") != "ready":
            if await self._handle_roll_mode_setup_message(message, campaign):
                return

        if self._is_command_message(message.content):
            return

        if not campaign:
            return
        if campaign.get("setup_status") != "ready":
            await message.reply(
                "⚙️ Сначала заверши настройку кампании и выбери режим бросков: ответь `1` или `2`.",
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
        if campaign.get("setup_status") == "ready":
            return True
        await ctx.send(
            "⚙️ Кампания ещё не настроена. Ответь `1` или `2` на сообщение о выборе режима бросков."
        )
        return False

    async def _handle_roll_mode_setup_message(self, message, campaign: dict) -> bool:
        normalized = self._normalize_setup_choice(message.content)
        if normalized is None:
            return False

        if not self._can_configure_campaign(message.author, message.channel):
            return False

        campaign = db.set_campaign_roll_mode(campaign["id"], normalized)
        mode_text = (
            "🎲 Бот будет сам кидать кубики и сразу показывать результат."
            if normalized == "bot_auto"
            else "🧑‍🎲 Игроки будут бросать кубики сами, а бот станет ждать результат только по запросу."
        )
        await message.channel.send(
            f"✅ Режим бросков для кампании **{campaign['title']}** выбран.\n{mode_text}\n\n"
            "Теперь каждый игрок может создать персонажа, а затем запустить игру командой `!начать_игру`."
        )
        return True

    async def _handle_direct_action(self, channel, campaign: dict, author, text: str, source_message_id: str):
        user_id = str(author.id)
        username = author.display_name
        char = db.get_character(user_id, campaign["id"])
        char_name = char["name"] if char else author.display_name
        analysis = dm.analyze_action_roll(campaign["id"], user_id, username, text)
        inline_roll_total = self._extract_inline_roll_total(text)

        if campaign.get("roll_mode") == "player_manual" and analysis["needs_roll"]:
            if inline_roll_total is None:
                pending = db.create_pending_roll_request(
                    campaign["id"],
                    user_id,
                    char_name,
                    text,
                    source_message_id,
                    analysis["dice_count"],
                    analysis["dice_sides"],
                    analysis.get("modifier_stat"),
                    analysis.get("reason"),
                )
                await channel.send(self._format_pending_roll_prompt(pending))
                return

            db.cancel_open_pending_roll_requests(campaign["id"], user_id)
            enriched_action = self._build_manual_action_text(text, analysis, inline_roll_total)
            await channel.send(self._format_manual_roll_summary(char_name, analysis, inline_roll_total))
            async with channel.typing():
                try:
                    response = await dm.process_action(
                        campaign["id"],
                        user_id,
                        username,
                        enriched_action,
                    )
                except RuntimeError as error:
                    await channel.send(str(error))
                    return
            await self._publish_master_response(channel, campaign["id"], response)
            return

        if campaign.get("roll_mode") == "bot_auto" and analysis["needs_roll"]:
            roll_data = self._perform_auto_roll(char, char_name, analysis)
            await channel.send(roll_data["summary"])
            action_text = self._build_auto_action_text(text, analysis, roll_data)
        else:
            action_text = text

        async with channel.typing():
            try:
                response = await dm.process_action(
                    campaign["id"],
                    user_id,
                    username,
                    action_text,
                )
            except RuntimeError as error:
                await channel.send(str(error))
                return

        await self._publish_master_response(channel, campaign["id"], response)

    async def _handle_round_reply(self, message, campaign: dict, active_round: dict, target: dict, content: str):
        user_id = str(message.author.id)
        username = message.author.display_name
        char = db.get_character(user_id, campaign["id"])
        char_name = char["name"] if char else message.author.display_name
        parsed = self._parse_round_reply(content, active_round["options"])
        analysis = dm.analyze_action_roll(campaign["id"], user_id, username, parsed["action_text"])
        inline_roll_total = self._extract_inline_roll_total(content)

        if campaign.get("roll_mode") == "player_manual" and analysis["needs_roll"]:
            if inline_roll_total is None:
                pending = db.create_pending_roll_request(
                    campaign["id"],
                    user_id,
                    char_name,
                    parsed["action_text"],
                    str(message.id),
                    analysis["dice_count"],
                    analysis["dice_sides"],
                    analysis.get("modifier_stat"),
                    analysis.get("reason"),
                    round_id=active_round["id"],
                )
                await message.reply(self._format_pending_roll_prompt(pending), mention_author=False)
                return

            db.cancel_open_pending_roll_requests(campaign["id"], user_id)
            stored_content = self._build_manual_action_text(parsed["action_text"], analysis, inline_roll_total)
            db.upsert_scene_round_response(
                active_round["id"],
                user_id,
                str(message.id),
                parsed["response_kind"],
                stored_content,
                selected_option=parsed["selected_option"],
            )
            await message.reply(
                f"{self._format_manual_roll_summary(char_name, analysis, inline_roll_total)}\n✅ Ход для **{char_name}** принят.",
                mention_author=False,
            )
            await self._maybe_resolve_if_complete(active_round["id"], message.channel)
            return

        if campaign.get("roll_mode") == "bot_auto" and analysis["needs_roll"]:
            roll_data = self._perform_auto_roll(char, char_name, analysis)
            stored_content = self._build_auto_action_text(parsed["action_text"], analysis, roll_data)
            db.upsert_scene_round_response(
                active_round["id"],
                user_id,
                str(message.id),
                parsed["response_kind"],
                stored_content,
                selected_option=parsed["selected_option"],
            )
            await message.reply(
                f"{roll_data['summary']}\n✅ Ход для **{char_name}** принят.",
                mention_author=False,
            )
            await self._maybe_resolve_if_complete(active_round["id"], message.channel)
            return

        db.cancel_open_pending_roll_requests(campaign["id"], user_id)
        db.upsert_scene_round_response(
            active_round["id"],
            user_id,
            str(message.id),
            parsed["response_kind"],
            parsed["action_text"],
            selected_option=parsed["selected_option"],
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
        await _send_long(channel, text)
        await self._maybe_open_round(channel, campaign_id, text)

    async def _maybe_open_round(self, channel, campaign_id: int, assistant_text: str):
        if db.get_active_scene_round(campaign_id):
            return

        analysis = dm.analyze_scene_response(campaign_id, assistant_text)
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

        deadline_at = (datetime.utcnow() + timedelta(minutes=ROUND_TIMEOUT_MINUTES)).isoformat()
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
            final_status = "resolved" if has_answers else "expired"
            db.finalize_scene_round(round_id, final_status)
            round_data = db.get_scene_round(round_id)
            runtime = db.record_scene_round_activity(
                round_data["campaign_id"],
                had_expected_reply=round_data["answered_count"] > 0,
            )

            if round_data["answered_count"] == 0:
                if channel:
                    await channel.send("⌛ Раунд завершён без ответов игроков.")
                    if not runtime["auto_wait_enabled"]:
                        await channel.send(
                            "⏸️ Два раунда подряд прошли без ответов. "
                            "Автоматическое ожидание остановлено. Используй `!продолжить_игру`, когда будете готовы продолжать."
                        )
                return

            if not channel:
                return

            async with channel.typing():
                try:
                    response = await dm.process_scene_round(round_data["campaign_id"], round_id)
                except RuntimeError as error:
                    await channel.send(str(error))
                    return

            await self._publish_master_response(channel, round_data["campaign_id"], response)
        finally:
            self._resolving_rounds.discard(round_id)

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
                "числом `1-5` или своим текстом, чтобы твой ход попал в общий резолв."
            )
            return

        await ctx.send("ℹ️ Сейчас открыт групповой раунд. Дождись завершения текущего выбора.")

    async def _get_channel(self, channel_id: str):
        channel = self.bot.get_channel(int(channel_id))
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    def _format_round_prompt(self, round_data: dict) -> str:
        expected_targets = [target for target in round_data["targets"] if target["status"] == "expected"]
        out_of_scene_targets = [target for target in round_data["targets"] if target["status"] == "out_of_scene"]
        mentions = " ".join(f"<@{target['user_id']}>" for target in expected_targets)

        lines = [
            "⏳ **Раунд выбора открыт**",
            f"Жду ответы от: {mentions}",
            f"Дедлайн: {self._format_deadline(round_data['deadline_at'])}",
            "Ответьте **реплаем на это сообщение**: числом `1-5` или свободным текстом.",
            "",
            "**Варианты действий:**",
        ]
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

    for index in range(0, len(text), 1900):
        messages.append(await target.send(text[index:index + 1900]))
    return messages


async def setup(bot):
    await bot.add_cog(GameCog(bot))


















