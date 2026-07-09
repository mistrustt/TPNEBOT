import asyncio
from enum import member
import os
import random
from datetime import timedelta
import aiohttp
import discord
import logging
from discord.ext import commands
from discord.ext.commands import Context
from utils.misc import MiscUtils

logger = logging.getLogger("discord.client")


class Fun(commands.Cog, name="Fun"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.utils = MiscUtils(self)
        self.ban_roulette_history = {}

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Cog {self.__class__.__name__} is ready!")

    @commands.command(
        name="randomfact", aliases=["rfact"], description="Get a random fact."
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def randomfact(self, ctx: Context) -> None:
        """
        Get a random fact from API Ninjas.
        """
        self.api_key = os.getenv("API_NINJAS_KEY")
        if not self.api_key:
            embed = discord.Embed(
                title="Error!",
                description="API key for API Ninjas is not set.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return
        url = "https://api.api-ninjas.com/v1/facts"
        headers = {"X-Api-Key": self.api_key}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as request:
                if request.status == 200:
                    data = await request.json()

                    fact_text = (
                        data[0].get("fact") if isinstance(data, list) and data else None
                    )

                    if fact_text:
                        color = (
                            discord.Color.blurple()
                            if isinstance(ctx.channel, discord.DMChannel)
                            else (ctx.author.top_role.color or discord.Color.blurple())
                        )
                        embed = discord.Embed(
                            title="Random Fact",
                            description=fact_text,
                            color=color,
                            timestamp=discord.utils.utcnow(),
                        )
                    else:
                        embed = discord.Embed(
                            title="Oops!",
                            description="Couldn't parse a fact from the API.",
                            color=discord.Color.red(),
                        )
                else:
                    embed = discord.Embed(
                        title="Error!",
                        description="There was a problem contacting the API. Please try again later.",
                        color=discord.Color.red(),
                    )
                    

                await ctx.send(embed=embed)

    @commands.command(
        name="fotd",
        aliases=["factoftheday"],
        description="Get a random fact of the day.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def fotd(self, ctx: Context) -> None:
        """Get a random fact of the day."""
        url = "https://uselessfacts.jsph.pl/api/v2/facts/today?language=en"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    embed = discord.Embed(
                        title="Error!",
                        description="There was a problem contacting the Useless Facts API. Please try again later.",
                        color=discord.Color.red(),
                    )
                    
                    return await ctx.reply(embed=embed)

                data = await response.json()

                if isinstance(data, dict):
                    item = data
                elif isinstance(data, list) and data:
                    item = data[0]
                else:
                    item = None

                if not item or not item.get("text"):
                    embed = discord.Embed(
                        title="Oops!",
                        description="Couldn't parse a fact of the day from the API response.",
                        color=discord.Color.red(),
                    )
                    return await ctx.reply(embed=embed)

                fact_text = item["text"]
                source = item.get("source") or item.get("source_url") or "unknown"

                color = (
                    discord.Color.blurple()
                    if isinstance(ctx.channel, discord.DMChannel)
                    else (ctx.author.top_role.color or discord.Color.blurple())
                )

                embed = discord.Embed(
                    title=f"Fact of the Day – {discord.utils.utcnow().strftime('%Y-%m-%d')}",
                    description=fact_text,
                    color=color,
                )
                embed.set_footer(text=f"Source: {source}")

                await ctx.reply(embed=embed)

    @commands.command(name="dog")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def dog(self, ctx: commands.Context):
        """Get a random dog photo."""
        url = "https://random.dog/woof.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await ctx.reply(data["url"])
                else:
                    embed = discord.Embed(
                        title="Error!",
                        description="There was a problem contacting the Dog API. Please try again later.",
                        color=discord.Color.red(),
                    )
                    
                    await ctx.reply(embed=embed)

    @commands.command(name="cat")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def cat(self, ctx: commands.Context):
        """Get a random cat photo."""
        url = "https://api.thecatapi.com/v1/images/search"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await ctx.reply(data[0]["url"])
                else:
                    embed = discord.Embed(
                        title="Error!",
                        description="There was a problem contacting the Cat API. Please try again later.",
                        color=discord.Color.red(),
                    )
                    
                    await ctx.reply(embed=embed)

    @commands.command(name="fox")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def fox(self, ctx: commands.Context):
        """Get a random fox photo."""
        url = "https://randomfox.ca/floof/"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await ctx.reply(data["image"])
                else:
                    embed = discord.Embed(
                        title="Error!",
                        description="There was a problem contacting the Fox API. Please try again later.",
                        color=discord.Color.red(),
                    )
                    
                    await ctx.reply(embed=embed)

    @commands.command(name="penguin")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def penguin(self, ctx: commands.Context):
        """Get a random penguin photo."""
        url = "https://penguin.sjsharivker.workers.dev/api"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await ctx.reply(data["img"])
                if resp.status == 201:
                    data = await resp.json()
                    await ctx.reply(data["img"])
                else:
                    embed = discord.Embed(
                        title="Error!",
                        description="There was a problem contacting the Penguin API. Please try again later.",
                        color=discord.Color.red(),
                    )
                    
                    await ctx.reply(embed=embed)

    @commands.command(name="duck", hidden=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def duck(self, ctx: commands.Context):
        """Get a random duck photo."""
        url = "https://random-d.uk/api/v2/random"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await ctx.reply(data["url"])
                else:
                    embed = discord.Embed(
                        title="Error!",
                        description="There was a problem contacting the Duck API. Please try again later.",
                        color=discord.Color.red(),
                    )
                    
                    await ctx.reply(embed=embed)

    @commands.command(name="boom", aliases=["kaboom"], description="boom.")
    async def pow(self, ctx: Context):
        await ctx.defer()
        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 5
        )
        await ctx.reply("pow :boom:")

    @commands.command(
        name="gay", aliases=["gayrate"], help="Estimates how homosexual a user is"
    )
    async def random_percentage(self, ctx: Context, member: discord.Member = None):
        """Estimates how homosexual a user is"""
        await ctx.defer()
        percentage = random.randrange(100)
        color = discord.Color.blurple()
        if isinstance(ctx.channel, discord.DMChannel):
            color = discord.Color.blurple()
        else:
            color = (
                ctx.author.top_role.color
                if ctx.author.top_role
                else discord.Color.blurple()
            )
        if member is None:
            await self.bot.database.set_cooldown(
                ctx.author.id, ctx.command.qualified_name, 5
            )
            if percentage > 50:
                embed = discord.Embed(
                    description=f"are {percentage}% gay :rainbow:", color=color
                )
                embed.set_author(
                    name=f"You", icon_url=self.utils.get_avatar_url(ctx.author)
                )
                await ctx.reply(embed=embed)
            else:
                embed = discord.Embed(description=f"are {percentage}% gay", color=color)
                embed.set_author(
                    name=f"You", icon_url=self.utils.get_avatar_url(ctx.author)
                )
                await ctx.reply(embed=embed)
        elif member == ctx.author:
            await self.bot.database.set_cooldown(
                ctx.author.id, ctx.command.qualified_name, 5
            )
            if percentage > 50:
                embed = discord.Embed(
                    description=f"are {percentage}% gay :rainbow:", color=color
                )
                embed.set_author(
                    name=f"You", icon_url=self.utils.get_avatar_url(ctx.author)
                )
                await ctx.reply(embed=embed)
            else:
                embed = discord.Embed(description=f"are {percentage}% gay", color=color)
                embed.set_author(
                    name=f"You", icon_url=self.utils.get_avatar_url(ctx.author)
                )
                await ctx.reply(embed=embed)
        elif member != ctx.author:
            await self.bot.database.set_cooldown(
                ctx.author.id, ctx.command.qualified_name, 5
            )
            if percentage > 50:
                embed = discord.Embed(
                    description=f"is {percentage}% gay :rainbow:", color=color
                )
                embed.set_author(
                    name=f"{member.display_name}",
                    icon_url=self.utils.get_avatar_url(member),
                )
                await ctx.reply(embed=embed)
            else:
                embed = discord.Embed(description=f"is {percentage}% gay", color=color)
                embed.set_author(
                    name=f"{member.display_name}",
                    icon_url=self.utils.get_avatar_url(member),
                )
                await ctx.reply(embed=embed)

    @commands.command(
        name="penis", aliases=["dih"], help="Find out how large your penis is."
    )
    async def size(self, ctx: Context, member: discord.Member = None):
        member = member or ctx.author

        size = random.randrange(start=0, stop=21)
        if size == 0:
            penis = "No penis detected"
        else:
            penis = f"8{'=' * size}D"
        embed = discord.Embed(description=f"{penis}")
        embed.set_author(
            name=f"{member.display_name}'s Penis Size",
            icon_url=self.utils.get_avatar_url(member),
        )
        await ctx.reply(embed=embed)

    @commands.command(name="weight", help="Find out how much you weigh")
    async def weight(self, ctx: Context, member: discord.Member = None):
        """Find out how much you weigh"""
        member = member or ctx.author

        units = ["kg", "lb", "tons"]
        selected_unit = random.choice(units)

        if selected_unit == "kg":
            if random.random() < 0.85:
                weight = random.randint(50, 150)
            else:
                weight = random.randint(151, 1000)
        elif selected_unit == "lb":
            if random.random() < 0.85:
                weight = random.randint(110, 330)
            else:
                weight = random.randint(331, 2200)
        else:
            if random.random() < 0.85:
                weight = round(random.uniform(0.1, 0.5), 2)
            else:
                weight = round(random.uniform(0.51, 50), 2)

        color = discord.Color.blurple()
        if not isinstance(ctx.channel, discord.DMChannel) and ctx.author.top_role:
            color = ctx.author.top_role.color

        embed = discord.Embed(
            description=f"weighs {weight} {selected_unit}", color=color
        )
        embed.set_author(
            name=member.display_name, icon_url=self.utils.get_avatar_url(member)
        )
        await ctx.reply(embed=embed)

    @commands.command(name="height", help="Find out how tall you are")
    async def height(self, ctx: Context, member: discord.Member = None):
        """Find out how tall you are (for comedic purposes)."""
        member = member or ctx.author

        if random.random() < 0.85:
            feet = random.randint(3, 7)
            inches = random.randint(0, 11)
        else:
            if random.random() < 0.5:
                feet = random.randint(8, 17)
                inches = random.randint(0, 11)
            else:
                feet = random.randint(0, 2)
                inches = random.randint(0, 11)

        height_str = f"{feet} ft {inches} in"

        color = discord.Color.blurple()
        if not isinstance(ctx.channel, discord.DMChannel) and ctx.author.top_role:
            color = ctx.author.top_role.color

        embed = discord.Embed(description=f"has a height of {height_str}", color=color)
        embed.set_author(
            name=member.display_name, icon_url=self.utils.get_avatar_url(member)
        )
        await ctx.reply(embed=embed)

    @commands.command(name="iq", help="Find out your IQ")
    async def iq(self, ctx: Context, member: discord.Member = None):
        """Find out your IQ."""
        member = member or ctx.author

        if random.random() < 0.85:
            iq = random.randint(90, 130)
        else:
            if random.random() < 0.5:
                iq = random.randint(131, 200)
            else:
                iq = random.randint(1, 89)

        color = discord.Color.blurple()
        if not isinstance(ctx.channel, discord.DMChannel) and ctx.author.top_role:
            color = ctx.author.top_role.color

        embed = discord.Embed(description=f"has an IQ of {iq}", color=color)
        embed.set_author(
            name=member.display_name, icon_url=self.utils.get_avatar_url(member)
        )
        await ctx.reply(embed=embed)

    @commands.command(
        name="banroulette", aliases=["br"], help="Play a game of banroulette"
    )
    @commands.has_guild_permissions(ban_members=True)
    async def banroulette(self, ctx: Context):
        """Play a game of banroulette. Players react to join the game and one player is randomly selected to be 'banned'."""
        embed = discord.Embed(
            title="Banroulette",
            description="React with 🔫 to join the game! You have 15 seconds.",
            color=discord.Color.blurple(),
        )
        message = await ctx.reply(embed=embed)
        await message.add_reaction("🔫")

        await asyncio.sleep(15)
        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 30
        )

        message = await ctx.fetch_message(message.id)
        users = set()
        for reaction in message.reactions:
            if str(reaction.emoji) == "🔫":
                async for user in reaction.users():
                    if not user.bot:
                        users.add(user)

        if len(users) < 2:
            embed = discord.Embed(
                title="Ban Roulette",
                description="Not enough players joined the game. Need at least 2 players.",
                color=discord.Color.red(),
            )
            await message.edit(embed=embed)
            return

        banned_user = random.choice(list(users))

        victim = await ctx.guild.fetch_member(banned_user.id)
        if not victim:
            embed = discord.Embed(
                title="Ban Roulette",
                description="Could not find the selected user in the guild.",
                color=discord.Color.red(),
            )
            await message.edit(embed=embed)
            return

        try:
            await victim.ban(reason="Lost Ban Roulette", delete_message_days=0)
            self.ban_roulette_history[ctx.author.id] = victim.id
            embed = discord.Embed(
                title="Ban Roulette",
                description=f"{victim.mention} has been banned! :hammer:",
                color=discord.Color.green(),
            )
        except discord.Forbidden:
            embed = discord.Embed(
                title="Ban Roulette",
                description=f"{victim.mention} would have been banned, but I don't have permission! :hammer:",
                color=discord.Color.red(),
            )
        except Exception as e:
            embed = discord.Embed(
                title="Ban Roulette",
                description=f"Failed to ban {victim.mention}: {str(e)}",
                color=discord.Color.red(),
            )

        await ctx.send(embed=embed)

    @commands.command(
        name="timeoutroulette", aliases=["tr"], help="Play a game of timeout roulette"
    )
    @commands.has_guild_permissions(moderate_members=True)
    async def timeoutroulette(self, ctx: Context):
        """Play a game of timeout roulette. Players react to join the game and one player is randomly selected to be timed out."""
        embed = discord.Embed(
            title="Timeout Roulette",
            description="React with ⏰ to join the game! You have 15 seconds.",
            color=discord.Color.blurple(),
        )
        message = await ctx.reply(embed=embed)
        await message.add_reaction("⏰")

        await asyncio.sleep(15)
        await self.bot.database.set_cooldown(
            ctx.author.id, ctx.command.qualified_name, 30
        )

        message = await ctx.fetch_message(message.id)
        users = set()
        for reaction in message.reactions:
            if str(reaction.emoji) == "⏰":
                async for user in reaction.users():
                    if not user.bot:
                        users.add(user)

        if len(users) < 2:
            embed = discord.Embed(
                title="Timeout Roulette",
                description="Not enough players joined the game. Need at least 2 players.",
                color=discord.Color.red(),
            )
            await message.edit(embed=embed)
            return

        timeout_user = random.choice(list(users))

        victim = await ctx.guild.fetch_member(timeout_user.id)
        if not victim:
            embed = discord.Embed(
                title="Timeout Roulette",
                description="Could not find the selected user in the guild.",
                color=discord.Color.red(),
            )
            await message.edit(embed=embed)
            return

        timeout_duration = random.randint(60, 600)  # 1-10 minutes

        try:
            await victim.timeout(
                discord.utils.utcnow() + timedelta(seconds=timeout_duration),
                reason="Lost Timeout Roulette",
            )
            embed = discord.Embed(
                title="Timeout Roulette",
                description=f"{victim.mention} has been timed out for {timeout_duration // 60} minutes and {timeout_duration % 60} seconds! ⏰",
                color=discord.Color.orange(),
            )
        except discord.Forbidden:
            embed = discord.Embed(
                title="Timeout Roulette",
                description=f"{victim.mention} would have been timed out, but I don't have permission! ⏰",
                color=discord.Color.red(),
            )
        except Exception as e:
            embed = discord.Embed(
                title="Timeout Roulette",
                description=f"Failed to timeout {victim.mention}: {str(e)}",
                color=discord.Color.red(),
            )

        await ctx.send(embed=embed)

async def setup(bot) -> None:
    await bot.add_cog(Fun(bot))
    logger.debug("Fun cog initialized successfully")
