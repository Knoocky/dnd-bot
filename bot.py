import argparse
import asyncio
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from logger_config import configure_logging

BASE_DIR = Path(__file__).resolve().parent

load_dotenv()
logger = configure_logging(BASE_DIR)

import ai_provider
import database as db


def parse_args():
    parser = argparse.ArgumentParser(
        description="Discord bot for D&D campaigns with selectable AI provider.",
    )
    parser.add_argument(
        "--provider",
        choices=("claude", "gpt"),
        help="AI provider to use for the dungeon master.",
    )
    return parser.parse_args()


args = parse_args()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("BOT_PREFIX", "!")

selected_provider = args.provider or os.getenv("AI_PROVIDER", ai_provider.DEFAULT_PROVIDER)
ai_provider.configure_provider(selected_provider)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(PREFIX),
    intents=intents,
    help_command=None,
)
bot.ai_provider = ai_provider.get_provider()
bot.ai_model = ai_provider.get_model_name()


@bot.event
async def on_ready():
    db.init_db()
    logger.info("Bot started: %s (ID: %s)", bot.user, bot.user.id)
    logger.info("Guilds: %s", len(bot.guilds))
    logger.info("AI provider: %s | model: %s", bot.ai_provider, bot.ai_model)
    await bot.change_presence(activity=discord.Game(name="D&D | !помощь"))


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ У тебя нет прав для этой команды.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Не хватает аргумента: `{error.param.name}`")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        logger.exception("Unhandled command error", exc_info=error)


async def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("Не найден DISCORD_TOKEN в .env")

    logger.info("Starting bot bootstrap")
    ai_provider.validate_configuration()

    async with bot:
        await bot.load_extension("cogs.character")
        await bot.load_extension("cogs.leveling")
        await bot.load_extension("cogs.game")
        await bot.load_extension("cogs.inventory")
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
