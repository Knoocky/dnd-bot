import random
from datetime import datetime, timedelta

import discord
from discord.ext import commands

import database as db
import game_data
import leveling

_LEVELUP_TIMEOUT = timedelta(minutes=15)


class LevelingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="уровень", aliases=["лвл", "опыт"])
    async def level_status(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"])
        if not char:
            await ctx.send("❌ У тебя нет персонажа. Используй `!создать_персонажа`.")
            return

        char = await self._ensure_build(char, campaign)
        progress = leveling.get_character_progress(char)
        queue_entry = db.get_user_levelup_queue_entry(campaign["id"], str(ctx.author.id))
        next_text = "максимум" if progress["next"] is None else str(progress["next"])
        status = self._queue_status_label(progress, queue_entry)
        await ctx.send(
            f"📈 **{char['name']}**: ур. **{progress['current_level']}**, XP **{char['exp']}** / **{next_text}**.\n"
            f"До следующего уровня: **{progress['remaining']}** XP.\n"
            f"Статус: **{status}**."
        )

    @commands.command(name="левелап", aliases=["уровень_ап", "лу"])
    async def start_levelup(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"])
        if not char:
            await ctx.send("❌ У тебя нет персонажа.")
            return
        char = await self._ensure_build(char, campaign)

        session = await self._get_or_prepare_session(ctx, campaign, char)
        if not session:
            return
        await self._send_levelup_prompt(ctx, campaign, char, session)

    @commands.command(name="выбрать", aliases=["выб"])
    async def choose_levelup_option(self, ctx, *, value: str):
        session, campaign, char = await self._require_owned_session(ctx)
        if not session:
            return

        steps = session["state"].get("steps", [])
        step_index = int(session["state"].get("step_index", 0))
        if step_index >= len(steps):
            await self._finalize_level(ctx, campaign, char, session)
            return

        step = steps[step_index]
        try:
            await self._apply_step_choice(ctx, campaign, char, session, step, value)
        except ValueError as error:
            await ctx.send(str(error))
            return

        session = db.get_active_levelup_session(campaign["id"])
        if not session:
            return
        updated_char = db.get_character(str(ctx.author.id), campaign["id"])
        await self._send_levelup_prompt(ctx, campaign, updated_char, session)

    @commands.command(name="отмена_левелапа", aliases=["ол"])
    async def cancel_levelup(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        session = db.get_active_levelup_session(campaign["id"])
        if not session:
            await ctx.send("ℹ️ Сейчас нет активного повышения уровня.")
            return
        if session["user_id"] != str(ctx.author.id):
            await ctx.send(
                f"❌ Сейчас левелап проходит <@{session['user_id']}>. Отменить его может только владелец."
            )
            return

        queue_id = session["state"].get("queue_id")
        if queue_id:
            db.release_levelup_queue_entry(queue_id)
        db.delete_levelup_session(campaign["id"])
        await ctx.send("🧹 Сессия повышения уровня остановлена. Персонаж остаётся в очереди и может продолжить позже через `!левелап`.")

    async def _ensure_build(self, char: dict, campaign: dict) -> dict:
        normalized, updates = leveling.ensure_character_build(char)
        if updates:
            db.update_character(char["user_id"], campaign["id"], updates)
            normalized = db.get_character(char["user_id"], campaign["id"])
        return normalized

    async def _get_or_prepare_session(self, ctx, campaign: dict, char: dict):
        session = db.get_active_levelup_session(campaign["id"])
        if session and self._session_is_stale(session):
            queue_id = session["state"].get("queue_id")
            if queue_id:
                db.release_levelup_queue_entry(queue_id)
            db.delete_levelup_session(campaign["id"])
            session = None

        user_id = str(ctx.author.id)
        if session:
            if session["user_id"] != user_id:
                await ctx.send(
                    f"⏳ Сейчас повышение уровня проходит <@{session['user_id']}>. Дождись завершения текущего мастера."
                )
                return None
            db.touch_levelup_session(campaign["id"])
            return db.get_active_levelup_session(campaign["id"])

        progress = leveling.get_character_progress(char)
        if not progress["ready"]:
            await ctx.send("ℹ️ У персонажа пока недостаточно XP для нового уровня.")
            return None

        queue_entry = db.enqueue_levelup(campaign["id"], user_id, char["name"], progress["eligible_level"])
        pending_queue = db.get_levelup_queue(campaign["id"], ("pending", "active"))
        if not pending_queue or pending_queue[0]["user_id"] != user_id:
            first = pending_queue[0]
            await ctx.send(
                f"⏳ В очереди на повышение первым сейчас идёт **{first['character_name_snapshot']}**. "
                f"Твой персонаж добавлен в очередь и сможет начать позже."
            )
            return None

        db.activate_levelup_queue_entry(queue_entry["id"])
        state = self._build_session_state(char, campaign, queue_entry)
        db.upsert_levelup_session(campaign["id"], user_id, char["name"], state)
        return db.get_active_levelup_session(campaign["id"])

    def _build_session_state(self, char: dict, campaign: dict, queue_entry: dict) -> dict:
        progress = leveling.get_character_progress(char)
        steps = leveling.build_level_steps(char, campaign)
        return {
            "queue_id": queue_entry["id"],
            "target_level": progress["eligible_level"],
            "pending_level": int(char["level"]) + 1,
            "steps": steps,
            "step_index": 0,
        }

    async def _require_owned_session(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return None, None, None
        session = db.get_active_levelup_session(campaign["id"])
        if not session:
            await ctx.send("❌ Сейчас нет активного мастера повышения уровня. Используй `!левелап`.")
            return None, campaign, None
        if self._session_is_stale(session):
            queue_id = session["state"].get("queue_id")
            if queue_id:
                db.release_levelup_queue_entry(queue_id)
            db.delete_levelup_session(campaign["id"])
            await ctx.send("⌛ Сессия повышения уровня истекла. Запусти `!левелап` заново.")
            return None, campaign, None
        if session["user_id"] != str(ctx.author.id):
            await ctx.send(
                f"⏳ Сейчас повышение уровня проходит <@{session['user_id']}>. Дождись своей очереди."
            )
            return None, campaign, None
        db.touch_levelup_session(campaign["id"])
        char = db.get_character(str(ctx.author.id), campaign["id"])
        return db.get_active_levelup_session(campaign["id"]), campaign, char

    def _session_is_stale(self, session: dict) -> bool:
        try:
            last_activity = datetime.fromisoformat(session["last_activity_at"])
        except (KeyError, ValueError, TypeError):
            return False
        return datetime.utcnow() - last_activity > _LEVELUP_TIMEOUT

    async def _apply_step_choice(self, ctx, campaign: dict, char: dict, session: dict, step: dict, raw_value: str):
        state = dict(session["state"])
        pending_level = int(state.get("pending_level", int(char.get("level", 1)) + 1))
        step_type = step["type"]

        if step_type == "hp_mode":
            choice = leveling.get_hp_gain_mode(raw_value)
            if choice not in {"fixed", "roll"}:
                raise ValueError("❌ Для HP выбери `fixed` или `roll`.")
            state["hp_mode_used"] = choice
            if choice == "roll":
                sides = int(game_data.find_class(char["class"])["base_hp"])
                state["rolled_hp"] = random.randint(1, sides)
            state["step_index"] = int(state.get("step_index", 0)) + 1
        elif step_type == "subclass":
            subclass = game_data.find_subclass(char["class"], raw_value)
            if not subclass:
                raise ValueError(
                    f"❌ Неизвестный подкласс. Доступны: {', '.join(game_data.get_subclass_label(char['class'], option) for option in step['options'])}"
                )
            state["subclass"] = subclass["key"]
            state["step_index"] = int(state.get("step_index", 0)) + 1
        elif step_type == "feat":
            parsed = leveling.parse_feat_choice(raw_value, pending_level)
            if not parsed:
                epic_text = "эпический дар" if step.get("epic_only") else "фит или `asi <характеристика> [характеристика]`"
                raise ValueError(f"❌ Не удалось распознать выбор. Укажи {epic_text}.")
            if step.get("epic_only") and parsed["type"] == "feat" and parsed["feat"].get("category") != "epic_boon":
                raise ValueError("❌ На 19 уровне здесь можно выбрать только Epic Boon.")
            state["feat_choice"] = parsed
            state["step_index"] = int(state.get("step_index", 0)) + 1
        elif step_type == "spell_notes":
            cleaned = raw_value.strip()
            if not cleaned:
                raise ValueError("❌ Укажи новые заклинания или кантрипы текстом.")
            state["spell_note"] = cleaned
            state["step_index"] = int(state.get("step_index", 0)) + 1
        else:
            raise ValueError(f"❌ Неподдерживаемый шаг левелапа: {step_type}")

        db.upsert_levelup_session(campaign["id"], str(ctx.author.id), char["name"], state)
        if state["step_index"] >= len(state.get("steps", [])):
            await self._finalize_level(ctx, campaign, char, db.get_active_levelup_session(campaign["id"]))

    async def _finalize_level(self, ctx, campaign: dict, char: dict, session: dict):
        state = session["state"]
        updates, log_lines = leveling.apply_level_gain(char, campaign, state)
        db.update_character(char["user_id"], campaign["id"], updates)
        queue_id = state.get("queue_id")
        if queue_id:
            db.complete_levelup_queue_entry(queue_id)
        db.delete_levelup_session(campaign["id"])

        updated_char = db.get_character(char["user_id"], campaign["id"])
        progress = leveling.get_character_progress(updated_char)
        await ctx.send(
            f"🎉 **{updated_char['name']}** достиг {updated_char['level']} уровня!\n" +
            ("\n".join(f"- {line}" for line in log_lines) if log_lines else "")
        )

        if progress["eligible_level"] > int(updated_char["level"]):
            queue_entry = db.enqueue_levelup(campaign["id"], updated_char["user_id"], updated_char["name"], progress["eligible_level"])
            db.activate_levelup_queue_entry(queue_entry["id"])
            next_state = self._build_session_state(updated_char, campaign, queue_entry)
            db.upsert_levelup_session(campaign["id"], updated_char["user_id"], updated_char["name"], next_state)
            await ctx.send(
                f"✨ У **{updated_char['name']}** хватает XP ещё на один уровень. Продолжаем мастер повышения."
            )
            await self._send_levelup_prompt(ctx, campaign, updated_char, db.get_active_levelup_session(campaign["id"]))

    async def _send_levelup_prompt(self, ctx, campaign: dict, char: dict, session: dict):
        char = await self._ensure_build(char, campaign)
        state = session["state"]
        steps = state.get("steps", [])
        step_index = int(state.get("step_index", 0))
        if step_index >= len(steps):
            await self._finalize_level(ctx, campaign, char, session)
            return
        step = steps[step_index]
        pending_level = int(state.get("pending_level", int(char.get("level", 1)) + 1))
        lines = leveling.build_levelup_embed_lines(char, campaign, state)

        embed = discord.Embed(title=f"⬆️ Повышение уровня: {char['name']}", description="\n".join(lines), color=0x4CAF50)
        if step["type"] == "hp_mode":
            embed.add_field(
                name="Выбор HP",
                value=(
                    "Кампания использует режим выбора прироста HP на каждом уровне.\n"
                    "Напиши `!выбрать fixed` для фиксированного прироста или `!выбрать roll` для броска Hit Die."
                ),
                inline=False,
            )
        elif step["type"] == "subclass":
            options = [game_data.get_subclass_label(char['class'], option) for option in step["options"]]
            embed.add_field(
                name=f"Подкласс на {pending_level} уровне",
                value="Доступны: " + ", ".join(options) + "\nИспользуй `!выбрать <название>`.",
                inline=False,
            )
        elif step["type"] == "feat":
            feats = leveling.get_available_feats(pending_level, epic_only=bool(step.get("epic_only")))
            feat_labels = ", ".join(feat["label"] for feat in feats)
            extra = "Используй `!выбрать asi сила сила` или `!выбрать asi сила мудрость` для ASI."
            embed.add_field(
                name="Выбор фита",
                value=f"Доступны: {feat_labels}\n{extra}",
                inline=False,
            )
        elif step["type"] == "spell_notes":
            embed.add_field(
                name="Обновление заклинаний",
                value=f"Нужно: {step['prompt']}.\nИспользуй `!выбрать <список через запятую>`.",
                inline=False,
            )
        embed.set_footer(text="Для выхода используй !отмена_левелапа")
        await ctx.send(embed=embed)

    async def _enqueue_if_ready(self, campaign: dict, chars: list[dict]) -> list[str]:
        notices = []
        for char in chars:
            progress = leveling.get_character_progress(char)
            if progress["eligible_level"] > int(char["level"]):
                queue_entry = db.enqueue_levelup(campaign["id"], char["user_id"], char["name"], progress["eligible_level"])
                notices.append(
                    f"⬆️ **{char['name']}** готов к повышению уровня и добавлен в очередь до уровня {queue_entry['target_level']}."
                )
        return notices

    def _queue_status_label(self, progress: dict, queue_entry: dict | None) -> str:
        if queue_entry:
            if queue_entry["status"] == "active":
                return "идёт мастер повышения"
            if queue_entry["status"] == "pending":
                return "в очереди на повышение"
        if progress["ready"]:
            return "готов к повышению"
        return "ещё набирает XP"


async def setup(bot):
    await bot.add_cog(LevelingCog(bot))
