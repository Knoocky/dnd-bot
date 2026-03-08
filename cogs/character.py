import random

import discord
from discord.ext import commands

import database as db
import game_data

_pending = {}


def roll_stat() -> int:
    rolls = [random.randint(1, 6) for _ in range(4)]
    return sum(sorted(rolls)[1:])


def generate_stats() -> dict:
    return {stat["key"]: roll_stat() for stat in game_data.get_stats()}


class CharacterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="создать_персонажа", aliases=["create_char", "новый_перс", "сп", "cc", "нп"])
    async def create_character(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ В этом канале нет активной кампании. Используй `!новая_кампания`.")
            return

        existing = db.get_character(str(ctx.author.id), campaign["id"])
        if existing:
            await ctx.send(
                f"⚠️ У тебя уже есть персонаж **{existing['name']}** в этой кампании. "
                "Используй `!персонаж` или `!лп`, чтобы посмотреть лист."
            )
            return

        embed = discord.Embed(title="🧝 Создание персонажа - Шаг 1: Раса", color=0x7B68EE)
        for race in game_data.get_races():
            embed.add_field(name=race["label"], value=race["description"], inline=False)
        embed.set_footer(text="Введи: !раса <название> или !р <название>")
        await ctx.send(embed=embed)

    @commands.command(name="раса", aliases=["р"])
    async def choose_race(self, ctx, *, race: str):
        race_key = race.lower().strip()
        races = game_data.get_race_map()
        if race_key not in races:
            await ctx.send(f"❌ Неизвестная раса. Доступны: {', '.join(races.keys())}")
            return

        _pending[ctx.author.id] = {"race": race_key}

        embed = discord.Embed(title="⚔️ Создание персонажа - Шаг 2: Класс", color=0x7B68EE)
        for cls in game_data.get_classes():
            embed.add_field(name=cls["label"], value=cls["description"], inline=False)
        embed.set_footer(text="Введи: !класс <название> или !кл <название>")
        await ctx.send(embed=embed)

    @commands.command(name="класс", aliases=["кл", "к"])
    async def choose_class(self, ctx, *, cls: str):
        class_key = cls.lower().strip()
        classes = game_data.get_class_map()
        if class_key not in classes:
            await ctx.send(f"❌ Неизвестный класс. Доступны: {', '.join(classes.keys())}")
            return
        if ctx.author.id not in _pending:
            await ctx.send("❌ Сначала выбери расу: `!создать_персонажа` или `!сп`")
            return

        _pending[ctx.author.id]["class"] = class_key
        await ctx.send("✏️ Введи имя персонажа: `!имя <имя>` или `!им <имя>`")

    @commands.command(name="имя", aliases=["им", "и"])
    async def choose_name(self, ctx, *, name: str):
        if ctx.author.id not in _pending or "class" not in _pending[ctx.author.id]:
            await ctx.send("❌ Сначала выбери расу и класс.")
            return

        name = name.strip()[:30]
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        pending = _pending[ctx.author.id]
        race_key = pending["race"]
        class_key = pending["class"]
        race_data = game_data.get_race_map()[race_key]
        class_data = game_data.get_class_map()[class_key]

        stats = generate_stats()
        stats = game_data.apply_race_bonuses(stats, race_key)

        hp = class_data["base_hp"] + max(0, (stats["constitution"] - 10) // 2)
        char_data = {"name": name, "race": race_key, "class": class_key, "hp": hp, **stats}
        success = db.create_character(str(ctx.author.id), campaign["id"], char_data)

        if not success:
            await ctx.send("⚠️ Персонаж уже существует в этой кампании.")
            _pending.pop(ctx.author.id, None)
            return

        _pending.pop(ctx.author.id, None)
        stat_labels = game_data.get_stat_label_map()

        embed = discord.Embed(
            title=f"✅ {name} создан!",
            description=f"**{race_data['label']} {class_data['label']}**, уровень 1",
            color=0x00FF99,
        )
        embed.add_field(name="❤️ HP", value=f"{hp}/{hp}", inline=True)
        embed.add_field(name="💰 Золото", value="10", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        stat_lines = "\n".join(
            f"**{stat_labels[stat['key']]}**: {stats[stat['key']]}" for stat in game_data.get_stats()
        )
        embed.add_field(name="📊 Характеристики", value=stat_lines, inline=False)
        embed.set_footer(text="Используй !персонаж или !лп, чтобы посмотреть лист персонажа")
        await ctx.send(embed=embed)

    @commands.command(name="персонаж", aliases=["char", "перс", "лп", "пс"])
    async def show_character(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"])
        if not char:
            await ctx.send("❌ У тебя нет персонажа. Используй `!создать_персонажа` или `!сп`.")
            return

        inventory = ", ".join(char["inventory"]) if char["inventory"] else "пусто"
        hp_bar = _hp_bar(char["hp"], char["max_hp"])

        embed = discord.Embed(
            title=f"📜 {char['name']}",
            description=(
                f"*{game_data.get_race_label(char['race'])} {game_data.get_class_label(char['class'])}, "
                f"уровень {char['level']}*"
            ),
            color=0xFFD700,
        )
        embed.add_field(name=f"❤️ HP {hp_bar}", value=f"{char['hp']}/{char['max_hp']}", inline=True)
        embed.add_field(name="💰 Золото", value=str(char["gold"]), inline=True)
        embed.add_field(name="⭐ Опыт", value=str(char["exp"]), inline=True)
        stats_text = "\n".join(
            f"{_stat_emoji(stat['key'])} {game_data.get_stat_label(stat['key'])}: **{char[stat['key']]}**"
            for stat in game_data.get_stats()
        )
        embed.add_field(name="📊 Характеристики", value=stats_text, inline=False)
        embed.add_field(name="🎒 Инвентарь", value=inventory, inline=False)
        await ctx.send(embed=embed)


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
