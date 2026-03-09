import discord
from discord.ext import commands

import database as db


class InventoryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="инвентарь", aliases=["сумка", "ин", "с"])
    async def show_inventory(self, ctx):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"])
        if not char:
            await ctx.send("❌ У тебя нет персонажа.")
            return

        embed = discord.Embed(
            title=f"🎒 Инвентарь — {char['name']}",
            color=0x8B6914,
        )
        if char["inventory"]:
            embed.description = "\n".join(f"• {item}" for item in char["inventory"])
        else:
            embed.description = "*Инвентарь пуст*"
        embed.add_field(name="💰 Золото", value=str(char["gold"]))
        await ctx.send(embed=embed)

    @commands.command(name="взять", aliases=["добавить_предмет", "вз"])
    async def add_item(self, ctx, *, item: str):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"])
        if not char:
            await ctx.send("❌ У тебя нет персонажа.")
            return

        inventory = char["inventory"]
        if len(inventory) >= 20:
            await ctx.send("⚠️ Инвентарь переполнен (макс. 20 предметов).")
            return

        item = item.strip()[:50]
        inventory.append(item)
        db.update_character(str(ctx.author.id), campaign["id"], {"inventory": inventory})
        await ctx.send(f"✅ **{char['name']}** подбирает: *{item}*")

    @commands.command(name="выбросить", aliases=["убрать", "вб"])
    async def drop_item(self, ctx, *, item: str):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"])
        if not char:
            await ctx.send("❌ У тебя нет персонажа.")
            return

        inventory = char["inventory"]
        found = next((entry for entry in inventory if entry.lower() == item.lower()), None)
        if not found:
            items_list = "\n".join(f"• {entry}" for entry in inventory) if inventory else "пусто"
            await ctx.send(f"❌ Предмет «{item}» не найден.\nТвой инвентарь:\n{items_list}")
            return

        inventory.remove(found)
        db.update_character(str(ctx.author.id), campaign["id"], {"inventory": inventory})
        await ctx.send(f"🗑️ **{char['name']}** выбрасывает: *{found}*")

    @commands.command(name="золото", aliases=["монеты", "зл"])
    async def manage_gold(self, ctx, action: str = "показать", amount: int = 0):
        campaign = db.get_active_campaign(str(ctx.channel.id))
        if not campaign:
            await ctx.send("❌ Нет активной кампании.")
            return

        char = db.get_character(str(ctx.author.id), campaign["id"])
        if not char:
            await ctx.send("❌ У тебя нет персонажа.")
            return

        if action == "показать":
            await ctx.send(f"💰 **{char['name']}** имеет **{char['gold']}** золотых монет.")
        elif action in ("дать", "+"):
            new_gold = char["gold"] + abs(amount)
            db.update_character(str(ctx.author.id), campaign["id"], {"gold": new_gold})
            await ctx.send(f"💰 +{abs(amount)} монет! У **{char['name']}** теперь **{new_gold}** золота.")
        elif action in ("взять", "-"):
            if char["gold"] < amount:
                await ctx.send(
                    f"❌ Недостаточно золота! У **{char['name']}** есть только **{char['gold']}** монет."
                )
            else:
                new_gold = char["gold"] - abs(amount)
                db.update_character(str(ctx.author.id), campaign["id"], {"gold": new_gold})
                await ctx.send(f"💸 -{abs(amount)} монет! У **{char['name']}** осталось **{new_gold}** золота.")
        else:
            await ctx.send("Использование: `!золото` / `!золото дать 50` / `!золото взять 20`")


async def setup(bot):
    await bot.add_cog(InventoryCog(bot))
